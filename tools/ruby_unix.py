#!/usr/bin/env python3
"""Build Ruby from source for macOS and Linux, and pack it as a relocatable artifact.

This is the last cell in `MixEngine's runtime table`_ that nothing can be borrowed for, and the
counterpart of ``php_legacy_unix.py`` on the other side of the table. Windows is free — RubyInstaller
publishes relocatable ``.7z`` archives for both architectures and ``ruby.py`` repacks them — and the
three relocatable Ruby distributions that exist for these two systems were each checked and each
refused: Homebrew's ``portable-ruby`` publishes exactly one version, ``ruby/ruby-builder``'s own
README says its artifacts "cannot be moved around", and RVM's are prefix-bound and years stale.

Unlike the PHP 7 range this is a **standing commitment**: those six branches are final, and Ruby is
not. Every security release of a line MixEngine offers has to come back through here.

Four decisions carry the weight.

*``--enable-load-relative``, which is the flag this whole cell turns on.* It makes Ruby compute its
standard library, its architecture directory and its gem home from the executable's own path at
every start instead of from the prefix it was configured with — and it makes ``rbinstall`` write
``bin/gem`` and friends as a ``/bin/sh`` preamble that re-executes ``$bindir/ruby -x`` on itself
rather than as a script with an absolute ``#!``. RubyInstaller is the proof it works, because it is
what RubyInstaller does. :func:`relative_shebangs` checks the second half rather than trusting it.

*The trust store, which was this task's open question and is answered in OpenSSL rather than in
Ruby.* A Ruby linked against a distribution's OpenSSL inherits that distribution's ``OPENSSLDIR`` —
``/etc/pki/tls`` on the Red Hat family, ``/etc/ssl`` on the Debian one — so an artifact built on
AlmaLinux verifies certificates perfectly on the runner and fails every handshake on a Debian user's
machine, with an error that names nothing. Setting ``SSL_CERT_FILE`` from Ruby would only cover the
programs that read it, and would leave ``OpenSSL::X509::DEFAULT_CERT_FILE`` pointing at a path that
does not exist. So OpenSSL is compiled here and :func:`relative_cert_defaults` teaches its four
default-path functions to answer relative to *the loaded libcrypto's own location*, which is the
same trick as ``--enable-load-relative`` applied one library down. What comes out is what
RubyInstaller has on Windows: a CA bundle inside the tree that the constant itself names.

*Everything outside the C runtime is bundled.* ``relocate.py`` copies each library in beside the
binary, rewrites every reference to ``$ORIGIN``/``@loader_path``, re-signs each Mach-O ad-hoc, and
then proves it from a directory the build has never seen.

*The build machine must not appear in what the build wrote down.* Ruby records the flags it was
configured with in ``rbconfig.rb``, and every native gem a user installs is compiled with them —
so a ``-I/tmp/mixengine-ruby-xyz/deps/include`` left in there is a path that will never exist again,
and an absolute ``CC`` is a compiler that will not either. :func:`scrub` and the bare ``CC``/``CXX``
in :func:`build_environment` are the two halves of that, and the smoke test compiles a native gem
from the moved tree rather than taking either on trust.

``docs/building-from-source.md`` is the other half of this file and was written before it, out of
the PHP pipeline. Everything in it applies here.

.. _MixEngine's runtime table: https://github.com/haiquang9994/MixEngine/blob/master/.claude/operations/runtime-packaging.md
"""

from __future__ import annotations

import argparse
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
import borrow  # noqa: E402  — siblings, and this directory is not importable as a package
import eol  # noqa: E402
import relocate  # noqa: E402
import ruby_parity  # noqa: E402
import ruby_smoke  # noqa: E402
import strip  # noqa: E402

# Every Ruby release ruby-lang.org has ever published, with the SHA-256 it published beside it. One
# file answers both questions this recipe has — *what exists* and *what it should hash to* — which is
# why nothing here enumerates releases any other way. It is the same shape as
# python-build-standalone's `SHA256SUMS`, and it means the tarball is checked against what ruby-lang
# stated rather than against itself.
INDEX = "https://cache.ruby-lang.org/pub/ruby/index.txt"

# The oldest line offered, and the same floor the Windows half has. Below it, `--enable-load-relative`
# still works and YJIT does not exist, and MixEngine's version policy does not offer 3.1 anyway.
FLOOR = (3, 2)

# The Mozilla CA set, as curl publishes it — the same bundle RubyInstaller ships inside its own tree,
# from the same source, with its digest published beside it so the download is checked against
# something upstream said rather than against itself.
CA_BUNDLE = "https://curl.se/ca/cacert.pem"

# Compiled here on every target rather than taken from the machine, and each for its own reason.
#
#   openssl   the trust store answer above needs a libcrypto this recipe controls, and macOS has no
#             OpenSSL at all — LibreSSL's headers are not a drop-in for Ruby's ext/openssl. 3.5 is
#             the current LTS line.
#   libyaml   `psych` is how a Rails application reads `config/database.yml`, and AlmaLinux 8's
#             libyaml is from 2014. Two hundred kilobytes to stop caring which one is installed.
#   libffi    `fiddle` is the FFI every gem that talks to a C library goes through. The image's is
#             3.1 from 2014, which predates the closures on aarch64 that anything current relies on.
#
# zlib is deliberately *not* here: it is on both platforms in a version nothing in Ruby is sensitive
# to, and on macOS it is Apple's, inside the SDK, where it stays system rather than being bundled.
SOURCE_LIBRARIES = {
    "openssl": {
        "url": "https://github.com/openssl/openssl/releases/download/openssl-3.5.7/openssl-3.5.7.tar.gz",
        "version": "3.5.7", "build": "openssl",
    },
    "libyaml": {
        "url": "https://github.com/yaml/libyaml/releases/download/0.2.5/yaml-0.2.5.tar.gz",
        "version": "0.2.5", "build": "autotools",
    },
    "libffi": {
        "url": "https://github.com/libffi/libffi/releases/download/v3.4.8/libffi-3.4.8.tar.gz",
        "version": "3.4.8", "build": "autotools",
        # Without this libffi installs into `lib64` on a 64-bit Linux and `--with-opt-dir` looks in
        # `lib`, so the library is present and fiddle is built without it.
        "arguments": ["--disable-multi-os-directory"],
    },
}

