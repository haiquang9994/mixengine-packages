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

# Built from source only when the distribution has no package for them. AlmaLinux 8 has most of what
# PHP 7 wants; these three move between EPEL, CRB and nowhere depending on the image.
SOURCE_LIBRARIES = {
    "oniguruma": (
        "https://github.com/kkos/oniguruma/releases/download/v6.9.9/onig-6.9.9.tar.gz",
        "autotools", "oniguruma",
    ),
    "libzip": (
        "https://libzip.org/download/libzip-1.10.1.tar.gz", "cmake", "libzip",
    ),
    "libwebp": (
        "https://storage.googleapis.com/downloads.webmproject.org/releases/webp/libwebp-1.3.2.tar.gz",
        "autotools", "libwebp",
    ),
}

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
BREW_PACKAGES = [
    "openssl@3", "icu4c", "libzip", "oniguruma", "libsodium", "libpq", "gmp", "libxslt",
    "jpeg-turbo", "libpng", "freetype", "webp", "sqlite", "libxml2", "libiconv", "bzip2",
    "readline",   # macOS ships libedit, not readline, and PHP's `--with-readline` wants the latter
]

# The Homebrew formula behind each name the configure table uses. `icu4c` is versioned now
# (`icu4c@78`) and the number moves, which is why the lookup below tries the versioned spellings
# rather than trusting any one of them.
BREW_FORMULAE = {
    "openssl": "openssl@3", "icu": "icu4c", "libzip": "libzip", "oniguruma": "oniguruma",
    "libsodium": "libsodium", "libpq": "libpq", "gmp": "gmp", "libxslt": "libxslt",
    "jpeg": "jpeg-turbo", "libpng": "libpng", "freetype": "freetype", "webp": "webp",
    "sqlite": "sqlite", "libxml2": "libxml2", "libiconv": "libiconv", "bzip2": "bzip2",
    "readline": "readline",
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
    """Compile one of the few libraries the distribution may not carry."""
    url, system, _ = SOURCE_LIBRARIES[name]
    directory = work / f"lib-{name}"
    directory.mkdir(parents=True, exist_ok=True)
    tarball = directory / url.rsplit("/", 1)[-1]
    tarball.write_bytes(fetch(url))
    with tarfile.open(tarball) as archive:
        archive.extractall(directory)
    unpacked = next(path for path in sorted(directory.iterdir()) if path.is_dir())

    if system == "cmake":
        build = unpacked / "build"
        run("cmake", "-S", str(unpacked), "-B", str(build), f"-DCMAKE_INSTALL_PREFIX={prefix}",
            "-DCMAKE_INSTALL_LIBDIR=lib", "-DBUILD_SHARED_LIBS=ON", "-DBUILD_TOOLS=OFF",
            "-DBUILD_EXAMPLES=OFF", "-DBUILD_DOC=OFF", "-DBUILD_REGRESS=OFF", capture=False)
        run("cmake", "--build", str(build), "--target", "install",
            "-j", str(os.cpu_count() or 2), capture=False)
    else:
        run("./configure", f"--prefix={prefix}", "--disable-static", cwd=unpacked, capture=False)
        run("make", f"-j{os.cpu_count() or 2}", cwd=unpacked, capture=False)
        run("make", "install", cwd=unpacked, capture=False)


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
    for name, (_, _, pkgconfig) in SOURCE_LIBRARIES.items():
        if not have_library(pkgconfig):
            print(f"{name} is not packaged in this image; building it from source")
            build_library(work, extra, name)
            built[{"oniguruma": "oniguruma", "libzip": "libzip", "libwebp": "webp"}[name]] = extra
    return built


def macos_dependencies() -> None:
    # Installed one at a time for the same reason as on Linux, and because a formula Homebrew has
    # since renamed would otherwise fail the whole install rather than one line of it.
    for package in BREW_PACKAGES:
        attempt("brew", "install", package, timeout=3600)


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
    "--with-curl", "--with-zlib", "--with-sqlite3", "--with-pdo-sqlite",
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
    """
    def directory(name: str, flag: str) -> str:
        prefix = prefixes.get(name)
        return f"{flag}={prefix}" if prefix else flag

    arguments = list(COMMON)
    arguments += [directory(name, flag) for name, flag in DIRECTED.items()]
    arguments.append(directory("libpq", "--with-pdo-pgsql"))

    if branch >= (7, 4):
        arguments += ["--enable-gd", "--with-jpeg", "--with-freetype", "--with-zip", "--with-libxml"]
        if "webp" in prefixes or have_library("libwebp"):
            arguments.append("--with-webp")
        arguments.append(directory("oniguruma", "--with-onig"))
    else:
        arguments += ["--with-gd", "--enable-zip"]
        arguments.append(directory("libpng", "--with-png-dir"))
        arguments.append(directory("jpeg", "--with-jpeg-dir"))
        arguments.append(directory("freetype", "--with-freetype-dir"))
        if "webp" in prefixes or Path("/usr/include/webp/decode.h").exists():
            arguments.append(directory("webp", "--with-webp-dir"))
        arguments.append(directory("libxml2", "--with-libxml-dir"))
        arguments.append(directory("icu", "--with-icu-dir"))
        if branch == (7, 3):
            # 7.3 deprecated the bundled libzip and 7.4 removed it. Using the system one a branch
            # early means one fewer thing that behaves differently between two adjacent versions.
            arguments.append(directory("libzip", "--with-libzip"))
    if branch >= (7, 2):
        arguments.append(directory("libsodium", "--with-sodium"))
    return arguments


def dependency_prefixes(built_from_source: dict[str, Path]) -> dict[str, Path]:
    """Where each dependency lives — which on macOS is nowhere the compiler looks by default.

    On Linux everything the image packaged is under ``/usr``, and the flags that take a directory
    are given it explicitly rather than left bare: ``--with-icu-dir`` with no argument is not a
    default, it is a configure error.
    """
    if sys.platform == "darwin":
        found = {name: brew_prefix(formula) for name, formula in BREW_FORMULAE.items()}
        prefixes = {name: prefix for name, prefix in found.items() if prefix}
    else:
        prefixes = {name: Path("/usr") for name in BREW_FORMULAE}
        if not Path("/usr/include/webp/decode.h").exists():
            prefixes.pop("webp", None)
    prefixes.update(built_from_source)
    return prefixes


def build_environment(prefixes: dict[str, Path], extra: Path) -> dict[str, str]:
    environment = {**os.environ}
    pkgconfig, includes, libraries = [], [], []
    for prefix in [extra] + sorted(set(prefixes.values())):
        if prefix == Path("/usr"):
            continue          # already where the compiler looks; adding it only shadows the SDK
        pkgconfig.append(str(prefix / "lib" / "pkgconfig"))
        includes.append(f"-I{prefix / 'include'}")
        libraries.append(f"-L{prefix / 'lib'}")

    existing = environment.get("PKG_CONFIG_PATH")
    environment["PKG_CONFIG_PATH"] = os.pathsep.join(pkgconfig + ([existing] if existing else []))
    environment["CPPFLAGS"] = " ".join(includes + [environment.get("CPPFLAGS", "")]).strip()
    link = list(libraries)
    if sys.platform == "darwin":
        # Without this the load commands are packed tight, and `install_name_tool` later refuses to
        # lengthen a path — which is the entire relocation step, failing after the build rather than
        # before it.
        link.append("-Wl,-headerpad_max_install_names")
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


def pecl_release(package: str, version: tuple[int, ...], work: Path) -> tuple[str, Path] | None:
    """The newest stable release of *package* that says it supports this PHP, and its tarball.

    Resolved rather than pinned. A table of six branches times four extensions is twenty-four
    numbers nobody can check without doing exactly this, and every package already states its own
    range in the ``package.xml`` it ships — so that is what is read.
    """
    try:
        catalogue = fetch(f"https://pecl.php.net/rest/r/{package}/allreleases.xml").decode()
    except urllib.error.HTTPError:
        return None
    candidates = [
        found for found, stability in re.findall(r"<v>([^<]+)</v>\s*<s>([^<]+)</s>", catalogue)
        if stability == "stable"
    ]
    candidates.sort(key=parts, reverse=True)

    for candidate in candidates[:40]:
        tarball = work / f"{package}-{candidate}.tgz"
        try:
            tarball.write_bytes(fetch(f"https://pecl.php.net/get/{package}-{candidate}.tgz"))
        except urllib.error.HTTPError:
            continue
        with tarfile.open(tarball) as archive:
            member = next((name for name in archive.getnames() if name.endswith("package.xml")), None)
            manifest = archive.extractfile(member).read().decode("utf-8", "replace") if member else ""
        supported = supports(manifest, version)
        if supported is None:
            tarball.unlink(missing_ok=True)
            continue
        print(f"{package} {candidate} claims PHP {supported}")
        return candidate, tarball
    return None


def supports(manifest: str, version: tuple[int, ...]) -> str | None:
    """The PHP range a ``package.xml`` declares, if *version* is inside it, else None.

    Only the ``<php>`` block inside ``<required>`` is read. A package that declares no range at all
    is treated as not answering the question rather than as answering yes: PECL has releases from
    before the field was used, and assuming they support a PHP from a decade later is how a build
    fails an hour in.
    """
    span = re.search(r"<php>(.*?)</php>", manifest, re.S)
    if not span:
        return None
    low = re.search(r"<min>([^<]+)</min>", span.group(1))
    high = re.search(r"<max>([^<]+)</max>", span.group(1))
    if low and version < parts(low.group(1)):
        return None
    if high and version > parts(high.group(1)):
        return None
    return f"{low.group(1) if low else '*'} – {high.group(1) if high else '*'}"


def build_extensions(prefix: Path, version: str, work: Path,
                     environment: dict[str, str]) -> dict[str, str]:
    """Build the PECL set with ``phpize``, and report what was built at which version."""
    chosen: dict[str, str] = {}
    for package in PECL:
        found = pecl_release(package, parts(version), work)
        if not found:
            print(f"warning: no stable {package} release claims support for PHP {version}",
                  file=sys.stderr)
            continue
        release_version, tarball = found
        directory = work / f"ext-{package}"
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
            run("make", f"-j{os.cpu_count() or 2}", cwd=unpacked, env=environment, capture=False)
            run("make", "install", cwd=unpacked, env=environment, capture=False)
        except SystemExit:
            # One extension refusing to build is not a reason to publish nothing. The manifest lists
            # what is actually in the archive, so a missing one is visible rather than assumed.
            print(f"warning: {package} {release_version} did not build against PHP {version}",
                  file=sys.stderr)
            continue
        chosen[package] = release_version
    return chosen


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
    extensions = next(
        (path for path in sorted((prefix / "lib" / "php" / "extensions").glob("*")) if path.is_dir()),
        None,
    )
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
    script.write_text(SMOKE_SCRIPT, encoding="ascii")
    answer = run(str(elsewhere / "bin" / "php"), "-n", str(script)).strip()
    if not answer.endswith("OK"):
        raise SystemExit(f"the relocated build cannot use its own libraries: {answer}")
    print("every bundled library answered from the relocated tree")

    loaded = None
    for candidate in shared:
        ini = elsewhere.parent / "php.ini"
        directive = "zend_extension" if candidate == "xdebug" else "extension"
        # Every value quoted, as the Windows recipe does and for the same reason: this is the
        # mechanism the daemon uses, so it is the one worth proving.
        ini.write_text(
            'display_errors=stderr\n'
            f'extension_dir="{elsewhere / "ext"}"\n'
            f'{directive}="{elsewhere / "ext" / (candidate + ".so")}"\n',
            encoding="ascii",
        )
        answer = run(
            str(elsewhere / "bin" / "php"), "-n", "-c", str(ini),
            "-r", f"echo extension_loaded({candidate!r}) ? 'yes' : 'no';",
        ).strip()
        if answer.endswith("yes"):
            loaded = candidate
            print(f"loaded {candidate} from the relocated ext/, through a generated php.ini")
            break
        print(f"{candidate} did not load: {answer!r}")

    proof = {"relocated": True, "ran": ran}
    if loaded:
        proof["loaded_extension"] = loaded
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

    built_from_source: dict[str, Path] = {}
    if operating_system == "linux":
        built_from_source = linux_dependencies(work, extra)
    else:
        macos_dependencies()
        if branch < (7, 4):
            autoconf_269(work, extra)

    prefixes = dependency_prefixes(built_from_source)
    environment = build_environment(prefixes, extra)
    source, version = source_tree(work, arguments.branch)

    prefix = Path("/opt/mixengine") / f"php-{version}"
    try:
        prefix.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        run("sudo", "mkdir", "-p", str(prefix))
        run("sudo", "chown", "-R", str(os.getuid()), "/opt/mixengine")

    build(source, prefix, branch, environment, prefixes)
    pecl_versions = build_extensions(prefix, version, work, environment)
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
