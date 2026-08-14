#!/usr/bin/env python3
"""Build PHP 7.0 – 8.0 from source for macOS and Linux, and pack it as a relocatable artifact.

This is the half of the PHP range `static-php-cli`_ cannot reach. It builds 8.1 upwards and nothing
older, which is why ``php_unix.py`` floors at 8.1 — and why the version policy, which offers PHP from
7.0 on purpose, was only being kept on Windows, where the official archive reaches back that far for
free. Everything below 8.1 on these two systems has to be compiled, and this compiles it.

The cost that usually makes "we build" the wrong answer does not apply here. 7.0.33, 7.1.33, 7.2.34,
7.3.33, 7.4.33 and 8.0.30 are final: those branches will never have another release. A pipeline for
them runs a handful of times and is then finished, rather than being maintained for every security
release for as long as the version is offered.

Four decisions here are load-bearing.

*Build on an old distribution, on purpose.* The Linux legs run inside AlmaLinux 8 (``manylinux_2_28``),
not on the runner. Not primarily for the glibc floor — although 2.28 instead of 2.35 is what it
happens to give — but because that image carries OpenSSL 1.1.1, ICU 60 and autoconf 2.69, which is
the toolchain PHP 7 was written against. On a current distribution all three are wrong at once: ICU
68 removed the ``TRUE``/``FALSE`` macros ``ext/intl`` still uses, autoconf 2.70 broke ``phpize`` for
these branches, and PHP 7 predates OpenSSL 3 entirely. Building somewhere old costs a line of YAML;
building somewhere new costs three separate patch sets.

*Never ``buildconf``.* The release tarballs ship a generated ``configure``, and regenerating it on
these branches needs an autoconf from the same era. The consequence is not cosmetic: an extension can
only be compiled *into* PHP by adding it to ``ext/`` and re-running ``buildconf``, so **the PECL
extensions are built shared here**, with ``phpize``, and shipped in ``ext/``. On 8.1+ ``redis`` and
``mongodb`` are compiled in; here they are loadable modules. The daemon already describes both shapes
— ``extensions.static`` against ``extensions.shared`` — and the loadable shape is the one T28's
enable/disable model wants anyway.

*Bundle what was linked.* A build against a distribution's packages is bound to that distribution.
Everything outside the C runtime is copied into ``lib/`` and every reference rewritten to point
there; ``relocate.py`` does that and proves it afterwards from a directory the tree has never seen.

*Install to a prefix that does not exist on anyone's machine.* PHP bakes its prefix into the binary —
``php-config``, the default ``php.ini`` path, the default ``extension_dir``. Installing to the build's
temporary directory would bake in a path that is merely absent; installing to ``/opt/mixengine/php-…``
bakes in one that is absent *and* deliberate, so a leak fails loudly rather than picking up a
stranger's configuration file that happens to sit where the build machine's did. It is also what
makes ``phpize`` work: an extension is configured through ``php-config``, which answers with the
prefix, so the prefix has to be somewhere that really exists on the build machine.

The per-branch configure table below is archaeology, and so is a good deal of what is *not* in it.
``docs/building-from-source.md`` records the failures behind both — including the four rounds spent
on extensions that were never loaded because ``HAVE_LIBDL`` was missing, which is the reason
``-ldl`` appears in ``LDFLAGS`` for no visible reason.

.. _static-php-cli: https://github.com/crazywhalecc/static-php-cli
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

import relocate

RELEASES = "https://www.php.net/releases/index.php?json&version={branch}"
DISTRIBUTIONS = "https://www.php.net/distributions/{filename}"
# php.net keeps every release it ever made, but moves the old ones out of `distributions/`.
MUSEUM = "https://museum.php.net/php{major}/{filename}"

# The newest branch this recipe is for. Anything at or above it is static-php-cli's, and running
# both recipes over one version would publish two different artifacts for the same cell.
CEILING = (8, 1)

# Built shared with `phpize`, in this order — `redis` links against `igbinary` when it is already
# installed, and without it stores a serialisation nothing else can read.
#
# `redis` and `mongodb` are the two MixEngine was told it must carry across the whole version range.
# `xdebug` is here for the same reason it is shared on 8.1+: a debugger that could never be turned
# off is not a debugger anybody wants.
PECL = ["igbinary", "redis", "mongodb", "xdebug"]

# Libraries this recipe compiles rather than takes from the system, keyed by the name the configure
# table below knows them as. `provides` is the key each one answers for there.
#
# Two different reasons appear in this table. On Linux `oniguruma`, `libzip` and `libwebp` are built
# only when the image turns out not to package them, because they move between EPEL, CRB and nowhere.
# On macOS the rest are built **always**, because there is no old distribution to build inside and
# these are precisely the libraries whose APIs moved under PHP 7's feet:
#
#   openssl   PHP gained OpenSSL 3 support in 8.1. Everything older uses RSA_SSLV23_PADDING, which
#             OpenSSL 3 removed, so it does not compile against the version Homebrew has.
#   libxml2   2.12 made the error struct const in callback signatures; PHP 7 predates it and fails
#             with "incompatible function pointer types" in ext/libxml.
#   libxslt   only because it has to match the libxml2 above — two libxml2 in one process is not a
#             thing that can be shipped.
#
#   icu       the largest one, and the clearest illustration of why the Linux legs build inside an
#             old distribution. AlmaLinux 8 has ICU 60 and every branch here compiles against it
#             untouched; macOS has whatever Homebrew installed this month, currently 78, and
#             ext/intl before 7.4 does not compile against it for two independent reasons. ICU 61
#             stopped putting its classes into the global namespace, so 7.0 fails with "unknown type
#             name 'UnicodeString'; did you mean 'icu_78::UnicodeString'?". ICU 70 changed the
#             virtuals in `CharacterIterator` from returning `UBool` to returning `bool`, so 7.3
#             fails overriding `operator==` with the wrong return type. The second has no macro and
#             no workaround: 7.4.33 carries `#if U_ICU_VERSION_MAJOR_NUM >= 70` around that
#             declaration, and 7.3.33 was released before ICU 70 existed, so it never could.
#
#             67.1 is therefore the pin: the last ICU that still defines TRUE/FALSE natively, three
#             releases before the `bool` change, and new enough that its autotools handle a modern
#             macOS — which 60.3 did not, having a 2017 `config.sub` that does not know Apple
#             Silicon and a Darwin makefile that emits `-install_namelibicudata.60.dylib` as one
#             argument. It still needs `U_USING_ICU_NAMESPACE=1`, because 67 is past ICU 61.
SOURCE_LIBRARIES = {
    "icu": {
        "url": "https://github.com/unicode-org/icu/releases/download/release-67-1/icu4c-67_1-src.tgz",
        "build": "autotools", "subdirectory": "source", "pkgconfig": "icu-uc", "provides": "icu",
        # `--build` and `--host` are named, and named identically so this stays a native build:
        # left to itself, ICU's `config.guess` reports `arm64-apple-darwin`, which a `config.sub`
        # of this vintage rejects outright. `aarch64` is the spelling it has understood for years.
        "arguments": ["--build={triple}", "--host={triple}", "--disable-samples",
                      "--disable-tests", "--disable-extras", "--disable-layoutex"],
    },
    "oniguruma": {
        "url": "https://github.com/kkos/oniguruma/releases/download/v6.9.9/onig-6.9.9.tar.gz",
        "build": "autotools", "pkgconfig": "oniguruma", "provides": "oniguruma",
    },
    "libzip": {
        "url": "https://libzip.org/download/libzip-1.10.1.tar.gz",
        "build": "cmake", "pkgconfig": "libzip", "provides": "libzip",
    },
    "libwebp": {
        "url": "https://storage.googleapis.com/downloads.webmproject.org/releases/webp/libwebp-1.3.2.tar.gz",
        "build": "autotools", "pkgconfig": "libwebp", "provides": "webp",
    },
    "openssl": {
        "url": "https://github.com/openssl/openssl/releases/download/OpenSSL_1_1_1w/openssl-1.1.1w.tar.gz",
        "build": "openssl", "pkgconfig": "openssl", "provides": "openssl",
    },
    "libxml2": {
        "url": "https://download.gnome.org/sources/libxml2/2.9/libxml2-2.9.14.tar.xz",
        "build": "autotools", "pkgconfig": "libxml-2.0", "provides": "libxml2",
        "arguments": ["--without-python", "--without-lzma"],
    },
    "libxslt": {
        "url": "https://download.gnome.org/sources/libxslt/1.1/libxslt-1.1.35.tar.xz",
        "build": "autotools", "pkgconfig": "libxslt", "provides": "libxslt",
        "arguments": ["--without-python", "--with-libxml-prefix={prefix}"],
    },
}

# What macOS compiles every time, in dependency order — libxslt links the libxml2 above it.
MACOS_SOURCE_LIBRARIES = ["openssl", "libxml2", "libxslt"]

# Everything AlmaLinux 8 does have and PHP 7 needs. Deliberately a named list rather than a `dnf
# group`: it is a list a reader can check against the configure flags below.
DNF_PACKAGES = [
    "gcc", "gcc-c++", "make", "cmake", "autoconf", "automake", "libtool", "pkgconfig", "patch",
    "xz", "patchelf", "binutils",
    "libxml2-devel", "openssl-devel", "bzip2-devel", "libcurl-devel", "libpng-devel",
    "libjpeg-turbo-devel", "freetype-devel", "gmp-devel", "libicu-devel", "libsodium-devel",
    "postgresql-devel", "sqlite-devel", "readline-devel", "libxslt-devel", "zlib-devel",
    "libedit-devel", "oniguruma-devel", "libzip-devel", "libwebp-devel",
]

# Homebrew's names for the same set. Keg-only formulae are the norm here, so nothing is assumed to be
# on the compiler's default search path — every one is asked for its own prefix below.
# openssl, libxml2 and libxslt are deliberately absent: those three are compiled here instead, for
# the reasons in SOURCE_LIBRARIES. Homebrew still installs openssl@3 as somebody else's dependency
# (libpq's), which is harmless — it is simply never what PHP is pointed at.
BREW_PACKAGES = [
    # `phpize` regenerates a configure script, so building any PECL extension needs autoconf — and a
    # macOS runner does not have one. On the branches before 7.4 the 2.69 built below shadows this
    # one, because those cannot be phpize'd by a current autoconf at all.
    "autoconf", "automake", "libtool", "pkg-config",
    "icu4c", "libzip", "oniguruma", "libsodium", "libpq", "gmp", "jpeg-turbo", "libpng",
    "freetype", "webp", "sqlite", "libiconv", "bzip2",
    "readline",   # macOS ships libedit, not readline, and PHP's `--with-readline` wants the latter
]

# The Homebrew formula behind each name the configure table uses. `icu4c` is versioned now
# (`icu4c@78`) and the number moves, which is why the lookup below tries the versioned spellings
# rather than trusting any one of them.
BREW_FORMULAE = {
    "icu": "icu4c", "libzip": "libzip", "oniguruma": "oniguruma", "libsodium": "libsodium",
    "libpq": "libpq", "gmp": "gmp", "jpeg": "jpeg-turbo", "libpng": "libpng",
    "freetype": "freetype", "webp": "webp", "sqlite": "sqlite", "libiconv": "libiconv",
    "bzip2": "bzip2", "readline": "readline",
}


def run(*command: str, cwd: Path | None = None, env: dict | None = None,
        capture: bool = True, timeout: int = 7200) -> str:
    """Run a command, loudly. Output is streamed when it is a build and captured when it is data."""
    print("$ " + " ".join(command), flush=True)
    result = subprocess.run(
        command, cwd=cwd, env=env, text=True, timeout=timeout,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )
    if result.returncode != 0:
        if capture:
            sys.stdout.write(result.stdout or "")
            sys.stderr.write(result.stderr or "")
        raise SystemExit(f"{command[0]} exited {result.returncode}")
    return result.stdout or ""


def attempt(*command: str, timeout: int = 1800) -> bool:
    """Run something whose failure is an answer rather than an error."""
    print("$ " + " ".join(command), flush=True)
    return subprocess.run(command, capture_output=True, timeout=timeout).returncode == 0


_sdk: list[Path | None] = []


def macos_sdk() -> Path | None:
    """Where macOS keeps the headers and stub libraries that used to be in ``/usr``.

    Since Xcode 10 there is no ``/usr/include`` on a Mac at all, which matters here because build
    systems written before that assume there is. Asked once and remembered: ``xcrun`` is not free
    and the answer cannot change mid-build.
    """
    if sys.platform != "darwin":
        return None
    if not _sdk:
        answer = subprocess.run(
            ["xcrun", "--show-sdk-path"], capture_output=True, text=True, timeout=120
        ).stdout.strip()
        _sdk.append(Path(answer) if answer else None)
    return _sdk[0]


def fetch(url: str, timeout: int = 300) -> bytes:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return response.read()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def parts(version: str) -> tuple[int, ...]:
    return tuple(int(piece) for piece in re.findall(r"\d+", version))


def host() -> tuple[str, str]:
    machine = {"x86_64": "x86_64", "amd64": "x86_64", "arm64": "aarch64", "aarch64": "aarch64"}
    arch = machine.get(platform.machine().lower())
    if arch is None:
        raise SystemExit(f"unsupported machine {platform.machine()}")
    if sys.platform == "darwin":
        # Both architectures are built, each on a runner of its own. Nothing here is cross-compiled
        # and nothing runs under Rosetta: an x86_64 build filed under aarch64 would be an artifact
        # that installs on Apple Silicon and then emulates, which is the thing a native runtime
        # manager exists to avoid. A branch that will not compile natively is a cell the index does
        # without, and says so.
        return "macos", arch
    if sys.platform.startswith("linux"):
        return "linux", arch
    raise SystemExit("this recipe is for macOS and Linux; Windows borrows instead of building")


# ------------------------------------------------------------------------------- getting php ---


def release(branch: str) -> tuple[str, str, str]:
    """``(version, filename, sha256)`` for the last release of *branch*.

    An EOL branch has no "newest" in the sense a supported one does — it has a last — but php.net's
    release endpoint answers for both, and carries the hash. That is the part worth having: it means
    the tarball is checked against what php.net published rather than against itself.
    """
    data = json.loads(fetch(RELEASES.format(branch=branch)))
    version = data.get("version")
    if not version:
        raise SystemExit(f"php.net has no release for branch {branch}")
    for source in data.get("source", []):
        if source.get("filename", "").endswith(".tar.xz"):
            return version, source["filename"], source.get("sha256", "")
    raise SystemExit(f"php {version} has no .tar.xz on php.net")


def source_tree(work: Path, branch: str) -> tuple[Path, str]:
    version, filename, expected = release(branch)
    tarball = work / filename
    for url in (DISTRIBUTIONS.format(filename=filename),
                MUSEUM.format(major=version.split(".")[0], filename=filename)):
        try:
            print(f"fetching {url}")
            tarball.write_bytes(fetch(url))
            break
        except urllib.error.HTTPError as error:
            print(f"  {error.code} {error.reason}", file=sys.stderr)
    else:
        raise SystemExit(f"could not download {filename} from php.net or its museum")

    if expected and sha256(tarball) != expected:
        raise SystemExit(
            f"{filename} does not match the sha256 php.net publishes for it. Either the download is "
            "damaged or it is not the file php.net released."
        )
    with tarfile.open(tarball) as archive:
        archive.extractall(work)
    unpacked = work / f"php-{version}"
    if not (unpacked / "configure").is_file():
        raise SystemExit(f"{unpacked} has no generated configure; this is not a release tarball")
    return unpacked, version


# ------------------------------------------------------------------------------ dependencies ---


def brew_prefix(formula: str) -> Path | None:
    """Homebrew's directory for *formula*, trying the versioned spellings it renames things into."""
    candidates = [formula]
    if "@" not in formula:
        listed = subprocess.run(
            ["brew", "list", "--formula"], capture_output=True, text=True, timeout=300
        ).stdout.split()
        candidates += sorted(
            (name for name in listed if name.startswith(f"{formula}@")), reverse=True
        )
    for candidate in candidates:
        result = subprocess.run(
            ["brew", "--prefix", candidate], capture_output=True, text=True, timeout=120
        )
        prefix = Path(result.stdout.strip()) if result.stdout.strip() else None
        if result.returncode == 0 and prefix and prefix.is_dir():
            return prefix
    return None


