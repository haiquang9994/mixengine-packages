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
#
# `ncurses` is deliberately absent: the client's line editing uses the libedit macOS ships, which is
# also what `ruby_unix.py` links for the same licence reason — GNU readline is GPLv3.
BREW_PACKAGES = ("bison", "openssl@3", "pkg-config")

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
DISABLED_PLUGINS = ("ROCKSDB", "COLUMNSTORE", "MROONGA", "S3", "SPIDER", "CONNECT", "OQGRAPH")

PRUNE = ("mysql-test", "sql-bench", "share/man", "share/doc", "man", "docs", "include", "support-files")


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
        found = {name: brew_prefix(name) for name in ("bison", "openssl@3")}
        missing = [name for name, prefix in found.items() if prefix is None]
        if missing:
            raise SystemExit(f"Homebrew has no {', '.join(missing)}, and this build needs both")
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
        "-DWITH_MARIABACKUP=OFF",
        # Not installed rather than installed and pruned: the test suite is most of the build time as
        # well as most of the tree.
        "-DINSTALL_MYSQLTESTDIR=",
        "-DINSTALL_SQLBENCHDIR=",
        "-DPLUGIN_AUTH_PAM=NO",
    ]
    arguments += [f"-DPLUGIN_{name}=NO" for name in DISABLED_PLUGINS]

    if sys.platform == "darwin":
        arguments += [
            f"-DWITH_SSL={found['openssl@3']}",
            f"-DBISON_EXECUTABLE={found['bison'] / 'bin' / 'bison'}",
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
    for relative in PRUNE:
        path = tree / relative
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        elif path.is_file():
            path.unlink()
    # macOS writes a `.dSYM` bundle beside everything it compiles — the debug information, lifted
    # into a Mach-O of its own. Nothing loads them and they are a large share of the archive.
    for symbols in sorted(tree.rglob("*.dSYM")):
        shutil.rmtree(symbols, ignore_errors=True)
    for symbols in sorted(tree.rglob("*.pdb")):
        symbols.unlink()
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

    origins = []
    for name, origin in sorted(bundled.items()):
        origins.append(f"{name}\t{origin}")
        real = origin.resolve()
        if "/Cellar/" in str(real):
            root = real
            while root.parent.name != "Cellar" and root.parent != root:
                root = root.parent
            for text in sorted(root.glob("LICENSE*")) + sorted(root.glob("COPYING*")):
                shutil.copy2(text, licences / f"{root.parent.name}-{text.name}")
    if origins:
        (licences / "BUNDLED.tsv").write_text(
            "library\tbuilt from\n" + "\n".join(origins) + "\n", encoding="utf-8"
        )


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
    if not windows:
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