# What AlmaLinux 8 is asked for. Deliberately a named list rather than a `dnf group`: it is a list a
# reader can check against the configure flags below. `perl` is not decoration — OpenSSL's build is
# written in it.
#
# `gdbm-devel` is deliberately absent although Ruby has a `gdbm` extension: libgdbm is GPLv3, and
# bundling it beside the binary would put obligations on this whole artifact that Ruby's own licence
# does not carry. It is the same reason `--enable-libedit` is passed below rather than linking GNU
# readline. Nothing a local web development environment does uses either.
DNF_PACKAGES = [
    "gcc", "gcc-c++", "make", "patch", "perl", "perl-IPC-Cmd", "pkgconfig", "patchelf", "binutils",
    "zlib-devel", "libedit-devel", "ncurses-devel",
    # Not for the build: `borrow.pack` asks tar for zstd and falls back to gzip where the machine
    # cannot, and this image cannot. Installing the compressor did **not** change that — the
    # artifacts still came out `.tar.gz` here and `.tar.zst` on macOS — so the refusal is tar's own
    # rather than a missing program, and `borrow.pack` prints it now instead of choosing in silence.
    # The package stays because it costs nothing and the day the image's tar learns the option, the
    # four artifacts of a version become the same kind of file. Both suffixes are named in the index
    # and either installs.
    "zstd",
    # Only so that the Linux artifacts are the Ruby the macOS ones are. A Homebrew runner has GMP
    # installed as somebody else's dependency, Ruby finds it and uses it for Integer arithmetic, and
    # an image without it produces a Ruby that behaves identically and is slower at one thing. The
    # licence is the same LGPL the PHP artifacts already carry it under.
    "gmp-devel",
]

# Homebrew is asked for almost nothing, which is the point: everything Ruby is version-sensitive
# about is compiled above, and everything else is Apple's and stays system.
#
# `gmp` is here for evenness rather than for a feature, and it was measured rather than assumed: the
# Intel runner had it installed as some other formula's dependency and the arm64 runner did not, so
# one artifact used it for Integer arithmetic and its sibling did not. Behaviour is identical either
# way. An artifact differing from the one beside it for a reason nobody chose is the thing worth
# preventing — see DNF_PACKAGES, which says the same about the Linux half.
BREW_PACKAGES = ["pkg-config", "gmp"]

# Where each command lives inside the tree. The keys are the same five the Windows recipe publishes,
# because `core::shims::COMMANDS` maps one set of command names onto whatever an artifact provides
# and a daemon must not have to know which recipe produced the thing it installed.
#
# `bundler` has no row although `bin/bundler` exists, for the reason `ruby.py` states: it is a
# *command* name, and an artifact publishing both spellings would be inviting the two to disagree
# about which file `bundler` runs.
LAYOUT = {
    "ruby": "bin/ruby",
    "gem": "bin/gem",
    "bundle": "bin/bundle",
    "rake": "bin/rake",
    "irb": "bin/irb",
}

REQUIRED = ("ruby", "gem", "bundle")

# Installed and then thrown away: documentation this repository has no business shipping four copies
# of, on four targets, for every version of every line.
#
# The list moved to `ruby_parity` in P5 and this name is kept as the way in, because the argument
# above was only ever true of four cells: `ruby.py` was carrying the same directories on the other
# two — 225 MB of them on 4.0.6 — while this comment described a policy it had never been told
# about. A decision two producers take separately drifts, and this one had.
PRUNE = ruby_parity.SURPLUS

# What replaces an absolute `#!` in a bin/ script that upstream did not already write relatively.
# `ruby -x` skips everything before the *next* `#!` line naming ruby, which is what makes one file
# both a shell script and a Ruby program. See `relative_shebangs`.
RELATIVE_SHEBANG = """#!/bin/sh
# -*- ruby -*-
# Rewritten by MixEngine's tools/ruby_unix.py: this tree is going to be installed somewhere else,
# and the interpreter has to be found from this script's own location rather than from the path it
# was configured with.
bindir="$(cd -P -- "$(dirname -- "$0")" && pwd -P)"
exec "$bindir/ruby" -x "$0" "$@"
#!ruby
"""


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
    try:
        return subprocess.run(command, capture_output=True, timeout=timeout).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def jobs() -> str:
    return str(os.cpu_count() or 2)


# ------------------------------------------------------------------------------- getting ruby ---


def resolve(spec: str) -> tuple[str, str, str]:
    """Turn ``3.4``, ``3.4.10`` or ``latest`` into ``(version, url, sha256)``.

    Previews are refused rather than ranked. ``index.txt`` lists ``ruby-4.0.0-preview2`` beside the
    stable releases, and a version whose channel nobody asked for should not be what ``latest``
    means — the same rule the Python recipe applies to release candidates.
    """
    listing = borrow.fetch(INDEX, timeout=300).decode("utf-8", "replace").splitlines()
    offered: dict[tuple[int, ...], tuple[str, str, str]] = {}
    for line in listing[1:]:
        columns = line.split("\t")
        if len(columns) < 4:
            continue
        name, url, _sha1, sha256 = columns[0], columns[1], columns[2], columns[3]
        match = re.fullmatch(r"ruby-(\d+\.\d+\.\d+)", name)
        if not match or not url.endswith(".tar.gz"):
            continue
        offered[borrow.parts(match.group(1))] = (match.group(1), url, sha256)

    if not offered:
        raise SystemExit(f"{INDEX} listed no stable release at all; its format has changed")

    if spec == "latest":
        candidates = sorted(key for key in offered if key[:2] >= FLOOR)
    else:
        prefix = borrow.parts(spec)
        candidates = sorted(key for key in offered if key[: len(prefix)] == prefix)

    if not candidates:
        lines = sorted({key[:2] for key in offered if key[:2] >= FLOOR})
        raise SystemExit(
            f"ruby-lang.org publishes no stable {spec}. MixEngine offers "
            f"{', '.join('.'.join(str(part) for part in line) for line in lines)}."
        )

    chosen = candidates[-1]
    if chosen[:2] < FLOOR:
        raise SystemExit(
            f"MixEngine offers Ruby from {'.'.join(str(part) for part in FLOOR)}; "
            f"{'.'.join(str(part) for part in chosen)} is below that floor"
        )
    return offered[chosen]


def source_tree(work: Path, version: str, url: str, expected: str) -> Path:
    tarball = work / url.rsplit("/", 1)[-1]
    print(f"fetching {url}")
    # Through `borrow.fetch` rather than `urlretrieve` for the retry: everything this recipe
    # downloads is fetched before anything is compiled, so a dropped connection here costs the
    # whole build and nothing else.
    try:
        tarball.write_bytes(borrow.fetch(url, timeout=600))
    except urllib.error.HTTPError as error:
        raise SystemExit(f"{url} answered {error.code}") from error

    actual = borrow.sha256(tarball)
    if actual != expected:
        raise SystemExit(
            f"{tarball.name} hashes to {actual}, and ruby-lang.org's index.txt states {expected}. "
            "Either the download is damaged or it is not the file ruby-lang.org released."
        )
    print(f"sha256 {actual} (matches cache.ruby-lang.org/pub/ruby/index.txt)")

    with tarfile.open(tarball) as archive:
        archive.extractall(work, filter="data")
    unpacked = work / f"ruby-{version}"
    if not (unpacked / "configure").is_file():
        raise SystemExit(
            f"{unpacked} has no generated configure; this is not a release tarball. Building from "
            "git would mean an autoconf and a baseruby, which is a dependency this recipe does not "
            "take for the same reason php_legacy_unix.py never runs buildconf."
        )
    return unpacked