def have_library(name: str) -> bool:
    try:
        return subprocess.run(
            ["pkg-config", "--exists", name], capture_output=True, timeout=60
        ).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def build_library(work: Path, prefix: Path, name: str) -> None:
    """Compile one of the libraries this recipe pins or the distribution does not carry."""
    recipe = SOURCE_LIBRARIES[name]
    directory = work / f"lib-{name}"
    directory.mkdir(parents=True, exist_ok=True)
    tarball = directory / recipe["url"].rsplit("/", 1)[-1]
    tarball.write_bytes(fetch(recipe["url"]))
    with tarfile.open(tarball) as archive:
        archive.extractall(directory)
    unpacked = next(path for path in sorted(directory.iterdir()) if path.is_dir())
    if "subdirectory" in recipe:
        unpacked = unpacked / recipe["subdirectory"]

    jobs = str(os.cpu_count() or 2)
    # `arm64` is what the kernel calls it and `aarch64` is what config.sub has understood since long
    # before Apple Silicon existed. Old autotools know only the second spelling, so that is the one
    # handed to anything old enough to need telling.
    machine = {"arm64": "aarch64"}.get(platform.machine(), platform.machine())
    triple = f"{machine}-apple-darwin" if sys.platform == "darwin" \
        else f"{machine}-pc-linux-gnu"
    arguments = [argument.format(prefix=prefix, triple=triple)
                 for argument in recipe.get("arguments", [])]
    environment = {**os.environ, **recipe.get("environment", {})}

    if recipe["build"] == "cmake":
        build = unpacked / "build"
        run("cmake", "-S", str(unpacked), "-B", str(build), f"-DCMAKE_INSTALL_PREFIX={prefix}",
            "-DCMAKE_INSTALL_LIBDIR=lib", "-DBUILD_SHARED_LIBS=ON", "-DBUILD_TOOLS=OFF",
            "-DBUILD_EXAMPLES=OFF", "-DBUILD_DOC=OFF", "-DBUILD_REGRESS=OFF",
            env=environment, capture=False)
        run("cmake", "--build", str(build), "--target", "install", "-j", jobs,
            env=environment, capture=False)
    elif recipe["build"] == "openssl":
        # OpenSSL has its own configure, and `install_sw` is the difference between three minutes
        # and twenty: the rest of `install` is documentation nothing here reads.
        run("./config", f"--prefix={prefix}", f"--openssldir={prefix}/ssl", "shared", "no-tests",
            cwd=unpacked, env=environment, capture=False)
        run("make", f"-j{jobs}", cwd=unpacked, env=environment, capture=False)
        run("make", "install_sw", cwd=unpacked, env=environment, capture=False)
    else:
        run("./configure", f"--prefix={prefix}", "--disable-static", *arguments,
            cwd=unpacked, env=environment, capture=False)
        run("make", f"-j{jobs}", cwd=unpacked, env=environment, capture=False)
        run("make", "install", cwd=unpacked, env=environment, capture=False)


