#!/usr/bin/env python3
"""Compile MySQL 5.6 and 5.7 for the four Unix cells upstream stopped publishing.

**Oracle withdrew macOS from both lines while they were alive.** `5.7.31` offers
`macos10.14-x86_64`, `5.7.20` offers `macos10.12-x86_64`, and `5.7.44` — the last release of the
line — offers no macOS asset of any kind and lists no macOS entry in its own operating-system menu.
5.6 does the same thing earlier. Neither line has ever had an ARM build, on any system. So four of
six cells have nothing to borrow, and they are compiled here.

**All four, and that is a decision rather than a shortage.** Upstream still publishes
`linux-glibc2.12-x86_64` for both lines, so that cell *could* be borrowed. It is not. The ARM cell
has to be compiled — there is nothing to borrow — which means a 2026 toolchain against an OpenSSL
this repository supplies, while the borrowed tarball is Oracle's 2021 build against whatever it
linked then, at a glibc floor of 2.12 against the built cell's 2.28. Two Linux artifacts of
`5.6.51` would be two different databases, and `parity.py` compares finished artifacts precisely
because that difference is invisible in two green builds. This is the first row in this repository
where *borrow before you build* loses to *one version means one thing*, and it is worth saying in
those terms: borrowing is cheaper per cell, and it is not cheaper than having the six cells of a
version mean one thing.

Two things follow that no other compiled recipe here has had to deal with, and both have their own
function below: **5.6 will not build for arm64 without a patch**, and **5.6 will not accept a
maintained OpenSSL**. The patch is upstream's own later change carried back one line, and the
modified source is published beside the binaries because MySQL Community is GPLv2.

Python 3 stdlib only, by policy.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import borrow
import mysql
import mysql_smoke
import relocate
import strip

# MySQL 5.6's own CMake decides this, and it is worth stating as a measurement rather than as
# history: `cmake/ssl.cmake` in 5.6.51 sets `OPENSSL_FOUND` only when `OPENSSL_MAJOR_VERSION
# STREQUAL "1"`, while 5.7.44 accepts `"1" OR "3"`. So 5.7 compiles against the OpenSSL every
# machine already has, and 5.6 needs a 1.1.1 this repository builds and bundles.
#
# **1.1.1 stopped receiving public security fixes in September 2023.** `smoke.openssl` on the four
# compiled 5.6 cells therefore names a TLS library nobody patches, which belongs in the artifact and
# on the page rather than in a comment. It is not a reason to refuse the version: a version whose
# own build system rejects a maintained OpenSSL cannot be given one, and the person maintaining an
# application against MySQL 5.6 is exactly who a local development environment is for.
OPENSSL_FOR_56 = {
    "url": "https://github.com/openssl/openssl/releases/download/OpenSSL_1_1_1w/openssl-1.1.1w.tar.gz",
    "sha256": "cf3098950cb4d853ad95c0841f1f9c6d3dc102dccfcacd521d93925208b76ac8",
    "version": "1.1.1w",
}

# The last CMake that will configure a MySQL 5.x tree at all, pinned and fetched the way the
# OpenSSL above is.
#
# **`-DCMAKE_POLICY_VERSION_MINIMUM=3.5` is not enough, and that is worth stating because it looks
# as though it should be.** That option answers a tree whose `CMAKE_MINIMUM_REQUIRED` is too old.
# 5.6 and 5.7 do something else as well: they ask for OLD behaviour on CMP0018, CMP0022, CMP0042 and
# CMP0045 *by name*, and CMake 4 replies `Policy CMP0018 may not be set to OLD behavior because this
# version of CMake no longer supports it` — four errors, before a single file is compiled, on macOS
# and inside the manylinux container alike.
#
# So the choice was to edit upstream's `CMakeLists.txt` or to bring a CMake that still speaks to it.
# Editing it would mean turning four compatibility settings a 2013-era build system asked for into
# whatever today's default happens to be, guessing at each one; this repository patches source only
# where upstream itself made the same change later (see :func:`patch_universal_binary`). An old tree
# gets an old tool. 3.31 is the last 3.x line, and it is also the line that introduced
# `CMAKE_POLICY_VERSION_MINIMUM`, so both halves of the problem are answered by one download.
CMAKE_FOR_5X = {
    "version": "3.31.12",
    "assets": {
        "linux-x86_64": (
            "0dc2e9a6860f06bf10bd8fadc03e35d9eeb4df46e33763a7e480e987758f385c", "bin/cmake",
        ),
        "linux-aarch64": (
            "83f8fd91d2038a56556e1400390fcfe42f79602940c494f6c6f1cdae7f9e7f40", "bin/cmake",
        ),
        "macos-universal": (
            "799af7fd545db9bf1b9cfe72f8095880e727a2d4e0df0e3dffc3bc7b95c2d3b0",
            "CMake.app/Contents/bin/cmake",
        ),
    },
}

# What Homebrew is asked for. `bison` is not decoration: Apple ships 2.3 in /usr/bin and MySQL's
# grammar needs 2.7 or newer, so the Homebrew one goes on the PATH ahead of it.
BREW_PACKAGES = ("bison", "openssl@3", "ncurses", "pkg-config")

# What AlmaLinux 8 is asked for. A named list rather than a `dnf group`, so a reader can check it
# against the configure flags. `perl` is there because OpenSSL's build is written in it.
DNF_PACKAGES = (
    # No `cmake`: the one this build runs is pinned and downloaded, because a current one
    # refuses these trees. See `CMAKE_FOR_5X`.
    "gcc", "gcc-c++", "make", "bison", "ncurses-devel", "perl", "perl-IPC-Cmd",
    "patchelf", "binutils", "zlib-devel", "openssl-devel", "libtirpc-devel", "rpcgen",
)

# The block that stops MySQL 5.6 compiling on any machine Apple sells today, and on any ARM Linux.
# Matched at both ends and deleted whole; see :func:`patch_universal_binary` for why this is
# upstream's change rather than this repository's invention.
DARWIN_BLOCK_OPENS = "#if defined(__APPLE__) && defined(__MACH__)\n#  undef SIZEOF_CHARP"
DARWIN_BLOCK_CLOSES = "#endif /* defined(__APPLE__) && defined(__MACH__) */"

# The other block 5.7 deleted, four lines long, and the reason it matters is one word in it:
# `defined`. It asks whether `TARGET_OS_LINUX` **exists**, meaning to ask whether it is 1 — and
# Apple's `TargetConditionals.h` has since grown a `TARGET_OS_LINUX`, defined as 0. So a macOS SDK
# new enough to carry it turns `_GNU_SOURCE` on for a platform that is not GNU, `mysys/my_error.c`
# takes its `#elif defined _GNU_SOURCE` branch, and `char *r = strerror_r(...)` meets the POSIX
# `strerror_r` that answers `int`.
#
# It is the same shape as the zlib clash `-DWITH_ZLIB=system` answers, and it splits the two macOS
# cells the same way: SDK 15.5 defines `TARGET_OS_LINUX`, SDK 14.5 does not, so Xcode 16.4 fails
# and Xcode 15.4 compiles it — with a warning, into a build where a failing `my_strerror` would
# read a pointer that is really a zero. **5.7.44 does not have the block at all**; on Linux
# `_GNU_SOURCE` comes from `my_config.h`, which 5.6 generates too, so deleting it changes nothing
# there and stops lying about what macOS is.
GNU_SOURCE_BLOCK = """/* Fix problem with S_ISLNK() on Linux */
#if defined(TARGET_OS_LINUX) || defined(__GLIBC__)
#undef  _GNU_SOURCE
#define _GNU_SOURCE 1
#endif
"""


def run(*command: str, cwd: Path | None = None, env: dict | None = None,
        capture: bool = False, timeout: int = 14400) -> str:
    print("$ " + " ".join(str(part) for part in command), flush=True)
    result = subprocess.run(
        [str(part) for part in command], cwd=cwd, env=env, text=True, timeout=timeout,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )
    if result.returncode != 0:
        if capture:
            sys.stdout.write(result.stdout or "")
            sys.stderr.write(result.stderr or "")
        raise SystemExit(f"{command[0]} exited {result.returncode}")
    return result.stdout or ""


def attempt(*command: str, timeout: int = 3600) -> bool:
    """Run something whose failure is not fatal — a package that is already installed, say."""
    print("$ " + " ".join(str(part) for part in command), flush=True)
    try:
        return subprocess.run(
            [str(part) for part in command], capture_output=True, timeout=timeout
        ).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def jobs() -> str:
    return str(os.cpu_count() or 2)


def source(version: str, work: Path) -> tuple[Path, str, str, str]:
    """Fetch the source release, verify its signature, unpack it, and answer with where it is."""
    program, home = mysql.keyring(work)
    name = mysql.source_asset(version)
    if name is None:
        raise SystemExit(f"upstream publishes no source tarball for MySQL {version}")
    tarball, digest, url, fingerprint = mysql.download(program, home, name, version, work)
    with tarfile.open(tarball) as archive:
        archive.extractall(work, filter="data")
    unpacked = work / f"mysql-{version}"
    if not (unpacked / "CMakeLists.txt").is_file():
        raise SystemExit(f"{unpacked} has no CMakeLists.txt; this is not a release tarball")
    return unpacked, digest, url, fingerprint


def patch_universal_binary(source_tree: Path) -> dict[str, str]:
    """Delete the block that stops MySQL 5.6 compiling on any machine Apple sells today.

    ``include/my_global.h`` carries a Darwin block written for PowerPC-era universal binaries. It
    ``#undef``s the ``SIZEOF_*`` values CMake has just detected correctly and hardcodes them again
    from ``__i386__ / __ppc__ / __x86_64__ / __ppc64__``, ending in
    ``#error Building FAT binary for an unknown architecture.`` On Apple Silicon that ``#error`` is
    the whole of the failure; nothing else in 5.6 is x86-bound.

    **The fix is Oracle's own.** 5.7.44 does not have the block — it was deleted upstream and the
    detected values left to stand — so what happens here is upstream's later change carried back one
    line, not a port invented in this repository, and it is checkable with two files at tags
    ``mysql-5.6.51`` and ``mysql-5.7.44``. MacPorts' still-maintained ``mysql56`` reaches the same
    place by adding ``__aarch64__`` to the ``#elif``; that is a diagnosis worth having, it is not
    the source, and no byte of it enters an artifact.

    Guarded the way ``ruby_unix.relative_cert_defaults`` guards its OpenSSL patch: found exactly
    once, or the build stops. An upstream that changed this file is a build that fails loudly rather
    than an artifact that ships quietly.
    """
    path = source_tree / "include" / "my_global.h"
    text = path.read_text(encoding="utf-8")
    opens, closes = text.count(DARWIN_BLOCK_OPENS), text.count(DARWIN_BLOCK_CLOSES)
    if opens != 1 or closes != 1:
        raise SystemExit(
            f"{path} does not contain exactly one Darwin universal-binary block ({opens} openings, "
            f"{closes} closings). MySQL has changed the file this recipe deletes a block from, and "
            f"guessing here would compile the wrong SIZEOF_* values into a database."
        )
    start = text.index(DARWIN_BLOCK_OPENS)
    end = text.index(DARWIN_BLOCK_CLOSES, start) + len(DARWIN_BLOCK_CLOSES)
    path.write_text(text[:start] + text[end:], encoding="utf-8")
    print("patched include/my_global.h: removed the universal-binary block 5.7 removed upstream")
    return {
        "include/my_global.h":
            "removed the __APPLE__ universal-binary block, which undoes CMake's detected SIZEOF_* "
            "values, hardcodes them per architecture from __i386__/__ppc__/__x86_64__/__ppc64__ and "
            "#errors on anything else, arm64 included. MySQL 5.7.44 does not carry the block; this "
            "is that deletion, applied to 5.6.",
    }


def patch_version_file(source_tree: Path) -> dict[str, str]:
    """Rename the tree's ``VERSION`` file out of the way of the C++ header of the same name.

    5.6 and 5.7 keep their version numbers in a file called ``VERSION`` at the root of the tree, and
    the root of the tree is on the include path. On a case-insensitive filesystem — which is what
    macOS gives you unless somebody went out of their way — libc++'s ``iosfwd`` doing
    ``#include <version>`` opens *that* file, and clang reports ``expected unqualified-id`` on the
    line ``MYSQL_VERSION_MAJOR=5``. Nothing in MySQL is wrong and nothing in libc++ is wrong; the
    two names collide, and the collision arrived years after both files were written.

    **The fix is Oracle's own.** 8.0.28 renamed the file to ``MYSQL_VERSION`` for exactly this
    reason, so this is upstream's later change carried back, the way
    :func:`patch_universal_binary` carries one back. Both references live in one file and are
    counted before anything is written: a tree that spells this differently stops the build rather
    than silently configuring against a version file nothing reads.
    """
    reference = "${CMAKE_SOURCE_DIR}/VERSION"
    versions = source_tree / "cmake" / "mysql_version.cmake"
    text = versions.read_text(encoding="utf-8")
    if text.count(reference) != 2 or not (source_tree / "VERSION").is_file():
        raise SystemExit(
            f"{versions} names {reference} {text.count(reference)} times and this recipe expects "
            f"two, or {source_tree / 'VERSION'} is not there. MySQL has changed how it reads its "
            f"own version, and renaming the file underneath that would configure a build against a "
            f"version nothing states."
        )
    versions.write_text(text.replace(reference, "${CMAKE_SOURCE_DIR}/MYSQL_VERSION"),
                        encoding="utf-8")
    (source_tree / "VERSION").rename(source_tree / "MYSQL_VERSION")
    print("renamed VERSION to MYSQL_VERSION: on a case-insensitive filesystem it answers "
          "#include <version>")
    return {
        "VERSION":
            "renamed to MYSQL_VERSION. The source root is on the include path, and on a "
            "case-insensitive filesystem a file called VERSION is what `#include <version>` finds "
            "— libc++'s own `iosfwd` includes that header, so every C++ file in the tree failed to "
            "compile. MySQL 8.0.28 renamed the same file for the same reason.",
        "cmake/mysql_version.cmake":
            "reads MYSQL_VERSION rather than VERSION, in the two places it names the file.",
    }


def patch_gnu_source(source_tree: Path) -> dict[str, str]:
    """Delete the block that tells a macOS SDK it is GNU. See :data:`GNU_SOURCE_BLOCK`."""
    path = source_tree / "include" / "my_global.h"
    text = path.read_text(encoding="utf-8")
    if text.count(GNU_SOURCE_BLOCK) != 1:
        raise SystemExit(
            f"{path} does not contain exactly one _GNU_SOURCE block "
            f"({text.count(GNU_SOURCE_BLOCK)} found). MySQL has changed the lines this recipe "
            f"deletes, and a guess here decides "
            f"which strerror_r a database compiles against."
        )
    path.write_text(text.replace(GNU_SOURCE_BLOCK, ""), encoding="utf-8")
    print("patched include/my_global.h: removed the _GNU_SOURCE block 5.7 removed upstream")
    return {
        "include/my_global.h (the _GNU_SOURCE block)":
            "removed the block defining _GNU_SOURCE on `defined(TARGET_OS_LINUX) || "
            "defined(__GLIBC__)`. Apple's TargetConditionals.h now defines TARGET_OS_LINUX as 0, "
            "which that test reads as yes, so mysys/my_error.c took its GNU branch and assigned "
            "the int that POSIX strerror_r returns to a char *. MySQL 5.7.44 does not have the "
            "block; on Linux _GNU_SOURCE comes from my_config.h, which 5.6 generates as well.",
    }


def brew_prefix(formula: str) -> Path | None:
    result = subprocess.run(["brew", "--prefix", formula], capture_output=True, text=True,
                            timeout=300)
    prefix = Path(result.stdout.strip()) if result.stdout.strip() else None
    return prefix if result.returncode == 0 and prefix and prefix.is_dir() else None


def dependencies() -> dict[str, Path]:
    """Install what this machine needs to compile a MySQL, and answer with where the parts are."""
    if sys.platform == "darwin":
        for package in BREW_PACKAGES:
            attempt("brew", "install", package)
        found = {name: brew_prefix(name) for name in ("bison", "openssl@3", "ncurses")}
        missing = [name for name, prefix in found.items() if prefix is None]
        if missing:
            raise SystemExit(f"Homebrew has no {', '.join(missing)}, and this build needs each")
        # Ahead of /usr/bin, where Apple's bison 2.3 is — old enough that MySQL's grammar does not
        # compile with it, and quiet enough about it that the failure looks like a syntax error in
        # sql_yacc.yy.
        os.environ["PATH"] = f"{found['bison'] / 'bin'}{os.pathsep}{os.environ['PATH']}"
        return {name: prefix for name, prefix in found.items() if prefix}

    for enabling in (["dnf", "config-manager", "--set-enabled", "powertools"],
                     ["dnf", "config-manager", "--set-enabled", "crb"]):
        attempt(*enabling)
    for package in DNF_PACKAGES:
        attempt("dnf", "install", "-y", package)
    return {}


def cmake_3(work: Path) -> str:
    """Fetch the pinned CMake and answer with the program to run, verified by hash.

    Kitware publishes no detached signature for these archives and does publish a SHA-256 file
    beside them, which is what `CMAKE_FOR_5X` carries: the check is against a digest recorded in
    this repository, not against one fetched next to the download and therefore worth nothing.
    """
    _, arch = borrow.host("MySQL")
    key = "macos-universal" if sys.platform == "darwin" else f"linux-{arch}"
    if key not in CMAKE_FOR_5X["assets"]:
        raise SystemExit(f"no pinned CMake for {key}, and MySQL 5.x will not configure without one")
    expected, program = CMAKE_FOR_5X["assets"][key]
    version = CMAKE_FOR_5X["version"]
    name = f"cmake-{version}-{key}.tar.gz"
    url = f"https://github.com/Kitware/CMake/releases/download/v{version}/{name}"

    directory = work / "cmake"
    directory.mkdir(parents=True, exist_ok=True)
    tarball = directory / name
    print(f"fetching {url}")
    tarball.write_bytes(borrow.fetch(url, timeout=900))
    actual = borrow.sha256(tarball)
    if actual != expected:
        raise SystemExit(f"{name} hashes to {actual}, and this recipe pins {expected}")
    print(f"sha256 {actual} (pinned in tools/mysql_build.py)")
    with tarfile.open(tarball) as archive:
        archive.extractall(directory, filter="data")
    unpacked = next(item for item in sorted(directory.iterdir()) if item.is_dir())
    found = unpacked / program
    if not found.is_file():
        raise SystemExit(f"{name} does not hold {program}; Kitware has changed its layout")
    found.chmod(found.stat().st_mode | 0o755)
    return str(found)


def build_openssl(work: Path) -> Path:
    """Compile the OpenSSL 1.1.1 that MySQL 5.6's own CMake will accept, and answer with its prefix.

    ``ruby_unix.build_library``'s shape, one branch older. ``--libdir=lib`` because OpenSSL installs
    into ``lib64`` on a 64-bit Linux otherwise and everything downstream of this looks in ``lib``;
    ``install_sw`` rather than ``install`` is the difference between three minutes and twenty, the
    rest being documentation nothing here reads.
    """
    directory = work / "openssl"
    directory.mkdir(parents=True, exist_ok=True)
    tarball = directory / OPENSSL_FOR_56["url"].rsplit("/", 1)[-1]
    print(f"fetching {OPENSSL_FOR_56['url']}")
    tarball.write_bytes(borrow.fetch(OPENSSL_FOR_56["url"], timeout=900))
    actual = borrow.sha256(tarball)
    if actual != OPENSSL_FOR_56["sha256"]:
        raise SystemExit(
            f"{tarball.name} hashes to {actual}, and this recipe pins {OPENSSL_FOR_56['sha256']}"
        )
    print(f"sha256 {actual} (pinned in tools/mysql_build.py)")
    with tarfile.open(tarball) as archive:
        archive.extractall(directory, filter="data")
    unpacked = next(path for path in sorted(directory.iterdir()) if path.is_dir())

    prefix = work / "openssl-prefix"
    environment = {**os.environ}
    if sys.platform == "darwin":
        # A Mach-O whose load commands were packed tight cannot have its install names rewritten at
        # all — only the linker can leave room, and finding that out afterwards costs the build.
        environment["LDFLAGS"] = (
            "-Wl,-headerpad_max_install_names " + environment.get("LDFLAGS", "")
        ).strip()
    # `no-docs` is **not** passed, and the omission is load-bearing rather than an oversight:
    # OpenSSL grew that option in 3.0, and 1.1.1's own `./config` answers `***** Unsupported
    # options: no-docs` and exits 255 without writing a makefile. Nothing is lost by leaving it out
    # — `install_sw` below is what skips the documentation, and it skips it either way.
    run("./config", f"--prefix={prefix}", f"--openssldir={prefix}/ssl", "--libdir=lib",
        "shared", "no-tests", cwd=unpacked, env=environment)
    run("make", f"-j{jobs()}", cwd=unpacked, env=environment)
    run("make", "install_sw", cwd=unpacked, env=environment)

    for text in sorted(unpacked.glob("LICENSE*")):
        shutil.copy2(text, work / f"licence-openssl-{text.name}")
    return prefix


def configure(source_tree: Path, build: Path, prefix: Path, line: str,
              found: dict[str, Path], ssl: Path | None, work: Path, program: str) -> list[str]:
    """Ask CMake for a standalone server, and answer with what it was asked."""
    arguments = [
        program, str(source_tree),
        f"-DCMAKE_INSTALL_PREFIX={prefix}",
        "-DCMAKE_BUILD_TYPE=RelWithDebInfo",
        "-DINSTALL_LAYOUT=STANDALONE",
        "-DWITH_UNIT_TESTS=OFF",
        "-DWITH_EMBEDDED_SERVER=OFF",
        # 5.6's CMakeLists.txt says `CMAKE_MINIMUM_REQUIRED(VERSION 2.6)`, which the pinned CMake
        # deprecates rather than refuses. Passed anyway: it is the option `CMAKE_FOR_5X` exists to
        # be able to pass, it costs nothing, and it says in the arguments — which go into the
        # artifact's own record of how it was configured — that this tree needed it.
        "-DCMAKE_POLICY_VERSION_MINIMUM=3.5",
        # **The standard these trees ask for and then throw away.** InnoDB's `univ.i` opens with
        # `#define byte unsigned char`, which was harmless when a compiler's default was C++98 and
        # is not now: `<cstddef>` declares `enum class byte : unsigned char` from C++17 on, the
        # macro rewrites the declaration, and GCC 14 stops with `unnamed scoped enum is not
        # allowed` in a header nobody here wrote.
        #
        # 5.6 knows this. `cmake/build_configurations/compiler_options.cmake` computes
        # `-std=gnu++03` for GCC 6 and newer — and the very next line is an unconditional
        # `SET(COMMON_CXX_FLAGS "-g -fabi-version=2 ...")` that overwrites what it just computed.
        # 5.7 fixed that same file by prepending instead of setting. So this is not a standard
        # chosen here: it is the one both trees choose and only one of them manages to keep, put
        # somewhere an overwrite cannot reach.
        #
        # On clang too, where upstream applies it only on Linux. The clash is a property of the
        # compiler's *default standard* rather than of the compiler, and Apple's has moved as well.
        "-DCMAKE_CXX_FLAGS=-std=gnu++03",
    ]
    if line == "5.7":
        # 5.7 needs Boost 1.59 exactly and refuses to look for it anywhere else. Letting its own
        # build fetch it is upstream's documented route, and pinning a copy here would be this
        # repository maintaining a mirror of a dependency it does not otherwise touch.
        boost = work / "boost"
        boost.mkdir(parents=True, exist_ok=True)
        arguments += [f"-DWITH_BOOST={boost}", "-DDOWNLOAD_BOOST=1"]
    if ssl is not None:
        arguments.append(f"-DWITH_SSL={ssl}")
    elif sys.platform == "darwin":
        arguments.append(f"-DWITH_SSL={found['openssl@3']}")
    else:
        arguments.append("-DWITH_SSL=system")

    if sys.platform == "darwin":
        arguments += [
            # **The bundled zlib is older than the SDK it is compiled against.** 5.6 carries a zlib
            # of 2013 whose `zutil.h` still has a branch for *classic* Mac OS, taken on
            # `defined(MACOS) || defined(TARGET_OS_MAC)` — and `TARGET_OS_MAC` is 1 on every Apple
            # platform today. The branch does `#define fdopen(fd,mode) NULL`, so the next
            # `#include <stdio.h>` reaches `FILE *fdopen(int, const char *)` with `fdopen` already a
            # macro and clang stops inside Apple's own header. Measured: the x86_64 cell (Xcode
            # 16.4, SDK 15.5) fails there and the arm64 one (Xcode 15.4, SDK 14.5) does not, a
            # difference between two SDKs rather than between two architectures, and a cell that
            # depends on which runner image was current is one that breaks later for no reason.
            #
            # macOS has shipped zlib in `/usr/lib` since forever, `relocate` leaves anything there
            # alone, and it is maintained — which the copy inside a 2013 source tree is not. Linux
            # keeps the bundled one: it compiles, and taking the system's would add a library to
            # bundle for no gain.
            "-DWITH_ZLIB=system",
            f"-DBISON_EXECUTABLE={found['bison'] / 'bin' / 'bison'}",
            "-DCMAKE_SHARED_LINKER_FLAGS=-Wl,-headerpad_max_install_names",
            "-DCMAKE_EXE_LINKER_FLAGS=-Wl,-headerpad_max_install_names",
        ]
    build.mkdir(parents=True, exist_ok=True)
    run(*arguments, cwd=build)
    return arguments[1:]


def compile_and_install(build: Path, program: str) -> None:
    run(program, "--build", str(build), "--parallel", jobs(), cwd=build)
    run(program, "--install", str(build), cwd=build)


def assemble(prefix: Path, work: Path) -> tuple[Path, list[str]]:
    """Copy the install prefix into a tree, and cut it down to what an artifact ships."""
    tree = work / "tree"
    shutil.copytree(prefix, tree, symlinks=True)
    for symbols in sorted(tree.rglob("*.dSYM")):
        shutil.rmtree(symbols, ignore_errors=True)
    removed = mysql.prune(tree)
    if removed:
        print(f"not shipping {len(removed)} paths: {', '.join(removed)}")
    return tree, removed


def patched_source(source_tree: Path, version: str, out: Path) -> Path:
    """Publish the source these binaries were actually built from, beside them.

    Every compiled cell in this repository so far — PHP, Ruby, MariaDB, Redis, memcached, nginx — is
    upstream's source unmodified, so "the source is upstream's, at this URL, with this sha256" has
    been a complete answer. MySQL 5.6 is the first artifact here built from **modified** source, and
    MySQL Community is GPLv2: the corresponding source has to travel with the binary rather than be
    describable on request.

    The route is ``relocate.cygwin_source_note``'s, one obligation stronger. The patched tree is
    packed and uploaded as an asset of the same release, and the artifact carries a file naming it,
    the upstream tarball it came from and what was changed. **That asset joins the archive's
    permanence promise like every other** — a deleted source tarball here is a licence violation
    rather than a missing convenience — and the patch is small enough that the difference between
    the two tarballs is readable, which is the point of shipping both.
    """
    out.mkdir(parents=True, exist_ok=True)
    packed = out / f"mysql-{version}-patched-src.tar.gz"
    run("tar", "-czf", str(packed), "-C", str(source_tree.parent), source_tree.name,
        timeout=3600)
    print(f"published the patched source as {packed.name} ({packed.stat().st_size:,} bytes)")
    return packed


SOURCE_NOTE = """\
# The source these binaries were built from