# ------------------------------------------------------------------------------ dependencies ---


def relative_cert_defaults(source: Path) -> None:
    """Make this OpenSSL's default certificate paths relative to its own file, not to a prefix.

    ``OPENSSLDIR`` is fixed when OpenSSL is compiled, and everything downstream of it is a claim
    about the *build* machine: ``X509_get_default_cert_file`` answers ``/etc/pki/tls/cert.pem`` on
    the Red Hat family and ``/etc/ssl/cert.pem`` on the Debian one, and Ruby publishes whichever it
    got as ``OpenSSL::X509::DEFAULT_CERT_FILE``. Ship that to a user on the other family and every
    HTTPS request fails verification, with an error that names neither the file nor the reason.

    So the four path functions are taught to look beside themselves first. ``dladdr`` on a symbol
    in libcrypto answers with the path libcrypto was loaded from — ``<tree>/lib/libcrypto.so.3``,
    or ``<tree>/bin/ruby`` if it was ever linked statically — and two directories up from that is
    the root of the artifact, where this recipe puts ``ssl/cert.pem``. When that file is not there
    the compiled-in answer is returned unchanged, so an OpenSSL built by this and installed anywhere
    else behaves exactly as it always did. The environment variables are untouched: ``SSL_CERT_FILE``
    still wins over both, which is how a user points this Ruby at a corporate CA.

    The file is *edited* rather than replaced. Replacing it would silently drop any function a later
    OpenSSL adds to it; substituting four return statements and requiring all four to have matched
    fails loudly instead, which is the behaviour worth having in a file nobody will look at again
    until it breaks.

    Two details are borrowed from the file's own Windows branch, which solves the same problem from
    the other end — it reads ``OPENSSLDIR`` out of the registry at run time instead of trusting the
    one compiled in. Its once-only initialisation is OpenSSL's ``RUN_ONCE``, already included here,
    rather than anything this recipe brings; and the whole helper is compiled out on Windows, where
    that branch owns these functions and ``dladdr`` does not exist.
    """
    path = source / "crypto" / "x509" / "x509_def.c"
    text = path.read_text(encoding="utf-8")

    # `dladdr` is a GNU extension: glibc's dlfcn.h declares it only under `__USE_GNU`, which is
    # decided the first time any libc header is read. So this goes above every include in the file
    # rather than beside the one that needs it, and is guarded because the build may pass it too.
    prologue = "#ifndef _GNU_SOURCE\n# define _GNU_SOURCE 1\n#endif\n"

    helper = """
/* --- MixEngine (tools/ruby_unix.py): default certificate paths relative to this library --- */
#if !defined(_WIN32)
# include <dlfcn.h>
# include <limits.h>
# include <stdlib.h>
# include <string.h>
# include <unistd.h>

# ifndef PATH_MAX
#  define PATH_MAX 4096
# endif

static char mixengine_area[PATH_MAX];
static char mixengine_certs[PATH_MAX];
static char mixengine_file[PATH_MAX];
static char mixengine_private[PATH_MAX];
static int mixengine_found = 0;

static CRYPTO_ONCE mixengine_once = CRYPTO_ONCE_STATIC_INIT;
DEFINE_RUN_ONCE_STATIC(do_mixengine_locate)
{
    Dl_info info;
    char resolved[PATH_MAX];
    char *slash;

    /* Where this very object was loaded from: <tree>/lib/libcrypto.so.3, or the executable itself
     * where it was linked statically. Either way the artifact's root is two components up. */
    if (dladdr((const void *)mixengine_area, &info) == 0 || info.dli_fname == NULL)
        return 1;
    if (realpath(info.dli_fname, resolved) == NULL)
        return 1;
    if ((slash = strrchr(resolved, '/')) == NULL)
        return 1;
    *slash = '\\0';
    if ((slash = strrchr(resolved, '/')) == NULL)
        return 1;
    *slash = '\\0';
    if (snprintf(mixengine_file, sizeof(mixengine_file), "%s/ssl/cert.pem", resolved)
            >= (int)sizeof(mixengine_file))
        return 1;
    if (access(mixengine_file, R_OK) != 0)
        return 1;
    snprintf(mixengine_area, sizeof(mixengine_area), "%s/ssl", resolved);
    snprintf(mixengine_certs, sizeof(mixengine_certs), "%s/ssl/certs", resolved);
    snprintf(mixengine_private, sizeof(mixengine_private), "%s/ssl/private", resolved);
    mixengine_found = 1;
    return 1;
}

static int mixengine_relative(void)
{
    if (!RUN_ONCE(&mixengine_once, do_mixengine_locate))
        return 0;
    return mixengine_found;
}
#endif
/* --- end MixEngine --- */
"""

    substitutions = {
        "return X509_CERT_AREA;": "return mixengine_relative() ? mixengine_area : X509_CERT_AREA;",
        "return X509_CERT_DIR;": "return mixengine_relative() ? mixengine_certs : X509_CERT_DIR;",
        "return X509_CERT_FILE;": "return mixengine_relative() ? mixengine_file : X509_CERT_FILE;",
        "return X509_PRIVATE_DIR;":
            "return mixengine_relative() ? mixengine_private : X509_PRIVATE_DIR;",
    }
    for original, replacement in substitutions.items():
        if text.count(original) != 1:
            raise SystemExit(
                f"{path} does not contain exactly one {original!r}. OpenSSL has changed the file "
                "this recipe teaches to look beside itself, and guessing here would ship a Ruby "
                "whose CA bundle points at the machine that built it."
            )
        text = text.replace(original, replacement)

    includes = list(re.finditer(r"^#\s*include.*$", text, re.MULTILINE))
    if not includes:
        raise SystemExit(f"{path} has no #include to insert around")
    # The helper goes after the *last* include, so it sees X509_CERT_FILE, CRYPTO_ONCE and RUN_ONCE;
    # the prologue goes before the *first* one, for the reason stated where it is defined.
    text = (
        text[: includes[0].start()] + prologue + text[includes[0].start(): includes[-1].end()]
        + "\n" + helper + text[includes[-1].end():]
    )
    path.write_text(text, encoding="utf-8")
    print(f"patched {path.name}: default certificate paths now resolve beside libcrypto")