def linux_dependencies(work: Path, extra: Path) -> dict[str, Path]:
    """Install what the image has, build what it does not. Returns the ones that were built."""
    for enabling in (["dnf", "config-manager", "--set-enabled", "powertools"],
                     ["dnf", "config-manager", "--set-enabled", "crb"],
                     ["dnf", "install", "-y", "epel-release"]):
        attempt(*enabling)
    # One at a time rather than one transaction: a single name missing from this image would
    # otherwise take the whole list down with it, and the three that move around have a fallback.
    for package in DNF_PACKAGES:
        attempt("dnf", "install", "-y", package)

    built = {}
    # Only the three that move around. Everything else in this image is already the version PHP 7
    # was written against, which is the whole reason the Linux legs build here.
    for name in ("oniguruma", "libzip", "libwebp"):
        recipe = SOURCE_LIBRARIES[name]
        if not have_library(recipe["pkgconfig"]):
            print(f"{name} is not packaged in this image; building it from source")
            build_library(work, extra, name)
            built[recipe["provides"]] = extra
    return built


def macos_dependencies(work: Path, extra: Path) -> dict[str, Path]:
    """Install what Homebrew can give, and compile the four PHP 7 is version-sensitive about."""
    # Installed one at a time for the same reason as on Linux, and because a formula Homebrew has
    # since renamed would otherwise fail the whole install rather than one line of it.
    for package in BREW_PACKAGES:
        attempt("brew", "install", package, timeout=3600)

    built = {}
    for name in MACOS_SOURCE_LIBRARIES:
        print(f"building {name} from source: see SOURCE_LIBRARIES for why this one is pinned")
        build_library(work, extra, name)
        built[SOURCE_LIBRARIES[name]["provides"]] = extra
    return built


ICU_CONFIG_SHIM = """#!/bin/sh
# Written by mixengine-packages. ext/intl before PHP 7.4 finds ICU only by running `icu-config`,
# which ICU deprecated in 61 and has since stopped shipping. This answers the handful of questions
# PHP's PHP_SETUP_ICU actually asks, from a prefix fixed at build time, and is not general-purpose.
prefix="{prefix}"
case "$1" in
  --prefix|--prefix=*) echo "$prefix" ;;
  --version) echo "{version}" ;;
  --cflags|--cxxflags) echo "" ;;
  --cppflags|--cppflags-searchpath) echo "-I$prefix/include" ;;
  --ldflags|--ldflags-searchpath) echo "-L$prefix/lib -licui18n -licuuc -licudata" ;;
  --ldflags-icuio) echo "-licuio" ;;
  --ldflags-libsonly) echo "-licui18n -licuuc -licudata" ;;
  *) echo "" ;;
esac
"""