MySQL Community Server is distributed under the GNU General Public License, version 2. These
binaries were compiled from upstream's release tarball **with the changes listed below**, so the
complete
corresponding source is published as an asset of this release rather than offered on request:

    mysql-{version}-patched-src.tar.gz

It was made from

    {url}

(sha256 {digest}), verified against a detached PGP signature from {who}, by applying:

{changes}

`diff -ru` between upstream's tarball and that one is the whole of the change. Nothing else in the
tree was touched.

MySQL's own licence is in `licenses/mysql-COPYING`. Libraries bundled beside these binaries carry
their own terms, in the files beside this one.
"""


def collect_licences(tree: Path, source_tree: Path, work: Path, bundled: dict[str, Path],
                     note: str) -> None:
    """Put MySQL's terms, the bundled libraries' terms and the source offer where a reader looks."""
    licences = tree / "licenses"
    licences.mkdir(exist_ok=True)
    for name in ("COPYING", "LICENSE", "README"):
        if (source_tree / name).is_file():
            shutil.copy2(source_tree / name, licences / f"mysql-{name}")
    for text in sorted(work.glob("licence-openssl-*")):
        shutil.copy2(text, licences / text.name.replace("licence-", ""))
    relocate.bundled_licences(tree, bundled)
    (licences / "SOURCE.md").write_text(note, encoding="utf-8")


def openssl_note(tree: Path, provides: dict[str, str], built: str | None) -> str:
    """What TLS library this server will actually load, read off the server rather than assumed."""
    mysqld = tree / provides["mysqld"]
    names = sorted({
        Path(spelling).name
        for spelling, _ in relocate.dependencies(
            mysqld, (tree / "bin"), relocate.loader_search(tree))
        if "ssl" in Path(spelling).name or "crypto" in Path(spelling).name
    })
    if not names:
        return "linked statically; mysqld names no TLS library at all"
    where = f"OpenSSL {built}, built and bundled by this recipe" if built else "the system's OpenSSL"
    return f"{', '.join(names)} ({where})"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True,
                        help="an exact version (5.6.51). Not a line: tools/mysql.py --plan "
                             "resolves those, once, for every cell at the same time.")
    parser.add_argument("--out", default=Path("dist"), type=Path)
    arguments = parser.parse_args()

    version = arguments.version.strip()
    if len(version.split(".")) != 3:
        raise SystemExit(f"{version} is a line rather than a version; pass what --plan printed")
    line = ".".join(version.split(".")[:2])
    if line not in mysql.BUILT_LINES:
        raise SystemExit(
            f"upstream publishes a binary for every cell of MySQL {line}; compiling one here would "
            f"be a pipeline maintained for every security release in place of a download. Use "
            f"tools/mysql_borrow.py."
        )

    operating_system, arch = borrow.host("MySQL")
    if operating_system == "windows":
        raise SystemExit(
            "the Windows x86_64 cell of 5.6 and 5.7 is upstream's own zip — use "
            "tools/mysql_borrow.py — and Windows on ARM64 is empty at every version of every line."
        )

    work = Path(tempfile.mkdtemp(prefix="mixengine-mysql-"))
    found = dependencies()
    source_tree, digest, url, fingerprint = source(version, work)
    print(f"MySQL {version} for {operating_system}/{arch}")

    changed = patch_version_file(source_tree)
    if line == "5.6":
        changed.update(patch_universal_binary(source_tree))
        changed.update(patch_gnu_source(source_tree))
    ssl = build_openssl(work) if line == "5.6" else None

    prefix = work / "prefix"
    program = cmake_3(work)
    asked = configure(source_tree, work / "build", prefix, line, found, ssl, work, program)
    compile_and_install(work / "build", program)

    tree, removed = assemble(prefix, work)
    # Before the bundling, for `strip.debug`'s own reason, and recorded in `recipe` rather than
    # in `upstream.changed`: nothing here is upstream's file to differ from.
    stripped = strip.debug(tree)
    provides = mysql_smoke.describe(tree, windows=False)

    search = [ssl / "lib"] if ssl else []
    if sys.platform == "darwin" and "openssl@3" in found:
        search.append(found["openssl@3"] / "lib")
    bundled = relocate.bundle(tree, search=search)
    print(f"bundled {len(bundled)} librar{'y' if len(bundled) == 1 else 'ies'}: "
          f"{', '.join(sorted(bundled)) or 'none'}")
    # After the bundling, never before it: every plugin here names an OpenSSL that lives outside the
    # tree until `bundle` has run, so the early question deletes what the recipe just built.
    removed += mysql.unloadable_libraries(tree)

    who = next(file for file, group in mysql.FINGERPRINTS.items() if fingerprint in group)
    note = SOURCE_NOTE.format(
        version=version, url=url, digest=digest, who=f"{who} ({fingerprint})",
        changes="\n".join(f"* `{path}` — {why}" for path, why in sorted(changed.items()))
        or "* nothing; this line needed no patch, and the tarball is upstream's unchanged",
    )
    collect_licences(tree, source_tree, work, bundled, note)

    manifest = {
        "schema": 1,
        "kind": "mysql",
        "version": version,
        "os": operating_system,
        "arch": arch,
        "source": "built",
        "recipe": (
            f"{source_tree.name}.tar.gz from source (sha256 {digest[:12]}…, verified against a "
            f"detached PGP signature from {who}); cmake "
            + " ".join(part for part in asked if part.startswith("-D"))
            + (f"; {len(bundled)} bundled libraries" if bundled else "")
            + (f"; debug symbols stripped from {len(stripped)} files" if stripped else "")
            + ("; patched: " + "; ".join(f"{path} — {why}" for path, why in sorted(changed.items()))
               if changed else "; upstream's source unmodified")
        ),
        "provides": provides,
    }
    # `upstream` on a *built* artifact, which most compiled recipes here do without: the source is
    # upstream's, it is the thing GPLv2 makes this recipe publish, and its signature is what was
    # checked. `changed` is deliberately **not** declared through `borrow.declare` — that field is
    # for files the artifact ships, and `include/my_global.h` is a header that was edited before a
    # compiler read it and is in no tree afterwards. Where the patch is recorded is the `recipe`
    # string, `licenses/SOURCE.md` and the published source tarball, all three of which a reader can
    # check; a `changed` entry naming a path that is not in the archive could not be.
    manifest["upstream"] = {
        "project": "mysql/mysql-server",
        "release": version,
        "url": url,
        "sha256": digest,
        "verified_against": mysql.verified_against(fingerprint),
    }
    borrow.declare(tree, manifest, removed=removed)

    measured = relocate.floor(tree)
    if measured:
        manifest["requires"] = {measured[0]: measured[1]}
        print(f"needs {measured[0]} {measured[1]} or newer")

    elsewhere = borrow.moved(tree)
    problems = relocate.verify(elsewhere)
    for problem in problems:
        print(f"error: {problem}", file=sys.stderr)
    if problems:
        raise SystemExit("the relocated tree reaches outside itself")
    manifest["smoke"] = {
        "relocated": True,
        "ran": mysql_smoke.server(elsewhere, version, provides, windows=False),
        "openssl": openssl_note(elsewhere, provides, OPENSSL_FOR_56["version"] if ssl else None),
    }
    print(f"TLS: {manifest['smoke']['openssl']}")
    borrow.discard(elsewhere)

    patched_source(source_tree, version, arguments.out)
    borrow.publish(tree, manifest, arguments.out, "tar")
    shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