def build_library(work: Path, prefix: Path, name: str) -> None:
    """Compile one of the three libraries this recipe pins."""
    recipe = SOURCE_LIBRARIES[name]
    directory = work / f"lib-{name}"
    directory.mkdir(parents=True, exist_ok=True)
    tarball = directory / recipe["url"].rsplit("/", 1)[-1]
    tarball.write_bytes(borrow.fetch(recipe["url"], timeout=600))
    with tarfile.open(tarball) as archive:
        archive.extractall(directory, filter="data")
    unpacked = next(path for path in sorted(directory.iterdir()) if path.is_dir())

    environment = {**os.environ}
    if sys.platform == "darwin":
        # Everything built here will have its install names rewritten by `relocate.bundle` inside
        # the artifact. A Mach-O whose load commands were packed tight cannot be rewritten at all —
        # only the linker can leave room, and finding that out afterwards costs the whole build.
        environment["LDFLAGS"] = (
            "-Wl,-headerpad_max_install_names " + environment.get("LDFLAGS", "")
        ).strip()

    if name == "openssl":
        relative_cert_defaults(unpacked)
        # `--libdir=lib` because OpenSSL installs into `lib64` on a 64-bit Linux otherwise, and
        # every other path in this recipe — pkg-config, `--with-openssl-dir`, the bundling — looks
        # in `lib`. `install_sw` rather than `install` is the difference between three minutes and
        # twenty: the rest is documentation nothing here reads.
        run("./config", f"--prefix={prefix}", f"--openssldir={prefix}/ssl", "--libdir=lib",
            "shared", "no-tests", "no-docs", cwd=unpacked, env=environment, capture=False)
        run("make", f"-j{jobs()}", cwd=unpacked, env=environment, capture=False)
        run("make", "install_sw", cwd=unpacked, env=environment, capture=False)
    else:
        run("./configure", f"--prefix={prefix}", f"--libdir={prefix}/lib", "--disable-static",
            *recipe.get("arguments", []), cwd=unpacked, env=environment, capture=False)
        run("make", f"-j{jobs()}", cwd=unpacked, env=environment, capture=False)
        run("make", "install", cwd=unpacked, env=environment, capture=False)

    for text in sorted(unpacked.glob("LICENSE*")) + sorted(unpacked.glob("COPYING*")):
        if text.is_file():
            shutil.copy2(text, work / f"licence-{name}-{text.name}")


def ensure_rust(work: Path) -> str | None:
    """A Rust compiler, because YJIT is written in Rust and is not optional here.

    Asking for ``--enable-yjit`` without one is the failure mode this repository is least able to
    afford: Ruby's configure *warns* and builds an interpreter without a JIT, which then answers
    every version question correctly and is quietly two to three times slower on the Rails
    application somebody installed MixEngine to run. So the compiler is installed where the image
    has none, and :func:`smoke` asks the finished artifact whether YJIT actually turns on.

    A minimal rustup toolchain rather than a distribution package: AlmaLinux 8's is too old for
    Ruby's own floor, and macOS runners already have one, so this is one code path that ends in the
    same place on all four targets.
    """
    if attempt("rustc", "--version", timeout=120):
        return subprocess.run(
            ["rustc", "--version"], capture_output=True, text=True, timeout=120
        ).stdout.strip()

    print("no rustc on this machine; installing a minimal rustup toolchain for YJIT")
    script = work / "rustup-init.sh"
    script.write_bytes(borrow.fetch("https://sh.rustup.rs", timeout=300))
    run("sh", str(script), "-y", "--profile", "minimal", "--default-toolchain", "stable",
        "--no-modify-path", capture=False, timeout=1800)
    cargo = Path(os.environ.get("CARGO_HOME") or (Path.home() / ".cargo")) / "bin"
    os.environ["PATH"] = f"{cargo}{os.pathsep}{os.environ['PATH']}"
    if not attempt("rustc", "--version", timeout=120):
        raise SystemExit("rustup installed a toolchain this build cannot run")
    return subprocess.run(
        ["rustc", "--version"], capture_output=True, text=True, timeout=120
    ).stdout.strip()


def brew_prefix(formula: str) -> Path | None:
    """Where Homebrew put *formula*, or None. Asked rather than assumed — see :func:`build`."""
    result = subprocess.run(
        ["brew", "--prefix", formula], capture_output=True, text=True, timeout=120
    )
    prefix = Path(result.stdout.strip()) if result.stdout.strip() else None
    return prefix if result.returncode == 0 and prefix and prefix.is_dir() else None


def dependencies(work: Path, extra: Path) -> None:
    """Install what the machine can give and compile the three it must not be trusted for."""
    if sys.platform == "darwin":
        for package in BREW_PACKAGES:
            attempt("brew", "install", package, timeout=3600)
    else:
        # `libedit-devel` lives in the repository AlmaLinux renamed between 8 and 9, so both names
        # are tried and neither is required.
        for enabling in (["dnf", "config-manager", "--set-enabled", "powertools"],
                         ["dnf", "config-manager", "--set-enabled", "crb"]):
            attempt(*enabling)
        # One at a time rather than one transaction: a single name missing from this image would
        # otherwise take the whole list down with it.
        for package in DNF_PACKAGES:
            attempt("dnf", "install", "-y", package)

    for name in SOURCE_LIBRARIES:
        print(f"building {name} {SOURCE_LIBRARIES[name]['version']} from source: "
              f"see SOURCE_LIBRARIES for why this one is not taken from the machine")
        build_library(work, extra, name)

    if sys.platform == "darwin":
        # ICU's Darwin makefile taught this repository that a library can link and then be
        # unloadable, because its install name is a bare file name with nowhere for dyld to look.
        # None of the three above is known to do it; checking costs a second and the failure it
        # prevents looks like the platform refusing features an hour later.
        repaired = relocate.absolutise(extra / "lib")
        if repaired:
            print(f"gave {len(repaired)} librar{'y' if len(repaired) == 1 else 'ies'} "
                  f"an install name dyld can resolve")


def ca_bundle(work: Path) -> tuple[Path, str, str]:
    """Fetch the Mozilla CA set, checked against the digest curl publishes beside it.

    Returns ``(file, sha256, date)``. The date is read out of the bundle's own header rather than
    taken from the clock: an artifact is published once and trusted for years, and *when* the
    authorities in it were current is the one thing a reader cannot recover from the bytes.
    """
    bundle = work / "cacert.pem"
    bundle.write_bytes(borrow.fetch(CA_BUNDLE, timeout=300))
    published = borrow.fetch(f"{CA_BUNDLE}.sha256", timeout=300).decode().split()
    actual = borrow.sha256(bundle)
    if published and published[0] != actual:
        raise SystemExit(f"{CA_BUNDLE} hashes to {actual}, curl.se states {published[0]}")

    header = bundle.read_text(encoding="utf-8", errors="replace")[:2000]
    stated = re.search(r"Certificate data from Mozilla as of: (.+)", header)
    date = stated.group(1).strip() if stated else "unstated"
    print(f"CA bundle from {CA_BUNDLE}, {date}, sha256 {actual} (matches curl.se's own digest)")
    return bundle, actual, date