def icu_config_shim(prefix: Path, icu: Path) -> Path:
    """Put an ``icu-config`` in *prefix* that answers for the ICU at *icu*.

    Whether the pinned release still ships a real one is not worth depending on — it is written
    afterwards either way, so there is exactly one answer and it is this recipe's. The version is
    read from ICU's own pkg-config file rather than assumed from the pin, because a shim that lies
    about the version is worse than no shim: PHP compares it.

    Everything this recipe generates is written as UTF-8, including shell. It used to be written as
    ASCII on the reasoning that generated program text has no business carrying anything else, and
    that cost a macOS build: the em dash in the comment above is inside the template, so every
    branch that needs this shim — 7.0 through 7.3, and only those — died here rather than in a
    compiler. A build must not be able to fail over the punctuation in its own comments.
    """
    version = "60.1"
    for candidate in (icu / "lib" / "pkgconfig" / "icu-uc.pc", icu / "lib" / "pkgconfig" / "icu-i18n.pc"):
        if candidate.is_file():
            found = re.search(r"^Version:\s*(\S+)", candidate.read_text(encoding="utf-8", errors="replace"),
                              re.MULTILINE)
            if found:
                version = found.group(1)
                break
    (prefix / "bin").mkdir(parents=True, exist_ok=True)
    shim = prefix / "bin" / "icu-config"
    shim.write_text(ICU_CONFIG_SHIM.format(prefix=icu, version=version), encoding="utf-8")
    shim.chmod(0o755)
    print(f"wrote an icu-config for ICU {version} at {shim}")
    return shim


def autoconf_269(work: Path, prefix: Path) -> None:
    """Put autoconf 2.69 on PATH, for ``phpize`` on the branches that need it.

    ``phpize`` regenerates a configure script with whatever autoconf is on the machine, and PHP
    before 7.4 does not survive 2.70's stricter quoting. Homebrew carries only the current one, so
    the era's is built here: two minutes, once, against the alternative of patching four branches'
    build systems.
    """
    directory = work / "autoconf"
    directory.mkdir(parents=True, exist_ok=True)
    tarball = directory / "autoconf-2.69.tar.xz"
    tarball.write_bytes(fetch("https://ftp.gnu.org/gnu/autoconf/autoconf-2.69.tar.xz"))
    with tarfile.open(tarball) as archive:
        archive.extractall(directory)
    unpacked = directory / "autoconf-2.69"
    run("./configure", f"--prefix={prefix}", cwd=unpacked, capture=False)
    run("make", "install", cwd=unpacked, capture=False)


# --------------------------------------------------------------------------------- configure ---


COMMON = [
    "--disable-cgi",            # a site here is served by php-fpm; php-cgi is Windows' answer
    "--disable-phpdbg",
    "--enable-fpm",
    "--enable-bcmath", "--enable-calendar", "--enable-dba", "--enable-exif", "--enable-ftp",
    "--enable-intl", "--enable-mbstring", "--enable-mysqlnd", "--enable-opcache",
    "--enable-pcntl", "--enable-shmop", "--enable-soap", "--enable-sockets",
    "--enable-sysvmsg", "--enable-sysvsem", "--enable-sysvshm",
    "--with-mysqli=mysqlnd", "--with-pdo-mysql=mysqlnd",
    "--with-sqlite3", "--with-pdo-sqlite",
    # `--with-curl` and `--with-zlib` are not here: before 7.4 they take a directory on macOS. See
    # the branch split below.
]

# Flags that take a directory in every branch this recipe covers, so a keg-only Homebrew prefix can
# be handed to them directly. Anything not in here is found through PKG_CONFIG_PATH and CPPFLAGS,
# because 7.4 moved several of them to pkg-config and passing a directory then is an error.
DIRECTED = {
    "openssl": "--with-openssl", "libpq": "--with-pgsql", "bzip2": "--with-bz2",
    "libiconv": "--with-iconv", "libxslt": "--with-xsl", "gmp": "--with-gmp",
    "readline": "--with-readline",
}


def configure_arguments(branch: tuple[int, int], prefixes: dict[str, Path]) -> list[str]:
    """The flags for one branch. The differences below are the whole reason this is a table.

    PHP 7.4 rewrote how several extensions are found — gd stopped taking ``--with-*-dir``, zip moved
    from ``--enable-zip`` to ``--with-zip``, and libxml, curl and intl started going through
    pkg-config. Passing 8.x's spelling to 7.2 does not fail loudly; it quietly builds a PHP without
    the extension, which is then discovered by a user whose site needs it.

    Two kinds of flag, and the difference matters. **Switches** turn an extension on and are passed
    with or without a directory — dropping one would drop the extension. **Hints** only say where to
    look, and are passed *only* when there is somewhere to point them: a bare ``--with-icu-dir``
    does not mean "search the usual places", it sets the value to ``yes`` and PHP then runs
    ``yes/bin/icu-config``.
    """
    def switch(name: str, flag: str) -> str:
        prefix = prefixes.get(name)
        return f"{flag}={prefix}" if prefix else flag

    def hint(name: str, flag: str) -> list[str]:
        prefix = prefixes.get(name)
        return [f"{flag}={prefix}"] if prefix else []

    arguments = list(COMMON)
    arguments += [switch(name, flag) for name, flag in DIRECTED.items()]
    arguments.append(switch("libpq", "--with-pdo-pgsql"))

    if branch >= (7, 4):
        # Both go through pkg-config from 7.4, where a directory is not what the flag means.
        arguments += ["--with-curl", "--with-zlib"]
        arguments += ["--enable-gd", "--with-jpeg", "--with-freetype", "--with-zip", "--with-libxml"]
        if "webp" in prefixes or have_library("libwebp"):
            arguments.append("--with-webp")
        # No `--with-onig`: 7.4 has no such option and answers with "unrecognized options", which is
        # a warning rather than an error and therefore easy to ship past. mbstring finds oniguruma
        # through pkg-config, and PKG_CONFIG_PATH already points at ours when we built it.
    else:
        # Switches, not hints: dropping either would drop the extension. On Linux there is no prefix
        # for them and the bare form is right, because `/usr/include/zlib.h` is where they look.
        arguments += [switch("curl", "--with-curl"), switch("zlib", "--with-zlib")]
        arguments += ["--with-gd", "--enable-zip"]
        arguments += hint("libpng", "--with-png-dir")
        arguments += hint("jpeg", "--with-jpeg-dir")
        arguments += hint("freetype", "--with-freetype-dir")
        arguments += hint("webp", "--with-webp-dir")
        arguments += hint("libxml2", "--with-libxml-dir")
        arguments += hint("icu", "--with-icu-dir")
        if branch == (7, 3):
            # 7.3 deprecated the bundled libzip and 7.4 removed it. Using the system one a branch
            # early means one fewer thing that behaves differently between two adjacent versions.
            arguments += hint("libzip", "--with-libzip")
    if branch >= (7, 2):
        arguments.append(switch("libsodium", "--with-sodium"))
    return arguments


def dependency_prefixes(built_from_source: dict[str, Path]) -> dict[str, Path]:
    """Where each dependency lives — which on macOS is nowhere the compiler looks by default.

    On Linux the answer is "where they always are", and saying so explicitly is worse than saying
    nothing: ``--with-iconv=/usr`` makes PHP look for a *libiconv*, which glibc does not have
    because iconv is part of the C library — the build then fails asking for it to be reinstalled.
    So on Linux only the libraries this recipe compiled itself get a directory.
    """
    if sys.platform == "darwin":
        found = {name: brew_prefix(formula) for name, formula in BREW_FORMULAE.items()}
        prefixes = {name: prefix for name, prefix in found.items() if prefix}
        sdk = macos_sdk()
        if sdk and (sdk / "usr" / "include" / "zlib.h").is_file():
            # zlib and curl are Apple's, and before 7.4 PHP hunts for them by reading
            # `$DIR/include/zlib.h` and `$DIR/include/curl/easy.h`, searching `/usr/local` and
            # `/usr`. Neither exists on a macOS since Xcode 10 — the system's headers moved into the
            # SDK — so a bare `--with-zlib` fails with "Cannot find libz" on a machine that has had
            # zlib all along. 7.4 and newer are unaffected: they ask pkg-config, which is answered
            # by putting the SDK on its path below.
            prefixes["zlib"] = prefixes["curl"] = sdk / "usr"
    else:
        prefixes = {}
    prefixes.update(built_from_source)
    return prefixes


