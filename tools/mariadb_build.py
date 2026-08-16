#!/usr/bin/env python3
"""Compile MariaDB for the three cells no publisher offers a binary for, and pack it relocatably.

**macOS on both architectures, and Windows on ARM64.** These are not cells where borrowing was
weighed and lost — there is nothing to borrow. MariaDB has never published a macOS build (checked
across every release from 10.2 to 13.1), and its Windows zip is x86_64 only. Homebrew and MacPorts
have a macOS MariaDB, and both are prefix-bound package-manager installs rather than relocatable
artifacts, which is the same reason ``ruby_unix.py`` refused ``ruby-builder`` and RVM.

So this is a standing commitment in the sense that file describes: every security release of a series
MixEngine offers has to come back through here, on three targets.

What carries the weight:

*``INSTALL_LAYOUT=STANDALONE``.* It is the layout upstream's own bintar uses — ``bin/``,
``lib/plugin/``, ``share/`` — so a compiled artifact and a borrowed one are the same shape and
``mariadb_smoke`` cannot mean different things about them. Any other layout puts the error messages
somewhere ``basedir`` does not reach and produces a server that starts and answers every connection
with ``Can't find messagefile``.

*Nothing is compiled that a local development environment does not run.* The storage engines that
need their own toolchain or their own database — RocksDB, ColumnStore, Mroonga, S3, Spider — are off,
Galera clustering is off (MixEngine supervises one node), and the embedded server is off. This is a
smaller build, and more importantly a build whose dependency list is short enough to bundle.

*Everything outside the C runtime is bundled and then proven from elsewhere*, exactly as in
``ruby_unix.py``: ``relocate.bundle`` on Unix, and on Windows a static MSVC runtime so that an
artifact does not depend on a redistributable the user has not installed.

*The version is resolved and the source verified against the same REST API the borrow recipes use*,
so ``11.8`` means one release across all six cells of this kind.

``docs/building-from-source.md`` is the other half of this file. Everything in it applies.
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
import borrow  # noqa: E402  — siblings, and this directory is not importable as a package
import mariadb  # noqa: E402
import mariadb_smoke  # noqa: E402
import relocate  # noqa: E402

# Homebrew is asked for the two things macOS genuinely cannot supply and nothing else.
#
#   bison     MariaDB's SQL parser is generated, and the bison Apple ships is 2.3 from 2006 — the
#             build needs 3.x and fails in the middle of `sql_yacc.yy` rather than at configure time.
#   openssl@3 macOS has LibreSSL headers only, which MariaDB's OpenSSL detection does not accept.
#             Bundled afterwards, so the artifact does not depend on Homebrew existing.
#   snappy    **The one asymmetry the finished artifacts showed that was ours rather than upstream's.**
#             Comparing the six cells of 12.3 plugin by plugin, both Linux cells carry all five
#             compression providers and macOS carried four: bzip2, lz4, lzma and lzo build against
#             what the SDK and MariaDB's own sources supply, and snappy has no header to find. So a
#             blueprint that sets a table's `PAGE_COMPRESSED` algorithm to snappy worked on Linux and
#             failed on macOS, for no reason a user could have predicted. Bundled like OpenSSL, and
#             its licence travels with it through `collect_licences`.
#
# `ncurses` is deliberately absent: the client's line editing uses the libedit macOS ships, which is
# also what `ruby_unix.py` links for the same licence reason — GNU readline is GPLv3.
BREW_PACKAGES = ("bison", "openssl@3", "pkg-config", "snappy")

# What Windows needs beyond Visual Studio, which the runner has. `winflexbison3` provides the same
# generated-parser tools under the names MariaDB's CMake looks for.
CHOCO_PACKAGES = ("winflexbison3",)

# Off in every build. Each is a storage engine or a feature whose absence a local development
# environment cannot notice, and whose presence is a toolchain, a service or a licence.
#
#   ROCKSDB/COLUMNSTORE/MROONGA/S3/SPIDER  each needs its own dependencies, and two of them do not
#                                          compile on ARM without patches nobody here should carry
#   CONNECT                                wants unixODBC and libxml2 from the machine
#   WSREP/Galera                           clustering, which MixEngine does not supervise
#   embedded server                        a library for linking a server into another program
#
# `PERFSCHEMA` is deliberately *not* here although it is the largest thing left: it is what
# `SHOW ENGINE PERFORMANCE_SCHEMA` and every profiling tool a developer might point at MixEngine
# reads, and an artifact missing it differs from the borrowed ones in a way a user would notice.
#
# This list and `mariadb.NOT_SHIPPED` are the same decision said twice — to a compiler here, to a
# packer there — so that all six cells of a version contain the same MariaDB. Change one and change
# the other.
DISABLED_PLUGINS = ("ROCKSDB", "COLUMNSTORE", "MROONGA", "S3", "SPIDER", "CONNECT", "OQGRAPH")

# What this recipe prunes is `mariadb.PRUNE` and the pattern lists beside it — not a list of its
# own, which is what it had and what let a Windows ARM64 artifact ship 21 MB of test binaries, a
# 14 MB import library and eighteen demonstration plugins that the borrowed Windows x86_64 artifact
# of the same version did not have. Turning them off at configure time is not the alternative: the
# `-DINSTALL_MYSQLTESTDIR=` attempt in `configure` is why installing and then pruning is the rule
# here.


def run(*command: str, cwd: Path | None = None, env: dict | None = None,
        capture: bool = False, timeout: int = 10800) -> str:
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
    """Run something whose failure is an answer rather than an error."""
    print("$ " + " ".join(str(part) for part in command), flush=True)
    try:
        return subprocess.run([str(part) for part in command],
                              capture_output=True, timeout=timeout).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def jobs() -> str:
    return str(os.cpu_count() or 2)


def source(spec: str, work: Path) -> tuple[str, Path, str, str | None]:
    """Fetch and unpack the release tarball, checked against the digest the REST API states.

    The source archive is listed in the same document as the binaries, so what exists and what it
    should hash to come from one place — the trade every recipe here makes.
    """
    stated = mariadb.lines()
    if spec == "latest":
        series = stated[max(stated)]
    else:
        prefix = borrow.parts(spec)[:2]
        matching = [key for key in stated if key[:2] == prefix]
        if not matching:
            raise SystemExit(
                f"downloads.mariadb.org lists no stable {spec}. It offers "
                f"{', '.join(stated[key]['release_id'] for key in sorted(stated))}."
            )
        series = stated[matching[0]]

    catalogue = mariadb.get(f"{mariadb.API}/{series['release_id']}/")["releases"]
    wanted = [name for name in catalogue
              if spec in ("latest",) or borrow.parts(name)[:len(borrow.parts(spec))]
              == borrow.parts(spec)]
    if not wanted:
        raise SystemExit(f"downloads.mariadb.org lists no {spec} in {series['release_id']}")
    version = max(wanted, key=borrow.parts)

    entry = next(
        (item for item in catalogue[version]["files"]
         if item.get("file_name") == f"mariadb-{version}.tar.gz"), None
    )
    if entry is None:
        raise SystemExit(f"the REST API lists no source tarball for {version}")
    digest = (entry.get("checksum") or {}).get("sha256sum")
    url = mariadb.secure(entry["file_download_url"])

    tarball = work / f"mariadb-{version}.tar.gz"
    print(f"fetching {url}")
    tarball.write_bytes(borrow.fetch(url, timeout=1800))
    actual = borrow.sha256(tarball)
    if actual != digest:
        raise SystemExit(f"{tarball.name} hashes to {actual}, the REST API states {digest}")
    print(f"sha256 {actual} (verified against downloads.mariadb.org's REST API)")

    with tarfile.open(tarball) as archive:
        archive.extractall(work, filter="data")
    unpacked = work / f"mariadb-{version}"
    if not (unpacked / "CMakeLists.txt").is_file():
        raise SystemExit(f"{unpacked} has no CMakeLists.txt; this is not a release tarball")
    return version, unpacked, actual, series.get("release_eol_date")


def toolchain() -> dict[str, str]:
    """One SDK and the compiler that belongs to it, asked of ``xcrun`` rather than left to CMake.

    **A GitHub macOS runner has two toolchains installed**, and nothing makes them agree: Xcode's,
    under ``/Applications/Xcode_<version>.app``, and the Command Line Tools' under
    ``/Library/Developer/CommandLineTools``. Each ships its own SDK, and the first build here mixed
    them — C headers resolved from the CLT's ``MacOSX14.sdk`` while ``<new>`` came from Xcode's
    ``c++/v1`` — which clang reports as ``<cstddef> tried including <stddef.h> but didn't find
    libc++'s <stddef.h>``, a message about header search paths that says nothing about the two SDKs
    behind it. It failed twenty seconds into compiling, on `gen_lex_hash.cc`, having configured
    perfectly.

    ``xcrun`` answers for whichever developer directory is *selected*, so asking it for the SDK and
    for the compilers in one breath is what makes them the same toolchain. Passing them explicitly
    also pins the answer into the build, rather than leaving each CMake version free to re-derive it.
    """
    def ask(*command: str) -> str:
        return subprocess.run(command, capture_output=True, text=True, timeout=300).stdout.strip()

    sdk = ask("xcrun", "--show-sdk-path")
    clang, clangxx = ask("xcrun", "-f", "clang"), ask("xcrun", "-f", "clang++")
    if not (sdk and clang and clangxx):
        raise SystemExit("xcrun could not name an SDK and a compiler; this machine has no toolchain")

    # **Passing the SDK to CMake is not enough, which took a second CI round to establish.** With the
    # compiler and `CMAKE_OSX_SYSROOT` both pinned to Xcode's SDK, `find_package(ZLIB)` still answered
    # with the *Command Line Tools* SDK — `MacOSX14.sdk/usr/lib/libz.tbd` — because a `find_` call
    # searches the environment's `SDKROOT` rather than the sysroot the compile will use. CMake then
    # adds that SDK's `usr/include` as an include directory, C headers land ahead of libc++, and the
    # build dies in `gen_lex_hash.cc` on a message about header search paths.
    #
    # Setting it in the environment is what makes every *later* lookup agree with the compile, and
    # it is exported rather than passed because `xcrun` and CMake read it from different places.
    os.environ["SDKROOT"] = sdk
    print(f"toolchain: {clang} against {sdk}")
    return {"sdk": sdk, "cc": clang, "cxx": clangxx}


def brew_prefix(formula: str) -> Path | None:
    result = subprocess.run(["brew", "--prefix", formula], capture_output=True, text=True,
                            timeout=300)
    prefix = Path(result.stdout.strip()) if result.stdout.strip() else None
    return prefix if result.returncode == 0 and prefix and prefix.is_dir() else None


def dependencies() -> dict[str, Path]:
    """Install what the machine can give, and answer with where it put the two that matter."""
    if sys.platform == "darwin":
        for package in BREW_PACKAGES:
            attempt("brew", "install", package)
        found = {name: brew_prefix(name) for name in ("bison", "openssl@3", "snappy")}
        missing = [name for name, prefix in found.items() if prefix is None]
        if missing:
            # Fatal rather than degraded, snappy included: a build that quietly drops a provider is
            # how macOS came to be the one system missing one, and nothing in the artifact said so.
            raise SystemExit(f"Homebrew has no {', '.join(missing)}, and this build needs each")
        # **Homebrew's prefix is a search path on Intel and not on Apple Silicon**, which is how two
        # artifacts of one version come to differ with no error on either. Asked rather than assumed,
        # for the reason `ruby_unix.build` states at length.
        os.environ["PATH"] = f"{found['bison'] / 'bin'}{os.pathsep}{os.environ['PATH']}"
        return {name: prefix for name, prefix in found.items() if prefix}

    for package in CHOCO_PACKAGES:
        attempt("choco", "install", "-y", "--no-progress", package)
    return {}


def configure(source_tree: Path, build: Path, prefix: Path, found: dict[str, Path]) -> list[str]:
    """The CMake invocation, as one list so that what was asked for is what the manifest records."""
    arguments = [
        "cmake", str(source_tree),
        f"-DCMAKE_INSTALL_PREFIX={prefix}",
        "-DCMAKE_BUILD_TYPE=RelWithDebInfo",
        # The whole reason a compiled tree and a borrowed one are interchangeable. See the docstring.
        "-DINSTALL_LAYOUT=STANDALONE",
        "-DWITH_UNIT_TESTS=OFF",
        "-DCONC_WITH_UNIT_TESTS=OFF",
        "-DWITH_EMBEDDED_SERVER=OFF",
        "-DWITH_WSREP=OFF",
        # `WITH_MARIABACKUP` is left at its default, which is on. It is the one optional thing in
        # this build a developer would actually reach for — a physical backup of a running database
        # — it needs nothing from the machine, and the borrowed cells ship it. See
        # `mariadb.NOT_SHIPPED` for why the parity goes this way rather than the other.
        # **The test suite is pruned after installation rather than turned off at configure time.**
        # `-DINSTALL_MYSQLTESTDIR=` is what MariaDB's own documentation suggests and it produces a
        # broken install: paths derived from it lose their prefix, so `plugin/auth_pam/testing`
        # resolves to `/suite/plugins/pam` — at the root of the filesystem — and the install step
        # fails there after a full build has already succeeded. Costing a minute of copying is
        # better than a variable whose empty value is interpreted three directories away.
        "-DPLUGIN_AUTH_PAM=NO",
        # The bundled zlib rather than the machine's, and it is the *machine's* that caused trouble:
        # finding it is what dragged a second SDK's include directory into the compile on macOS (see
        # `toolchain`). It is also one fewer library to bundle and one fewer version to differ
        # between the six artifacts of a release.
        "-DWITH_ZLIB=bundled",
    ]
    arguments += [f"-DPLUGIN_{name}=NO" for name in DISABLED_PLUGINS]

    if sys.platform == "darwin":
        picked = toolchain()
        arguments += [
            # One toolchain, stated. See `toolchain` — the alternative is a runner with two of them
            # and a build that mixes their headers.
            f"-DCMAKE_OSX_SYSROOT={picked['sdk']}",
            f"-DCMAKE_C_COMPILER={picked['cc']}",
            f"-DCMAKE_CXX_COMPILER={picked['cxx']}",
            f"-DWITH_SSL={found['openssl@3']}",
            f"-DBISON_EXECUTABLE={found['bison'] / 'bin' / 'bison'}",
            # **Named, not left to the default search path**, which is the whole lesson of the
            # comment in `dependencies`: `cmake/FindSnappy.cmake` is a bare `find_path` plus
            # `find_library`, so on Intel it would find `/usr/local` and on Apple Silicon it would
            # find nothing — one architecture with the provider and one without, no error on either.
            f"-DCMAKE_PREFIX_PATH={found['snappy']}",
            # Only the linker can leave room for a longer install name, and every Mach-O here is
            # going to be rewritten by `relocate.bundle`. Finding that out afterwards costs the
            # whole build.
            "-DCMAKE_SHARED_LINKER_FLAGS=-Wl,-headerpad_max_install_names",
            "-DCMAKE_EXE_LINKER_FLAGS=-Wl,-headerpad_max_install_names",
        ]
    else:
        arguments += [
            # wolfSSL, compiled into the server, rather than an OpenSSL the user has to have: there
            # is no system OpenSSL on Windows, and a bundled DLL is one more file beside every
            # binary that has to be found after the tree moves.
            "-DWITH_SSL=bundled",
            # A statically linked C runtime, for the same reason: an artifact that needs a Visual
            # C++ redistributable the user has not installed fails to start with a dialog box and no
            # log line. It costs a few megabytes across the tree.
            "-DCMAKE_MSVC_RUNTIME_LIBRARY=MultiThreaded",
            "-DMSVC_STATIC_RUNTIME=ON",
            "-A", "ARM64" if borrow.host("MariaDB")[1] == "aarch64" else "x64",
        ]
    build.mkdir(parents=True, exist_ok=True)
    run(*arguments, cwd=build)
    return arguments[1:]


def compile_and_install(build: Path) -> None:
    if sys.platform == "win32":
        run("cmake", "--build", str(build), "--config", "RelWithDebInfo", "--parallel", jobs(),
            cwd=build)
        run("cmake", "--install", str(build), "--config", "RelWithDebInfo", cwd=build)
    else:
        run("cmake", "--build", str(build), "--parallel", jobs(), cwd=build)
        run("cmake", "--install", str(build), cwd=build)


def assemble(prefix: Path, work: Path) -> Path:
    tree = work / "tree"
    shutil.copytree(prefix, tree, symlinks=True)

    # macOS writes a `.dSYM` bundle beside everything it compiles — the debug information, lifted
    # into a Mach-O of its own. Nothing loads them and they are a large share of the archive. Done
    # before the shared prune so that a `.dSYM` whose binary is about to go does not outlive it.
    for symbols in sorted(tree.rglob("*.dSYM")):
        shutil.rmtree(symbols, ignore_errors=True)

    removed = mariadb.prune(tree)
    if removed:
        print(f"not shipping {len(removed)} paths: {', '.join(removed)}")
    return tree


def collect_licences(tree: Path, source_tree: Path, bundled: dict[str, Path]) -> None:
    """Ship the licence of everything in the archive — MariaDB's, and each bundled library's.

    Several of these require their text to travel with the binary, so this is a condition of
    redistributing the archive rather than tidiness.
    """
    licences = tree / "licenses"
    licences.mkdir(exist_ok=True)
    for name in ("COPYING", "COPYING.thirdparty", "THIRDPARTY", "EXCEPTIONS-CLIENT"):
        if (source_tree / name).is_file():
            shutil.copy2(source_tree / name, licences / f"mariadb-{name}")

    # The bundled half is `relocate.bundled_licences`, shared with every recipe that bundles at all,
    # because the walk that used to live here stopped one directory above the files and collected
    # nothing.
    relocate.bundled_licences(tree, bundled)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True, help="a series (11.8), an exact version, 'latest'")
    parser.add_argument("--out", default=Path("dist"), type=Path)
    arguments = parser.parse_args()

    operating_system, arch = borrow.host("MariaDB")
    if (operating_system, arch) in mariadb.BORROWABLE:
        raise SystemExit(
            f"upstream publishes a binary for {operating_system}/{arch}; compiling one here would be "
            f"a pipeline maintained for every security release in place of a download. Use "
            f"tools/mariadb.py."
        )
    windows = operating_system == "windows"

    work = Path(tempfile.mkdtemp(prefix="mixengine-mariadb-"))
    found = dependencies()
    version, source_tree, digest, eol = source(arguments.version, work)
    print(f"{arguments.version} resolves to MariaDB {version} ({operating_system}/{arch})")
    if eol:
        print(f"upstream supports this series until {eol}")

    prefix = (Path("C:/mixengine") if windows else Path("/opt/mixengine")) / f"mariadb-{version}"
    try:
        prefix.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        run("sudo", "mkdir", "-p", str(prefix))
        run("sudo", "chown", "-R", str(os.getuid()), str(prefix.parent))

    asked = configure(source_tree, work / "build", prefix, found)
    compile_and_install(work / "build")
    tree = assemble(prefix, work)

    provides = mariadb_smoke.describe(tree, windows)
    bundled: dict[str, Path] = {}
    if not windows:
        bundled = relocate.bundle(tree, search=[found["openssl@3"] / "lib"])
        print(f"bundled {len(bundled)} librar{'y' if len(bundled) == 1 else 'ies'}: "
              f"{', '.join(sorted(bundled))}")
    collect_licences(tree, source_tree, bundled)

    manifest = {
        "schema": 1,
        "kind": "mariadb",
        "version": version,
        "os": operating_system,
        "arch": arch,
        "source": "built",
        "recipe": (
            f"mariadb-{version}.tar.gz from source (sha256 {digest[:12]}…, as published in "
            f"downloads.mariadb.org's REST API); cmake "
            + " ".join(part for part in asked if part.startswith("-D"))
            + (f"; {len(bundled)} bundled libraries" if bundled else "")
        ),
        "provides": provides,
    }
    measured = relocate.floor(tree) if not windows else None
    if measured:
        manifest["requires"] = {measured[0]: measured[1]}
        print(f"needs {measured[0]} {measured[1]} or newer")

    elsewhere = borrow.moved(tree)
    # Windows included since P6a. The default `directories` needs no help here: this tree keeps
    # everything in `bin` and `lib`, and a root scan of the published 12.3.2 finds the same 85 files
    # — which is why MariaDB is the one row of the six where nothing but the guard was wrong. It is
    # also the tree `verify` had hard-coded `tree/"bin"` for, a shape that turned out not to be
    # every tree's; see `relocate.verify`.
    problems = relocate.verify(elsewhere)
    for problem in problems:
        print(f"error: {problem}", file=sys.stderr)
    if problems:
        raise SystemExit("the relocated tree reaches outside itself")
    manifest["smoke"] = {
        "relocated": True,
        "ran": mariadb_smoke.server(elsewhere, version, provides, windows),
    }
    borrow.discard(elsewhere)

    borrow.publish(tree, manifest, arguments.out, "zip" if windows else "tar")
    shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