# ----------------------------------------------------------------------------------- building ---


def build_environment(extra: Path) -> dict[str, str]:
    """The environment Ruby is configured in — and, because Ruby writes it down, shipped with.

    Two things here are about the *user's* machine rather than this one. ``CC`` and ``CXX`` are bare
    names on purpose: a manylinux image's compiler is ``/opt/rh/gcc-toolset-14/root/usr/bin/gcc``,
    and Ruby records whatever it was handed into ``rbconfig.rb``, where it becomes the compiler every
    native gem is built with — on a machine that has no such directory. A bare name is resolved from
    ``PATH`` at that moment instead, which is the only answer that can still be right in a year.

    And ``-Wl,-rpath`` names the dependency prefix so the freshly linked binaries can be *run*
    during ``make install`` and read by ``ldd`` afterwards; :func:`relocate.rewrite` replaces the
    whole search path with ``$ORIGIN`` in the artifact, and :func:`scrub` takes the flag itself back
    out of what was written down.
    """
    environment = {**os.environ}
    environment["PKG_CONFIG_PATH"] = os.pathsep.join(
        [str(extra / "lib" / "pkgconfig")]
        + ([environment["PKG_CONFIG_PATH"]] if environment.get("PKG_CONFIG_PATH") else [])
    )
    environment["CPPFLAGS"] = f"-I{extra / 'include'} {environment.get('CPPFLAGS', '')}".strip()

    link = [f"-L{extra / 'lib'}", f"-Wl,-rpath,{extra / 'lib'}"]
    if sys.platform == "darwin":
        # Only the linker can leave room for a longer install name, and every one of these files is
        # going to be rewritten by `relocate.bundle`.
        link.append("-Wl,-headerpad_max_install_names")
    environment["LDFLAGS"] = " ".join(link + [environment.get("LDFLAGS", "")]).strip()

    # A probe that fails to compile is not an error to autoconf — it is a *no*, written into the
    # configuration and built against. gcc 14 and clang 16 turned six long-standing warnings into
    # errors, and the code they reject is mostly `configure`'s own probe programs. Ruby's are newer
    # than PHP 7's and none of them is known to trip; this costs nothing and the failure it prevents
    # is a wrong answer rather than a compiler error. See docs/building-from-source.md.
    relaxed = ["implicit-function-declaration", "implicit-int", "int-conversion",
               "incompatible-pointer-types"]
    if sys.platform != "darwin":
        relaxed += ["return-mismatch", "declaration-missing-parameter-type"]   # gcc spellings
    permit = " ".join(f"-Wno-error={name}" for name in relaxed)
    environment["CFLAGS"] = f"{permit} {environment.get('CFLAGS', '')}".strip()

    environment["CC"] = "clang" if sys.platform == "darwin" else "gcc"
    environment["CXX"] = "clang++" if sys.platform == "darwin" else "g++"
    return environment


def build(source: Path, prefix: Path, extra: Path, environment: dict[str, str]) -> None:
    arguments = [
        f"--prefix={prefix}",
        # The flag this whole cell turns on. See the module docstring.
        "--enable-load-relative",
        "--disable-install-doc",
        "--enable-yjit",
        f"--with-openssl-dir={extra}",
        # Covers libyaml and libffi in one, which is what psych and fiddle look through.
        f"--with-opt-dir={extra}",
        # libedit rather than GNU readline, and the reason is the licence rather than the API:
        # readline is GPLv3, and linking it into a binary this project redistributes would put the
        # whole artifact under obligations Ruby's own licence does not carry. libedit is BSD and it
        # is what macOS ships.
        #
        # Measured: this only bites on 3.2. From 3.3 upstream replaced the C extension with a
        # `readline.rb` shim over the pure-Ruby `reline`, so `require "readline"` works on every
        # line here while only the 3.2 artifacts carry libedit, ncurses and tinfo beside them.
        "--enable-libedit",
        # Static libruby, linked into the executable: one fewer file to find after the tree moves,
        # and nothing in MixEngine embeds a Ruby interpreter.
        "--disable-shared",
    ]
    # **Homebrew's prefix is a search path on Intel and not on Apple Silicon**, which is how the two
    # macOS artifacts of one version came to differ: `/usr/local/include` is where the compiler
    # looks anyway, `/opt/homebrew/include` is not, so `brew install gmp` produced
    # `checking for gmp.h... yes` on one runner and `no` on the other — with no error on either.
    # Asking Homebrew where it put the thing costs one call and is the only spelling that is true
    # on both.
    gmp = brew_prefix("gmp") if sys.platform == "darwin" else None
    if gmp:
        arguments.append(f"--with-gmp-dir={gmp}")
    run("./configure", *arguments, cwd=source, env=environment, capture=False)
    run("make", f"-j{jobs()}", cwd=source, env=environment, capture=False)
    run("make", "install", cwd=source, env=environment, capture=False)
    if not (prefix / "bin" / "ruby").exists():
        raise SystemExit(f"make install produced no {prefix / 'bin' / 'ruby'}")


def relative_shebangs(tree: Path, prefix: Path) -> list[str]:
    """Check — and where necessary make true — that no ``bin/`` script names an absolute interpreter.

    ``--enable-load-relative`` makes ``rbinstall.rb`` write these as a ``/bin/sh`` preamble that
    re-executes ``$bindir/ruby -x`` on the script itself, so the whole tree moves and every command
    keeps working. That is upstream's behaviour and it is what this recipe expects; it is not what
    this recipe *trusts*, because a `#!` naming ``/opt/mixengine/ruby-3.4.10/bin/ruby`` fails on a
    user's machine with "no such file or directory" and nothing else, and the flag that prevents it
    is one line in a build system nobody here controls.

    Returns the scripts that had to be rewritten — empty on a Ruby that did it itself, which is the
    outcome worth noticing when a future release stops doing it.
    """
    rewritten = []
    for path in sorted((tree / "bin").iterdir()):
        if path.is_symlink() or not path.is_file() or relocate.kind(path):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        first, _, rest = text.partition("\n")
        if not first.startswith("#!") or str(prefix) not in first:
            continue
        path.write_text(RELATIVE_SHEBANG + rest, encoding="utf-8")
        path.chmod(0o755)
        rewritten.append(path.name)
    return rewritten