def build_environment(prefixes: dict[str, Path], extra: Path) -> dict[str, str]:
    environment = {**os.environ}
    sdk = macos_sdk()

    # Prefixes the platform owns rather than this recipe. A dependency may still be *pointed* at one
    # of these — that is how `--with-zlib` is answered on macOS — but neither may go into the flags
    # every compile gets. `-I/usr/include` shadows the SDK; `-I<sdk>/usr/include` is worse, because
    # it puts Apple's headers ahead of the Homebrew prefixes further down the list. That is how a
    # build ends up compiling against the system's `iconv.h` while linking GNU libiconv, which does
    # not fail until a `dyld` symbol error at startup.
    platform_owned = {Path("/usr")} | ({sdk / "usr"} if sdk else set())

    pkgconfig, includes, libraries = [], [], []
    for prefix in [extra] + sorted(set(prefixes.values())):
        if prefix in platform_owned:
            continue
        pkgconfig.append(str(prefix / "lib" / "pkgconfig"))
        includes.append(f"-I{prefix / 'include'}")
        libraries.append(f"-L{prefix / 'lib'}")

    if sdk:
        # curl, zlib and sqlite are Apple's on macOS, and from 7.4 PHP looks for them through
        # pkg-config. Their `.pc` files live inside the SDK, which is not on pkg-config's default
        # search path — so it is put there rather than relied upon.
        pkgconfig.append(str(sdk / "usr" / "lib" / "pkgconfig"))

    existing = environment.get("PKG_CONFIG_PATH")
    environment["PKG_CONFIG_PATH"] = os.pathsep.join(pkgconfig + ([existing] if existing else []))
    environment["CPPFLAGS"] = " ".join(includes + [environment.get("CPPFLAGS", "")]).strip()

    # The era applies to the language too, not only to the libraries. AlmaLinux 8's gcc-toolset is
    # new enough to default to C23, under which `false` stopped being a null pointer constant and
    # `f()` came to mean "no parameters" rather than "unspecified" — so C written before it stops
    # compiling. That is not a hypothetical: it is what stopped `mongodb` ("incompatible types when
    # returning `_Bool` where `mc_mincover_t *` was expected") and `xdebug` ("too many arguments to
    # xdebug_develop_minit"), neither of which had anything to do with the PHP version they were
    # being built against.
    #
    # Choosing the standard is not enough on its own, because the second half of that change was
    # about *diagnostics*: gcc 14 and clang 16 turned six long-standing warnings into errors. The
    # code they reject is mostly not PHP's — it is `configure`'s own probe programs, written when
    # implicit declarations were ordinary C. A probe that fails to compile does not stop the build;
    # it answers "no", and autoconf writes the wrong answer into `php_config.h`:
    #
    #   * 7.0 — the broken-sprintf probe is `main() {char buf[20];exit(sprintf(buf,"…")!=11);}`,
    #     with no includes at all. Rejected, so configure concludes sprintf *is* broken and declares
    #     `int zend_sprintf(…)` in `php_config.h`. That header has no include guard, `ext/intl`
    #     reaches it once at C++ scope and once inside `extern "C"`, and the build dies on a
    #     conflicting linkage error naming a function nobody asked for.
    #   * 7.3 — the readdir_r probe passes a `DIR *` to `close()`. Rejected, so configure falls
    #     through to "old-style", and `main/reentrancy.c` calls the two-argument readdir_r that no
    #     libc has shipped in twenty years.
    #
    # Both look like PHP failing to compile. Neither is. Restoring the era's diagnostics is the
    # same argument as choosing the era's distribution, applied to the compiler's opinions.
    relaxed = ["implicit-function-declaration", "implicit-int", "int-conversion",
               "incompatible-pointer-types"]
    if sys.platform != "darwin":
        relaxed += ["return-mismatch", "declaration-missing-parameter-type"]   # gcc spellings
    permit = " ".join(f"-Wno-error={name}" for name in relaxed)
    environment["CFLAGS"] = f"-std=gnu17 {permit} {environment.get('CFLAGS', '')}".strip()
    environment["CXXFLAGS"] = f"-std=gnu++17 {environment.get('CXXFLAGS', '')}".strip()

    link = list(libraries)
    if sys.platform.startswith("linux"):
        # `-ldl` up front, so configure's very first `dlopen` test links rather than its fallback.
        # This matters more than it looks: PHP defines HAVE_LIBDL off that test, and **without
        # HAVE_LIBDL both extension loaders in main/php_ini.c compile down to empty functions** —
        # so an `extension=` line is not refused, it is not read, and PHP starts perfectly while
        # loading nothing and saying nothing. On glibc before 2.34 dlopen lives in libdl rather than
        # in libc, which is exactly where that first test looks.
        link.append("-ldl")
    if sys.platform == "darwin":
        # Without this the load commands are packed tight, and `install_name_tool` later refuses to
        # lengthen a path — which is the entire relocation step, failing after the build rather than
        # before it.
        link.append("-Wl,-headerpad_max_install_names")
        # `dns_get_record` needs the resolver, and since the macOS 14 SDK PHP's configure no longer
        # works out that it has to ask for it — the build gets all the way to linking the binary and
        # then cannot find `res_9_dn_expand`. Homebrew's PHP formulae patch around the same thing.
        link.append("-lresolv")
        # ICU is C++ and current releases need C++17; PHP's own configure never says so.
        environment["ICU_CXXFLAGS"] = "-std=c++17"
    environment["LDFLAGS"] = " ".join(link + [environment.get("LDFLAGS", "")]).strip()
    return environment


# ------------------------------------------------------------------------------------- build ---


def build(source: Path, prefix: Path, branch: tuple[int, int], environment: dict[str, str],
          prefixes: dict[str, Path]) -> None:
    arguments = [f"--prefix={prefix}"] + configure_arguments(branch, prefixes)
    run("./configure", *arguments, cwd=source, env=environment, capture=False)
    run("make", f"-j{os.cpu_count() or 2}", cwd=source, env=environment, capture=False)
    run("make", "install", cwd=source, env=environment, capture=False)
    if not (prefix / "bin" / "php").exists():
        raise SystemExit(f"make install produced no {prefix / 'bin' / 'php'}")


# How far back down a package's release list to look. It has to be this large: reaching a `mongodb`
# that still supports PHP 7.0 means walking past every 2.x and most of 1.x, roughly eighty releases.
# A smaller number does not fail — it quietly reports "nothing supports this PHP" and the artifact
# ships without an extension MixEngine promised across the whole range.
PECL_DEPTH = 250

# Tried in turn when the newest suitable release does not compile. A declared range is a claim, and
# claims about a PHP from ten years ago are sometimes optimistic.
PECL_ATTEMPTS = 5

# Missing either of these is a failed build, not a warning. They are the two extensions MixEngine
# was told it must carry on every version, so an artifact without one is an artifact that lies about
# what it can run.
PECL_REQUIRED = {"redis", "mongodb"}

# Loaded with `zend_extension=` rather than `extension=`. Getting this wrong does not look like a
# configuration mistake from the outside — the extension simply reports as not loaded, which is
# indistinguishable from a broken build. `opcache` is here as well as `xdebug` because PHP builds it
# as a shared module by default, so it arrives in `ext/` alongside the PECL ones.
ZEND_EXTENSIONS = {"xdebug", "opcache"}

