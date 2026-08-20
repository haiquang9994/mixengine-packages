#!/usr/bin/env python3
"""Borrow a MySQL build from Oracle's archive and repack it as a MixEngine artifact.

Five of the six cells, on the three lines upstream still builds: macOS on both architectures, Linux
on both, and Windows x86_64. On 5.6 and 5.7 this is **one** cell — the Windows zip — because upstream
withdrew macOS from those lines while they were alive and has never published an ARM build of either;
:mod:`mysql_build` compiles their four Unix cells.

Two things make this more than :mod:`caddy` with a different table.

*The verification is a signature, and it has to be fetched from somewhere else.* MySQL's archive page
states an MD5 and nothing better, and ``upstream.verified_against`` is not a field this repository
writes a broken hash into. The detached PGP signatures exist at two routes, neither of which is
beside the asset, and :mod:`mysql` knows where — including that an asset with no signature answers
``200`` with one byte rather than 404.

*A borrowed MySQL is not self-contained.* 8.0 and newer ship a ``lib/private/`` holding their own
protobuf and OpenSSL, linked by soname with no search path of its own, so ``relocate.bundle`` runs
over a borrowed tree here exactly as it does for MariaDB, and ``upstream.added`` records what it put
in.

Python 3 stdlib only, by policy.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import borrow
import mysql
import mysql_smoke
import relocate
import strip

# What a Windows tree's own imports say about the runtime a user has to have. Measured off the
# binaries rather than taken from a version number, because upstream's archive states the compiler
# nowhere in the asset name — unlike PHP's, where `php_windows.py` reads it out of the URL.
#
# In order, first match wins. `vcruntime140_1.dll` arrived with Visual Studio 2019 and the 2022
# redistributable is the one that supersedes it, which is what a user is told to install; a bare
# `vcruntime140.dll` is the 2015 family; `msvcr120.dll` is 2013 and `msvcr100.dll` is 2010.
#
# **Measuring it is the point, and 5.6 is why.** The obvious guess from MySQL 5.6's documentation is
# Visual Studio 2013; what `mysql-5.6.51-winx64.zip` actually imports is `msvcr100.dll` and
# `msvcp100.dll`, which is 2010. 5.7.44 goes the other way — a 2023 rebuild of a 2015-era line that
# imports `vcruntime140_1.dll`, so it needs the *newest* redistributable rather than the oldest. A
# table keyed on the line would have been wrong at both ends.
VCREDIST = (
    ("vcruntime140_1.dll", "2022"),
    ("vcruntime140.dll", "2015"),
    ("msvcr120.dll", "2013"),
    ("msvcr100.dll", "2010"),
)


def vcredist(tree: Path) -> str | None:
    """Which Microsoft runtime this Windows tree needs, read off what its binaries import."""
    imported = {
        name.lower()
        for path in relocate.machine_files(tree)
        for name in relocate.pe_imports(path)
    }
    for soname, redistributable in VCREDIST:
        if soname in imported:
            return redistributable
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True,
                        help="an exact version (9.7.1). Not a line: tools/mysql.py --plan resolves "
                             "those, once, for every cell at the same time.")
    parser.add_argument("--out", default=Path("dist"), type=Path)
    arguments = parser.parse_args()

    version = arguments.version.strip()
    if len(version.split(".")) != 3:
        raise SystemExit(
            f"{version} is a line rather than a version. Resolving one here, per leg, is how a "
            f"release ends up holding three cells of 8.0.45 and two of 8.0.44 — run "
            f"`tools/mysql.py --plan` and pass what it printed."
        )
    line = ".".join(version.split(".")[:2])
    if line not in mysql.LINES:
        raise SystemExit(f"this repository packs MySQL {', '.join(mysql.LINES)}, not {line}")

    target = borrow.host("MySQL")
    if target == ("windows", "aarch64"):
        borrow.unavailable(
            "Oracle has never published an ARM64 Windows build of MySQL, at any version of any "
            "line, and compiling one is not close: the 5.x trees are of an era whose published "
            "binaries still import the Visual Studio 2010 runtime, nobody has demonstrated either "
            "line building with MSVC on ARM64, and for 8.0 and newer it is a build nobody here has "
            "attempted."
        )
    if line in mysql.BUILT_LINES and target[0] != "windows":
        raise SystemExit(
            f"upstream publishes nothing for {target[0]}/{target[1]} on MySQL {line} — it withdrew "
            f"macOS from the line while it was alive and never built ARM for it at all. That cell "
            f"is compiled from source: use tools/mysql_build.py."
        )

    name = mysql.asset_for(version, target)
    if name is None:
        borrow.unavailable(
            f"upstream published no {target[0]}/{target[1]} asset for MySQL {version}"
        )

    windows = target[0] == "windows"
    work = borrow.long_name(Path(tempfile.mkdtemp(prefix="mixengine-mysql-")))
    program, home = mysql.keyring(work)
    archive, digest, url, fingerprint = mysql.download(program, home, name, version, work)

    tree = borrow.unpack(archive, work / "unpacked", "zip" if windows else "tar")
    removed = mysql.prune(tree)
    if removed:
        print(f"not shipping {len(removed)} paths: {', '.join(removed)}")
    # Before the bundling and not after it, so that the files this strips are upstream's own. What
    # `bundle` is about to copy in belongs to the machine's distribution, which stripped it already
    # and whose licence text `bundled_licences` files beside it under its own name.
    changed = strip.debug(tree)

    added: dict[str, Path] = {}
    if not windows:
        # `lib/private` is 8.0 and newer: upstream's own protobuf and OpenSSL, named by soname with
        # no search path, which is a directory MariaDB's bintar does not have. Naming it here does
        # two jobs, and the second one is easy to miss: it is where `bundle` looks while it decides
        # what is missing, *and* — because it is inside the tree — it is a directory `rewrite` then
        # keeps in every `DT_RUNPATH` it writes. Left out, the rewrite would point the whole tree at
        # `lib/` alone and upstream's own OpenSSL would stop being found from inside its own tree.
        added = relocate.bundle(tree, search=[tree / "lib", tree / "lib" / "private"])
        if added:
            print(f"bundled {len(added)} librar{'y' if len(added) == 1 else 'ies'}: "
                  f"{', '.join(sorted(added))}")
        relocate.bundled_licences(tree, added)

    # After the bundling, never before it. A plugin naming `libssl.so.3` is unresolvable in the tree
    # upstream shipped and perfectly resolvable in the one `bundle` has just finished, so asking the
    # question early would delete the plugins this recipe exists to make work.
    removed += mysql.unloadable_libraries(tree)

    provides = mysql_smoke.describe(tree, windows)

    manifest = {
        "schema": 1,
        "kind": "mysql",
        "version": version,
        "os": target[0],
        "arch": target[1],
        "source": "borrowed",
        "upstream": {
            "project": "mysql/mysql-server",
            "release": version,
            "url": url,
            "sha256": digest,
            "verified_against": mysql.verified_against(fingerprint),
        },
        "provides": provides,
    }
    borrow.declare(
        tree, manifest,
        added=[f"lib/{library}" for library in added],
        removed=removed,
        # A plugin can be stripped and then deleted a few lines later for naming a library upstream
        # did not ship, and `declare` refuses — rightly — to claim a modification to a file the
        # archive does not contain. What went out is already declared, once, in `upstream.removed`.
        changed={path: how for path, how in changed.items() if (tree / path).exists()},
    )

    requires = {}
    measured = relocate.floor(tree) if not windows else None
    if measured:
        requires[measured[0]] = measured[1]
        print(f"needs {measured[0]} {measured[1]} or newer")
    if windows:
        runtime = vcredist(tree)
        if runtime:
            requires["vcredist"] = runtime
            print(f"needs the Microsoft Visual C++ {runtime} redistributable")
    if requires:
        manifest["requires"] = requires

    elsewhere = borrow.moved(tree)
    problems = relocate.verify(elsewhere)
    for problem in problems:
        print(f"error: {problem}", file=sys.stderr)
    if problems:
        raise SystemExit("the relocated tree reaches outside itself")
    manifest["smoke"] = {
        "relocated": True,
        "ran": mysql_smoke.server(elsewhere, version, provides, windows),
    }
    borrow.discard(elsewhere)

    borrow.publish(tree, manifest, arguments.out, "zip" if windows else "tar")
    shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