def assemble(prefix: Path, work: Path, bundle: Path) -> tuple[Path, dict[str, str], list[str]]:
    """Lay the installed prefix out as the archive: prune, add the trust store, fix what leaked."""
    tree = work / "tree"
    shutil.copytree(prefix, tree, symlinks=True)
    for pruned in PRUNE:
        shutil.rmtree(tree / pruned, ignore_errors=True)
    # macOS writes a `.dSYM` bundle beside every extension it compiles: the debug information,
    # lifted out of the binary into a Mach-O of its own. Nothing loads them, `relocate` now knows
    # not to touch them, and they are a third of the archive.
    for symbols in sorted(tree.rglob("*.dSYM")):
        shutil.rmtree(symbols, ignore_errors=True)

    # `make install` compiles each bundled gem's extension in place and leaves the build behind — a
    # Makefile, an mkmf.log and the object files, every one of them naming the directory this was
    # compiled in. The extension itself is installed under `extensions/`, so nothing here is loaded
    # by anything; what they would ship is a path that stopped existing when this build finished.
    for extensions in sorted(tree.glob("lib/ruby/gems/*/gems/*/ext")):
        for leftover in sorted(extensions.rglob("*")):
            if leftover.is_file() and (leftover.name in ("Makefile", "mkmf.log")
                                       or leftover.suffix == ".o"):
                leftover.unlink()

    # Where `relative_cert_defaults` taught libcrypto to look. `certs/` is created empty and
    # deliberately: it is the hash directory OpenSSL reads one certificate at a time, and a user
    # adding a corporate CA to this runtime has somewhere to put it.
    (tree / "ssl" / "certs").mkdir(parents=True, exist_ok=True)
    shutil.copy2(bundle, tree / "ssl" / "cert.pem")

    rewritten = relative_shebangs(tree, prefix)
    if rewritten:
        print(f"rewrote {len(rewritten)} absolute interpreter path(s): {', '.join(rewritten)}")
    else:
        print("every bin/ script was already written to find its interpreter relatively")

    provides = {name: where for name, where in LAYOUT.items() if (tree / where).exists()}
    missing = [name for name in REQUIRED if name not in provides]
    if missing:
        raise SystemExit(
            f"the build provides no {', '.join(missing)} — expected at "
            f"{', '.join(LAYOUT[name] for name in missing)}. Contents of bin/: "
            f"{sorted(path.name for path in (tree / 'bin').iterdir())[:25]}"
        )
    return tree, provides, rewritten


def scrub(tree: Path, directories: list[Path]) -> list[str]:
    """Take the build machine's own directories out of the flags Ruby wrote down.

    ``rbconfig.rb`` is not a log. It is the configuration every native gem is compiled with, so a
    ``-I/tmp/mixengine-ruby-a1b2c3/deps/include`` in it is a directory that will never exist again
    being handed to a compiler on somebody else's machine — harmless on a good day, and on a bad one
    a header found in a stale path that happens to exist there.

    Only *this build's* temporary directories are removed. The install prefix stays: like the PHP
    recipes, this installs to ``/opt/mixengine/ruby-<version>``, which is absent on purpose, so a
    path that leaks fails loudly instead of quietly picking up a stranger's file. Everything the
    artifact actually needs is found relative to the executable.
    """
    targets = [
        *sorted(tree.glob("lib/ruby/*/*/rbconfig.rb")),
        *sorted(tree.glob("lib/pkgconfig/*.pc")),
        *sorted(tree.glob("lib/ruby/*/rubygems/defaults/*.rb")),
    ]
    # The flag carrying the path goes with it, in both spellings Ruby writes: `-L/tmp/…` and
    # `-Wl,-rpath,/tmp/…` in the link flags, `--with-opt-dir=/tmp/…` inside the quoted
    # `configure_args`. Matching the path alone would leave a `-L` pointing at nothing.
    pattern = re.compile(
        r"\s*(?:-{1,2}[A-Za-z][^\s\"']*)?(?:" +
        "|".join(re.escape(str(directory)) for directory in directories) +
        r")[^\s\"']*"
    )
    changed = []
    for path in targets:
        text = path.read_text(encoding="utf-8", errors="replace")
        cleaned = pattern.sub("", text)
        if cleaned != text:
            path.write_text(cleaned, encoding="utf-8")
            changed.append(str(path.relative_to(tree)))

    # Whatever is left is reported rather than assumed absent: a text file this did not know to look
    # at is a finding, and a silent one would be a path shipped to a user.
    remaining = []
    for path in sorted(tree.rglob("*")):
        if not path.is_file() or path.is_symlink() or relocate.kind(path):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if any(str(directory) in text for directory in directories):
            remaining.append(str(path.relative_to(tree)))
    if remaining:
        print(f"warning: {len(remaining)} file(s) still name this build's temporary directories: "
              f"{', '.join(remaining[:10])}", file=sys.stderr)
    return changed


def strip_symbols(tree: Path, operating_system: str, borrowed: set[str]) -> str:
    """Take the debug information out of what this recipe compiled, and prove it took nothing else.

    **`borrowed` is what makes the first sentence true**, and its absence is what P5b shipped. This
    function took `relocate.machine_files(tree)`, which is every binary in the tree — including the
    ten libraries `relocate.bundle` had just copied in from the build machine. Those are the distro's
    own, and a distribution strips before it packages: the debug information lives in a separate
    `-debuginfo` package and there is nothing here to remove. The first CI run of this step said so
    twice in two lines — ``stripped lib/libcrypt.so.1 (2,101,249 -> 2,105,768, -4,519 of symbol
    table)``, a file that grew, and then ``strip --strip-all lib/libcrypto.so.3 changed 3 thing(s) a
    loader or a linker can see``. Nothing to gain, an already-packaged binary rewritten by BFD to get
    it, and a refusal to publish as the result.

    A library this recipe *built* is not borrowed even though `bundle` also copies it: OpenSSL,
    libyaml and libffi come out of `SOURCE_LIBRARIES` with their debug information intact and are
    exactly what this is for. The line between them is where the file came from, which is the same
    line `collect_licences` draws a few functions down and for a related reason — origin inside the
    work directory means this recipe made it.

    **This is the asymmetry P5a measured and did not fix**, and it is between the two halves of one
    row: RubyInstaller links with `-s` — visible in the `DLDFLAGS` its own `ruby-3.4.pc` publishes —
    so a Windows cell carries no DWARF at all, while these four ship 37.8 MB of it in a 106 MB Linux
    tree and 19.9 MB in an 81 MB macOS one. That is one version meaning two things, in the same
    direction and for the same reason as P4b's finding about CPython, and it is settled the same
    way: level down to the cell whose publisher already did this.

    Two calls rather than one, because this tree holds two kinds of file and *the instruction for
    one would destroy the other* — see :data:`strip.IMAGES` and :data:`strip.ARCHIVES`. `bin/ruby`
    and the compiled extension modules are loaded, and their symbol tables are dead weight.
    `lib/libruby*-static.a` is **linked against**, by anything embedding Ruby, which is precisely
    what P5a wrote into `keeps` — and `--strip-all` over it would produce a 7.9 MB file that
    resolves nothing and passes every test in this repository, because nothing in the tree links
    against it either. It gets `--strip-debug`, and :func:`strip.resolvable` checks the archive's
    index and every member's symbols across the operation.

    Done here rather than by configuring the build with `debugflags=`, which would be less work and
    a weaker claim. A flag that suppresses debug information is proven by nothing: the artifact is
    simply smaller, and whether it is otherwise the Ruby that Ruby's own build produces is a
    question nobody can answer afterwards. Removing the sections and then comparing everything a
    loader maps and everything a linker resolves is a claim a reader can check, on the same file, in
    either direction. macOS is smaller here than Linux for a reason `assemble` already gave: the
    `.dSYM` bundles are deleted there, which is where the compiler put each *executable's* debug
    information and is not where it put the archive's.
    """
    images = [path for path in relocate.machine_files(tree) if path.name not in borrowed]
    libraries = strip.archives(tree)

    def weigh() -> int:
        return sum(path.stat().st_size for path in images + libraries)

    was = weigh()
    strip.symbols(tree, images, strip.IMAGES[operating_system], operating_system)
    strip.symbols(tree, libraries, strip.ARCHIVES[operating_system], operating_system)
    saved = was - weigh()
    print(f"stripped {len(images)} binar{'y' if len(images) == 1 else 'ies'} and "
          f"{len(libraries)} static librar{'y' if len(libraries) == 1 else 'ies'}: "
          f"{saved:,} bytes out")
    return (f"{len(images) + len(libraries)} compiled files stripped, {saved:,} bytes of debug "
            f"information out")