# What `extension_loaded()` answers to, where that is not the file name. Only opcache so far, and
# missing it makes a perfectly loaded extension report as absent.
EXTENSION_NAMES = {"opcache": "Zend OPcache"}


def pecl_candidates(package: str, version: tuple[int, ...], work: Path):
    """Yield ``(release, tarball)`` for each stable release that says it supports this PHP.

    Newest first, and lazily — the tarball for a candidate is only fetched once its declared range
    has already been checked. That check reads PEAR's ``deps.<version>.txt``, a few hundred bytes of
    serialised PHP, rather than the ``package.xml`` inside the tarball: the answer is the same and
    it is the difference between eighty small requests and eighty archive downloads.
    """
    try:
        catalogue = fetch(f"https://pecl.php.net/rest/r/{package}/allreleases.xml").decode()
    except urllib.error.HTTPError:
        return
    candidates = [
        found for found, stability in re.findall(r"<v>([^<]+)</v>\s*<s>([^<]+)</s>", catalogue)
        # Both halves are needed. PECL's declared stability is what the packager typed, and
        # `igbinary 3.2.17RC1` is declared stable there — the version string is the more honest of
        # the two, and a release candidate is not what a runtime manager should be shipping.
        if stability == "stable" and re.fullmatch(r"[0-9.]+", found)
    ]
    candidates.sort(key=parts, reverse=True)

    for candidate in candidates[:PECL_DEPTH]:
        try:
            declaration = fetch(
                f"https://pecl.php.net/rest/r/{package}/deps.{candidate}.txt"
            ).decode("utf-8", "replace")
        except urllib.error.HTTPError:
            continue
        supported = supports(declaration, version)
        if supported is None:
            continue
        tarball = work / f"{package}-{candidate}.tgz"
        try:
            tarball.write_bytes(fetch(f"https://pecl.php.net/get/{package}-{candidate}.tgz"))
        except urllib.error.HTTPError:
            continue
        print(f"{package} {candidate} declares PHP {supported}")
        yield candidate, tarball


def supports(declaration: str, version: tuple[int, ...]) -> str | None:
    """The PHP range a package declares, if *version* is inside it, else None.

    Reads either shape PEAR states it in: the serialised ``a:1:{s:8:"required";…}`` of
    ``deps.<version>.txt``, or the ``<php><min>…`` of a ``package.xml``. A package that declares no
    range at all is treated as not answering the question rather than as answering yes — PECL has
    releases from before the field was used, and assuming one of those supports a PHP from a decade
    later is how a build fails an hour in.
    """
    serialised = re.search(r's:3:"php";a:\d+:\{(.*?)\}', declaration, re.S)
    if serialised:
        body = serialised.group(1)
        low = re.search(r's:3:"min";s:\d+:"([^"]+)"', body)
        high = re.search(r's:3:"max";s:\d+:"([^"]+)"', body)
    else:
        span = re.search(r"<php>(.*?)</php>", declaration, re.S)
        if not span:
            return None
        low = re.search(r"<min>([^<]+)</min>", span.group(1))
        high = re.search(r"<max>([^<]+)</max>", span.group(1))
    if not low and not high:
        return None
    if low and version < parts(low.group(1)):
        return None
    if high and version > parts(high.group(1)):
        return None
    return f"{low.group(1) if low else '*'} – {high.group(1) if high else '*'}"


def build_extensions(prefix: Path, version: str, work: Path,
                     environment: dict[str, str]) -> dict[str, str]:
    """Build the PECL set with ``phpize``, and report what was built at which version.

    Each package walks down its own release list until one compiles. A declared range is what the
    packager believed at the time, and for a PHP this old the belief is sometimes wrong — the newest
    `mongodb` that claims 7.x is not always the newest that builds against it. Trying the next one
    down costs a minute; not trying it costs an artifact quietly missing an extension.
    """
    chosen: dict[str, str] = {}
    for package in PECL:
        attempts = 0
        for release_version, tarball in pecl_candidates(package, parts(version), work):
            attempts += 1
            if attempts > PECL_ATTEMPTS:
                break
            directory = work / f"ext-{package}-{release_version}"
            directory.mkdir(parents=True, exist_ok=True)
            with tarfile.open(tarball) as archive:
                archive.extractall(directory)
            unpacked = directory / f"{package}-{release_version}"
            if not unpacked.is_dir():
                unpacked = next(path for path in sorted(directory.iterdir()) if path.is_dir())

            configure = [f"--with-php-config={prefix / 'bin' / 'php-config'}"]
            if package == "redis" and "igbinary" in chosen:
                configure.append("--enable-redis-igbinary")
            try:
                run(str(prefix / "bin" / "phpize"), cwd=unpacked, env=environment, capture=False)
                run("./configure", *configure, cwd=unpacked, env=environment, capture=False)
                run("make", f"-j{os.cpu_count() or 2}", cwd=unpacked, env=environment,
                    capture=False)
                run("make", "install", cwd=unpacked, env=environment, capture=False)
            except SystemExit:
                print(f"warning: {package} {release_version} declares support for PHP {version} "
                      f"and does not build against it; trying an older release", file=sys.stderr)
                continue
            chosen[package] = release_version
            break

        if package not in chosen:
            message = (f"no stable {package} release both claims PHP {version} and builds against "
                       f"it, within {PECL_ATTEMPTS} attempts")
            if package in PECL_REQUIRED:
                raise SystemExit(
                    f"{message}. MixEngine offers {package} on every version it ships, so an "
                    "artifact without it is not one worth publishing."
                )
            print(f"warning: {message}", file=sys.stderr)
    return chosen


def loads(php: Path, extension_dir: Path, module: str, ini: Path) -> tuple[bool, str, str]:
    """Try to load one extension through a generated ini, and report what PHP said about it.

    The ini is the mechanism the daemon will use, so it is the one worth proving — and
    ``display_startup_errors`` is turned on because loading an extension happens at startup, where
    PHP's default is to refuse in silence. A refusal nobody can see is the failure this whole check
    exists to catch.
    """
    name = EXTENSION_NAMES.get(module, module)
    directive = "zend_extension" if module in ZEND_EXTENSIONS else "extension"
    lines = ["display_errors=stderr\n", "display_startup_errors=On\n", "error_reporting=E_ALL\n",
             f'extension_dir="{extension_dir}"\n']
    if module == "redis" and (extension_dir / "igbinary.so").exists():
        lines.append(f'extension="{extension_dir / "igbinary.so"}"\n')
    lines.append(f'{directive}="{extension_dir / (module + ".so")}"\n')
    ini.write_text("".join(lines), encoding="utf-8")

    attempt = subprocess.run(
        [str(php), "-c", str(ini), "-r", f"echo extension_loaded({name!r}) ? 'yes' : 'no';"],
        capture_output=True, text=True, timeout=300,
    )
    ok = attempt.stdout.strip().endswith("yes")
    error = attempt.stderr.strip()
    if not ok and not error:
        # PHP refusing an extension without a word means one of exactly two things, and `dl()` says
        # which. It reports "dynamic modules are not supported" when PHP was built without
        # HAVE_LIBDL — in which case `extension=` lines are not ignored so much as compiled out of
        # existence, since both loader callbacks in main/php_ini.c have empty bodies without it.
        # Otherwise it reports dlopen's own complaint, which is the answer we were looking for all
        # along and which the ini path never shows.
        probe = subprocess.run(
            [str(php), "-c", str(ini), "-r", f"var_dump(dl({module + '.so'!r}));"],
            capture_output=True, text=True, timeout=300,
        )
        error = "dl() says: " + " ".join(
            (probe.stdout + " " + probe.stderr).split()
        )
    return ok, attempt.stdout.strip(), error