def collect_licences(tree: Path, source: Path, work: Path, bundled: dict[str, Path]) -> None:
    """Ship the licence of everything in the archive: Ruby's, the three compiled here, and the rest.

    Driven by what was actually bundled rather than by the dependency list, because the two differ —
    a library nobody asked for can arrive as a dependency of a dependency. Several of these licences
    require their text to travel with the binary, so this is a condition of redistributing the
    archive rather than tidiness.
    """
    licences = tree / "licenses"
    licences.mkdir(exist_ok=True)
    for name in ("COPYING", "COPYING.ja", "LEGAL", "BSDL"):
        if (source / name).is_file():
            shutil.copy2(source / name, licences / f"ruby-{name}")
    for text in sorted(work.glob("licence-*")):
        shutil.copy2(text, licences / text.name[len("licence-"):])

    origins = []
    for name, origin in bundled.items():
        real = origin.resolve()
        if relocate.inside(real, work):
            # Its licence is already here, and where it *came from* is a temporary directory that
            # will not exist by the time anybody reads this file. What is true a year from now is
            # the recipe that produced it, and the versions are in the manifest beside this.
            origins.append(f"{name}\tbuilt from source by tools/ruby_unix.py")
            continue
        origins.append(f"{name}\t{origin}")
        label, texts = name, []
        if "/Cellar/" in str(real):
            root = real
            while root.parent.name != "Cellar" and root.parent != root:
                root = root.parent
            label = root.parent.name
            texts = sorted(root.glob("LICENSE*")) + sorted(root.glob("COPYING*"))
        else:
            owner = subprocess.run(
                ["rpm", "-qf", "--queryformat", "%{NAME}", str(real)],
                capture_output=True, text=True, timeout=120,
            )
            if owner.returncode == 0 and owner.stdout.strip():
                label = owner.stdout.strip()
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


# -------------------------------------------------------------------------------------- proof ---


def smoke(tree: Path, version: str, provides: dict[str, str]) -> dict:
    """Exercise the build from a directory it has never seen, with its libraries beside it.

    Four claims, and the first two are the ones a build machine cannot make about itself.

    *Nothing in the tree reaches outside it.* `relocate.verify` re-resolves every dependency the way
    the loader will, from the moved copy.

    *It is this Ruby, self-contained, and it can verify a certificate chain.* That is
    :mod:`ruby_smoke`, shared with the Windows recipe so the two cannot drift into meaning different
    things by the same manifest field.

    *YJIT is there and turns on.* ``--enable-yjit`` without a Rust compiler is a warning, not an
    error, so an artifact can be published with a JIT that silently is not in it — and the only
    symptom is that somebody's Rails application is slow.

    *A native gem compiles against this tree after it has moved.* This is what makes ``rbconfig.rb``
    a claim rather than a record: it holds the compiler, the flags and the header directory every
    gem with a C extension is built with, and every one of them was written on a machine whose
    directories are gone. Compiling one here is the difference between believing :func:`scrub` and
    having asked.
    """
    elsewhere = borrow.moved(tree)

    problems = relocate.verify(elsewhere)
    for problem in problems:
        print(f"error: {problem}", file=sys.stderr)
    if problems:
        raise SystemExit("the relocated tree still reaches outside itself")

    report = ruby_smoke.interpreter(elsewhere, version, provides)
    ran = ruby_smoke.commands(elsewhere, provides)

    ruby = elsewhere / provides["ruby"]
    path = borrow.clean_path(ruby.parent)
    enabled = borrow.run(ruby, "--yjit", "-e", "print RubyVM::YJIT.enabled?",
                         path=path, drop=ruby_smoke.DROP)
    if enabled != "true":
        raise SystemExit(
            f"this Ruby answers {enabled!r} to RubyVM::YJIT.enabled? under --yjit. It was configured "
            "with --enable-yjit, which warns rather than fails when there is no Rust compiler, so "
            "this is an artifact whose JIT is missing and says nothing about it."
        )
    print("YJIT enabled under --yjit")
    ran.append("ruby --yjit -e RubyVM::YJIT.enabled?")

    # Everything above runs from a directory whose name contains a space, on purpose. This one
    # cannot, and that is a finding rather than a compromise: on macOS `mkmf` points an extension at
    # the interpreter with `-bundle_loader <bindir>/ruby` and does not quote the path, so `ld` reads
    # `…/moved\ here/tree/bin/ruby` as the name of a library and cannot find it. It is upstream's
    # escaping, it has nothing to do with relocation, and it would fail the same way for a user
    # whose home directory has a space in it — which is worth knowing and is written down in
    # docs/building-from-source.md. So this step gets a second copy, somewhere the build has equally
    # never seen, without the space.
    buildable = Path(tempfile.mkdtemp(prefix="mixengine-gem-")) / "tree"
    shutil.copytree(elsewhere, buildable, symlinks=True)

    # And this is the one step that gets the *machine's* PATH rather than the cut-down one. Every
    # other check strips it so that the runner's own Ruby cannot answer for the archive; here the
    # compiler is supposed to come from the machine, because on a user's machine it will. Cutting
    # it down instead asks a question nobody asked: the manylinux image keeps its compiler in
    # `/opt/rh/gcc-toolset-14`, so `/usr/bin:/bin` produced "the compiler failed to generate an
    # executable file" — a true statement about a PATH this recipe had invented. `gem` is still
    # started by absolute path, and `mkmf` is handed `--ruby=<tree>/bin/ruby`, so there is nothing
    # here for another interpreter to answer.
    compiling = os.pathsep.join([str(buildable / "bin"), os.environ["PATH"]])

    # A default gem, deliberately: it is small, it has a C extension, and it needs nothing from the
    # machine but a compiler — so what this measures is this tree's headers and flags rather than
    # somebody's system libraries.
    try:
        borrow.run(buildable / provides["gem"], "install", "--no-document", "bigdecimal",
                   path=compiling, drop=ruby_smoke.DROP, timeout=1800)
    except SystemExit:
        # mkmf writes down why it gave up and then the temporary tree is deleted, which is how a
        # compile failure here reads as "extconf failed" and nothing else. It is quoted instead.
        for log in sorted(buildable.rglob("mkmf.log")):
            print(f"--- {log}", file=sys.stderr)
            print(log.read_text(encoding="utf-8", errors="replace")[-4000:], file=sys.stderr)
        raise
    compiled = sorted((buildable / "lib" / "ruby" / "gems").glob("*/extensions/*/*/bigdecimal-*"))
    if not compiled:
        raise SystemExit(
            "gem install bigdecimal reported success and left no compiled extension in the tree; "
            "the gem home is not where `gem env gemdir` said it was"
        )
    print(f"compiled a native gem from the moved tree: {compiled[0].name}")
    ran.append(f"{provides['gem']} install bigdecimal (native extension, from the moved tree)")

    shutil.rmtree(buildable.parent, ignore_errors=True)
    borrow.discard(elsewhere)
    return {"relocated": True, "ran": ran, "openssl": report["openssl"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--version", required=True,
        help="exact version (3.4.10), a line (3.4) for its newest release, or 'latest'",
    )
    parser.add_argument("--out", default=Path("dist"), type=Path)
    arguments = parser.parse_args()

    operating_system, arch = borrow.host("Ruby")
    if operating_system == "windows":
        raise SystemExit(
            "this recipe compiles Ruby, and Windows does not need compiling: RubyInstaller "
            "publishes relocatable archives for both architectures. Use tools/ruby.py."
        )

    version, url, digest = resolve(arguments.version)
    print(f"{arguments.version} resolves to Ruby {version} ({operating_system}/{arch})")
    eol.announce("ruby", version)

    work = Path(tempfile.mkdtemp(prefix="mixengine-ruby-"))
    extra = work / "deps"
    extra.mkdir(parents=True)
    os.environ["PATH"] = f"{extra / 'bin'}{os.pathsep}{os.environ['PATH']}"

    rust = ensure_rust(work)
    dependencies(work, extra)
    bundle, bundle_digest, bundle_date = ca_bundle(work)
    source = source_tree(work, version, url, digest)

    prefix = Path("/opt/mixengine") / f"ruby-{version}"
    try:
        prefix.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        run("sudo", "mkdir", "-p", str(prefix))
        run("sudo", "chown", "-R", str(os.getuid()), "/opt/mixengine")

    build(source, prefix, extra, build_environment(extra))
    tree, provides, rewritten = assemble(prefix, work, bundle)

    bundled = relocate.bundle(tree, search=[extra / "lib"])
    print(f"bundled {len(bundled)} librar{'y' if len(bundled) == 1 else 'ies'}: "
          f"{', '.join(bundled)}")
    collect_licences(tree, source, work, bundled)
    # Both spellings of the same directory: macOS hands out `/var/folders/…` and resolves it to
    # `/private/var/folders/…`, and a build system records whichever one it was given.
    scrub(tree, sorted({work, work.resolve()}))
    # After `relocate.bundle`, which rewrites the load paths of these same files, and before the
    # smoke test, which has to exercise the tree that ships rather than the one that was built.
    #
    # What came off the build machine rather than out of this build. `relocate.bundle` answers with
    # where each library was copied from, so the two kinds are already told apart here — see
    # `strip_symbols` for why stripping the borrowed ones gains nothing and costs the build.
    borrowed = {name for name, origin in bundled.items()
                if not relocate.inside(origin.resolve(), work)}
    stripped = strip_symbols(tree, operating_system, borrowed)

    libraries = ", ".join(
        f"{name} {recipe['version']}" for name, recipe in SOURCE_LIBRARIES.items()
    )
    recipe = (
        f"ruby-src {version} from source (sha256 {digest[:12]}…, as published in "
        f"cache.ruby-lang.org/pub/ruby/index.txt); {libraries}, openssl resolving its default "
        f"certificate paths beside itself; YJIT with {rust or 'a rust toolchain'}; "
        f"{len(bundled)} bundled libraries; CA bundle from curl.se dated {bundle_date}, "
        f"sha256 {bundle_digest[:12]}…; {stripped}"
    )
    if rewritten:
        recipe += f"; {len(rewritten)} interpreter path(s) rewritten to be relative"

    manifest = {
        "schema": 1,
        "kind": "ruby",
        "version": version,
        "os": operating_system,
        "arch": arch,
        "source": "built",
        "recipe": recipe,
        "provides": provides,
    }
    # Asked on this side too, and answering nothing. That is the point of asking: `lacks` present on
    # two cells and absent on four says the four were considered, where a field only one recipe has
    # ever heard of says nothing about the other. These are the cells that *have* YJIT and that
    # compile a native gem — both proven below rather than claimed here.
    absent = ruby_parity.lacks(operating_system)
    if absent:
        manifest["lacks"] = absent
    # The first *built* artifact in this repository to go through `borrow.declare`, and it is here
    # for the one claim that is not about a publisher: `keeps` states a difference from this
    # repository's own rule, which a compiled cell can be as far from as a borrowed one. Nothing is
    # added or removed relative to an upstream archive because there is no upstream archive — what
    # is declared is `lib/libruby*-static.a`, 41.4 MB on Linux and the largest file in the tree,
    # which `--disable-shared` produces and `rbconfig.rb` sends every embedder to. See
    # `ruby_parity.keeps`; it checks the file is there before the claim is written.
    borrow.declare(tree, manifest, keeps=ruby_parity.keeps(tree, operating_system))
    measured = relocate.floor(tree)
    if measured:
        manifest["requires"] = {measured[0]: measured[1]}
        print(f"needs {measured[0]} {measured[1]} or newer")
    manifest["smoke"] = smoke(tree, version, provides)

    borrow.publish(tree, manifest, arguments.out, "tar")
    shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