def installed_extension_dir(prefix: Path) -> Path | None:
    return next(
        (path for path in sorted((prefix / "lib" / "php" / "extensions").glob("*")) if path.is_dir()),
        None,
    )


def check_where_installed(prefix: Path, work: Path) -> None:
    """Load every built extension where it was installed, before anything has been moved.

    This is here to cut a two-sided question in half. When an extension will not load out of the
    finished archive, the cause is either the build or the packing — and those need entirely
    different fixes. Asking before the packing starts says which one it is, and costs seconds.
    """
    extension_dir = installed_extension_dir(prefix)
    if not extension_dir:
        return
    modules = sorted(path.stem for path in extension_dir.glob("*.so"))
    print(f"loading {len(modules)} extension(s) where they were installed, before packing")
    for module in modules:
        ok, answer, error = loads(prefix / "bin" / "php", extension_dir, module, work / "check.ini")
        print(f"  {module}: {'loads' if ok else f'does NOT load ({answer!r})'}")
        for line in error.splitlines():
            print(f"    {line}")


def assemble(prefix: Path, work: Path) -> tuple[Path, dict[str, str], list[str]]:
    """Lay the installed prefix out as the archive, and report what it provides.

    Unlike the Windows recipe this *does* choose a layout, because there is no publisher's layout to
    preserve — and it is the same layout ``php_unix.py`` produces, so the daemon sees one shape for
    every PHP it installs on these two systems.
    """
    tree = work / "tree"
    (tree / "bin").mkdir(parents=True)
    provides = {}
    for name, source in (("php", prefix / "bin" / "php"), ("php-fpm", prefix / "sbin" / "php-fpm")):
        if not source.exists():
            continue
        shutil.copy2(source, tree / "bin" / name)
        provides[name] = f"bin/{name}"
    if "php" not in provides:
        raise SystemExit("no php binary was installed")

    shared = []
    extensions = installed_extension_dir(prefix)
    if extensions:
        (tree / "ext").mkdir(exist_ok=True)
        for module in sorted(extensions.glob("*.so")):
            shutil.copy2(module, tree / "ext" / module.name)
            shared.append(module.stem)
    return tree, provides, shared


def collect_licences(tree: Path, source: Path, bundled: dict[str, Path]) -> None:
    """Ship the licence of everything in the archive, PHP's own and every library bundled with it.

    Driven by what was actually bundled rather than by the dependency list, because the two differ:
    a library can be linked and then turn out to be part of the C runtime, and one nobody asked for
    can arrive as a dependency of a dependency. Several of these licences require their text to
    travel with the binary, so this is a condition of redistributing the archive rather than tidiness.
    """
    licences = tree / "licenses"
    licences.mkdir(exist_ok=True)
    for name in ("LICENSE", "COPYING"):
        if (source / name).is_file():
            shutil.copy2(source / name, licences / f"php-{name}")

    origins = []
    for name, origin in bundled.items():
        origins.append(f"{name}\t{origin}")
        # Homebrew writes its install names through `opt/<formula>`, which is a symlink into the
        # Cellar; the licence text is in the Cellar, so the link is followed before looking.
        real = origin.resolve()
        texts: list[Path] = []
        if "/Cellar/" in str(real):
            # …/Cellar/<formula>/<version>/lib/libfoo.dylib -> the version directory is the root
            root = real
            while root.parent.name != "Cellar" and root.parent != root:
                root = root.parent
            label = root.parent.name
            texts = sorted(root.glob("LICENSE*")) + sorted(root.glob("COPYING*"))
        else:
            label = name
            try:
                owner = subprocess.run(
                    ["rpm", "-qf", "--queryformat", "%{NAME}", str(real)],
                    capture_output=True, text=True, timeout=120,
                )
                if owner.returncode == 0 and owner.stdout.strip():
                    label = owner.stdout.strip()
            except (OSError, subprocess.SubprocessError):
                pass
            directory = Path("/usr/share/licenses") / label
            if directory.is_dir():
                texts = sorted(path for path in directory.rglob("*") if path.is_file())
        if not texts:
            print(f"warning: no licence text found for {name} ({real})", file=sys.stderr)
        for text in texts:
            shutil.copy2(text, licences / f"{label}-{text.name}")
    (licences / "BUNDLED.tsv").write_text(
        "library\tbuilt from\n" + "\n".join(sorted(origins)) + "\n", encoding="utf-8"
    )


SMOKE_SCRIPT = r"""<?php
// Deliberately PHP 5-era syntax: this same script has to parse on 7.0.
$results = array();
$results['openssl'] = strlen(openssl_digest('mixengine', 'sha256')) === 64;
$curl = curl_version();
$results['curl'] = !empty($curl['version']);
$results['mbstring'] = mb_strtoupper('mixengine') === 'MIXENGINE';
$results['intl'] = numfmt_format(numfmt_create('en_US', NumberFormatter::DECIMAL), 1234.5) !== false;
$image = imagecreatetruecolor(1, 1);
$results['gd'] = !empty($image);
$results['zip'] = class_exists('ZipArchive');
$database = new SQLite3(':memory:');
$results['sqlite3'] = $database->querySingle('select 1') == 1;
$xml = simplexml_load_string('<a><b>c</b></a>');
$results['xml'] = $xml && (string) $xml->b === 'c';
$failed = array();
foreach ($results as $name => $ok) { if (!$ok) { $failed[] = $name; } }
echo $failed ? 'FAILED: ' . implode(',', $failed) : 'OK';
"""


def smoke(tree: Path, provides: dict[str, str], shared: list[str]) -> tuple[str, dict]:
    """Exercise the build from a directory it has never seen, with its libraries beside it.

    Three things are proven here that ``php -v`` cannot: that nothing in the tree still reaches
    outside it, that the bundled libraries are found *and work* after the move, and that a shared
    extension loads through a generated ``php.ini``, which is the mechanism the daemon uses.
    """
    elsewhere = Path(tempfile.mkdtemp(prefix="mixengine-smoke-")) / "moved here" / "php"
    elsewhere.parent.mkdir(parents=True)
    shutil.copytree(tree, elsewhere, symlinks=True)

    problems = relocate.verify(elsewhere)
    for problem in problems:
        print(f"error: {problem}", file=sys.stderr)
    if problems:
        raise SystemExit("the relocated tree still reaches outside itself")

    banner = run(str(elsewhere / "bin" / "php"), "-n", "-v").splitlines()[0]
    version = re.search(r"PHP (\d+\.\d+\.\d+)", banner)
    if not version:
        raise SystemExit(f"could not read a version out of {banner!r}")
    ran = [f"{path} -v" for path in provides.values()]
    for name, path in provides.items():
        if name != "php":
            run(str(elsewhere / path), "-v")

    script = elsewhere.parent / "smoke.php"
    script.write_text(SMOKE_SCRIPT, encoding="utf-8")
    answer = run(str(elsewhere / "bin" / "php"), "-n", str(script)).strip()
    if not answer.endswith("OK"):
        raise SystemExit(f"the relocated build cannot use its own libraries: {answer}")
    print("every bundled library answered from the relocated tree")

    # Every shared extension is loaded, not just the first one that works. An extension that built
    # and then cannot be loaded is the failure this is looking for — it is invisible in the build
    # log, and `redis` is exactly the case, since it resolves igbinary's symbols only at load time.
    loaded, refused = [], []
    ini = elsewhere.parent / "php.ini"
    php = elsewhere / "bin" / "php"
    for candidate in shared:
        ok, answer, error = loads(php, elsewhere / "ext", candidate, ini)
        if ok:
            loaded.append(candidate)
            print(f"loaded {candidate} from the relocated ext/, through a generated php.ini")
            continue
        refused.append(candidate)
        print(f"{candidate} did not load: {answer!r}", file=sys.stderr)
        for line in error.splitlines():
            print(f"  {line}", file=sys.stderr)
        if not error:
            # Nothing on stderr is its own diagnosis: PHP did not object, so it may not have been
            # asked. What it read, and what it ended up with, come from PHP rather than from a guess.
            print("  nothing on stderr; asking PHP what it read and what it has", file=sys.stderr)
            report = subprocess.run(
                [str(php), "-c", str(ini), "-i"], capture_output=True, text=True, timeout=300
            ).stdout
            for line in report.splitlines():
                if "Configuration File" in line or line.startswith("extension_dir"):
                    print(f"  {line}", file=sys.stderr)
            modules = subprocess.run(
                [str(php), "-c", str(ini), "-m"], capture_output=True, text=True, timeout=300
            ).stdout.split()
            print(f"  php -m: {' '.join(modules)}", file=sys.stderr)

    missing = sorted(PECL_REQUIRED - set(loaded))
    if missing:
        raise SystemExit(
            f"built but cannot be loaded: {', '.join(missing)}. These are offered on every version "
            "MixEngine ships, so a build where they do not load is not one worth publishing."
        )
    if refused:
        print(f"warning: {', '.join(refused)} built but would not load", file=sys.stderr)

    proof = {"relocated": True, "ran": ran, "loaded_extensions": loaded}
    shutil.rmtree(elsewhere.parent.parent, ignore_errors=True)
    return version.group(1), proof


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--branch", required=True,
        help="PHP branch, e.g. 7.4 — the last release of it is what gets built, and the recipe "
             "records which one that was",
    )
    parser.add_argument("--out", default=Path("dist"), type=Path)
    arguments = parser.parse_args()

    branch = tuple(int(piece) for piece in arguments.branch.split(".")[:2])
    if len(branch) != 2:
        raise SystemExit(f"--branch takes a branch like 7.4, not {arguments.branch!r}")
    if branch >= CEILING:
        raise SystemExit(
            f"php {arguments.branch} is static-php-cli's; use php_unix.py. This recipe exists for "
            "the branches that tool cannot build, and running both over one version would publish "
            "two different artifacts for the same cell."
        )
    if branch < (7, 0):
        raise SystemExit("MixEngine offers PHP from 7.0; nothing here builds 5.x")

    operating_system, arch = host()
    work = Path(tempfile.mkdtemp(prefix="mixengine-php-"))
    extra = work / "deps"
    extra.mkdir(parents=True)
    os.environ["PATH"] = f"{extra / 'bin'}{os.pathsep}{os.environ['PATH']}"

    if operating_system == "linux":
        built_from_source = linux_dependencies(work, extra)
    else:
        built_from_source = macos_dependencies(work, extra)
        if branch < (7, 4):
            autoconf_269(work, extra)
            # Homebrew's ICU is what 7.4 and newer build against, and what nothing older can. See
            # SOURCE_LIBRARIES: ext/intl on these branches predates both the namespace change and
            # the `bool` change, and the second is not fixable from outside the source.
            print("building icu from source: see SOURCE_LIBRARIES for why this one is pinned")
            build_library(work, extra, "icu")
            # ICU's Darwin makefile still names its libraries after themselves and nothing else, so
            # what it installs links but cannot be loaded. Left alone that does not announce itself:
            # configure's link probes go on passing while every *run* probe fails to launch, and PHP
            # stops several screens later claiming this system has no `struct flock`.
            repaired = relocate.absolutise(extra / "lib")
            print(f"gave {len(repaired)} librar{'y' if len(repaired) == 1 else 'ies'} "
                  f"an install name dyld can resolve")
            built_from_source["icu"] = extra
            # Written after ICU is installed, so ours is the `icu-config` that survives whether or
            # not this release still ships one. ext/intl before 7.4 finds ICU no other way.
            icu_config_shim(extra, extra)

    prefixes = dependency_prefixes(built_from_source)
    environment = build_environment(prefixes, extra)
    if operating_system == "macos" and branch < (7, 4):
        # ICU 61 stopped emitting `using namespace icu;` from its headers, and ext/intl on these
        # branches spells its types unqualified. This is ICU's own switch for exactly that, and it
        # is narrower than a `using` of our own: only ICU's headers are affected.
        #
        # The TRUE/FALSE macros are not an issue against the pinned 67, which still defines them —
        # they went in 68. Bumping that pin means adding `-DU_DEFINE_FALSE_AND_TRUE=1` back, and
        # cannot go past 69 at all.
        environment["CPPFLAGS"] = (
            environment.get("CPPFLAGS", "") + " -DU_USING_ICU_NAMESPACE=1"
        ).strip()
    source, version = source_tree(work, arguments.branch)

    prefix = Path("/opt/mixengine") / f"php-{version}"
    try:
        prefix.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        run("sudo", "mkdir", "-p", str(prefix))
        run("sudo", "chown", "-R", str(os.getuid()), "/opt/mixengine")

    build(source, prefix, branch, environment, prefixes)
    pecl_versions = build_extensions(prefix, version, work, environment)
    check_where_installed(prefix, work)
    tree, provides, shared = assemble(prefix, work)

    bundled = relocate.bundle(tree)
    print(f"bundled {len(bundled)} librar{'y' if len(bundled) == 1 else 'ies'}: "
          f"{', '.join(bundled)}")
    collect_licences(tree, source, bundled)

    version_built, proof = smoke(tree, provides, shared)
    static = json.loads(
        run(str(tree / "bin" / "php"), "-n", "-r", "echo json_encode(get_loaded_extensions());")
    )

    recipe = f"php-src {version} from source, {len(bundled)} bundled libraries"
    if pecl_versions:
        recipe += "; " + ", ".join(f"{name} {value}" for name, value in sorted(pecl_versions.items()))

    manifest = {
        "schema": 1,
        "kind": "php",
        "version": version_built,
        "os": operating_system,
        "arch": arch,
        "source": "built",
        "recipe": recipe,
        "provides": provides,
        "extensions": {"static": sorted(static), "shared": sorted(shared)},
        "smoke": proof,
    }
    if shared:
        manifest["extension_dir"] = "ext"
    measured = relocate.floor(tree)
    if measured:
        manifest["requires"] = {measured[0]: measured[1]}
        print(f"needs {measured[0]} {measured[1]} or newer")
    (tree / "mixengine-artifact.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    arguments.out.mkdir(parents=True, exist_ok=True)
    packed = arguments.out / f"php-{version_built}-{operating_system}-{arch}.tar.zst"
    try:
        run("tar", "--zstd", "-cf", str(packed), "-C", str(tree), ".")
    except SystemExit:
        # A tar that died half way leaves a truncated archive behind, and `dist/` is uploaded
        # wholesale — so it is removed rather than left for something downstream to find.
        packed.unlink(missing_ok=True)
        packed = packed.with_suffix("").with_suffix(".tar.gz")
        run("tar", "-czf", str(packed), "-C", str(tree), ".")

    (arguments.out / f"{packed.name}.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    shutil.rmtree(work, ignore_errors=True)

    print(f"packed {packed} ({packed.stat().st_size:,} bytes)")
    print(f"sha256 {sha256(packed)}")
    print(f"php {version_built} on {operating_system}/{arch}: {', '.join(sorted(provides))}")
    print(f"{len(static)} static extensions, {len(shared)} shared, {len(bundled)} bundled libraries")


if __name__ == "__main__":
    main()
