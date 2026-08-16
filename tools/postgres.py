#!/usr/bin/env python3
"""Borrow a PostgreSQL build from EDB and repack it as a MixEngine artifact.

**The row where the evaluation changed the plan twice**, which is what *borrow before you build* is
for. The runtime table said "EDB binaries" for Windows and macOS and "a tarball" for Linux. Asked
rather than assumed, upstream answers something else on both counts, and the answers are measured
rather than read off a download page:

* **Windows x86_64** — ``postgresql-<version>-1-windows-x64-binaries.zip``. Borrowed here.
* **macOS, both architectures** — ``postgresql-<version>-1-osx-binaries.zip``, and it is a **single
  universal build**: ``bin/postgres`` is a fat Mach-O carrying x86_64 and arm64 in one file. So one
  download serves two cells, which no other recipe in this repository can say.
* **Linux** — ``…-linux-x64-binaries.tar.gz`` answers 403 for every version tried. EDB stopped
  publishing it, and the PostgreSQL project itself publishes source and nothing else. The route left
  is the one MariaDB's aarch64 cell already takes: rearranging Debian's own packages, which
  ``apt.postgresql.org`` publishes for amd64 **and** arm64 with a SHA256 per file in its ``Packages``
  index. That is a recipe of its own and is P7a.
* **Windows on ARM** — nothing, from anybody. An empty cell, and P7b.

Three decisions this recipe is answerable for.

*The download is not checked against a digest the publisher states, because EDB states none.* Every
other borrow here has one — nodejs.org's ``SHASUMS256.txt``, Caddy's ``checksums.txt``, MariaDB's
REST API, python-build-standalone's ``SHA256SUMS`` — and ``get.enterprisedb.com`` answers 403 to
``.sha256`` and ``.md5`` beside every archive it serves. What is left is TLS to the publisher's own
host, with no mirror redirector in between, which is strictly what the Ruby recipe records when
RubyInstaller publishes no checksum file: ``verified_against`` says so in those words rather than
implying a check nobody made. The archive's own SHA-256 is still computed and published, so the
*next* person to download it can compare with this one.

*The version catalogue is upstream's, and so is the end-of-life date.* ``postgresql.org/versions
.json`` states every major, its newest minor, whether it is supported and the day support ends —
the same trade ``mariadb.py`` makes with the MariaDB REST API. Both kinds still have an entry in
``data/eol.json``, and for the reason that file states: the index must be rebuildable from the
release assets alone years later, and a generator that called this API would produce a different
index depending on the day it ran. What being read from a publisher changes is where the entry came
from, not whether it exists — so this prints the date it saw on every run, and a schedule upstream
moves is caught the next time that major is packed.

*Most of what EDB publishes is not a database server, and it is never extracted rather than being
extracted and deleted.* The Windows zip unpacks to 914 MB of which **717 MB is pgAdmin 4** — an
Electron desktop application with its own Python — plus StackBuilder, a downloader for more EDB
software. The macOS zip is 1,215 MB with the same two inside. MixEngine supervises a server and
installs neither, so ``UNWANTED`` is applied *while unpacking*: partly because writing 700 MB in
order to delete it is a minute of every run, and partly because it cannot be written at all —
``pgsql/pgAdmin 4/python/Lib/site-packages/azure/mgmt/rdbms/…`` is past ``MAX_PATH`` on Windows and
extraction dies half way with ``FileNotFoundError`` on a file whose name is simply too long. Every
root skipped is still named in ``upstream.removed``: a reader holding EDB's archive and this one
should find every difference stated, and "never unpacked" and "deleted" are the same difference.

Python 3 stdlib only, by policy: this runs on a GitHub runner with nothing installed.
"""

from __future__ import annotations

import argparse
import json
import shutil
import stat
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import borrow  # noqa: E402  — siblings, and this directory is not importable as a package
import postgres_smoke  # noqa: E402
import relocate  # noqa: E402

CATALOGUE = "https://www.postgresql.org/versions.json"
EDB = "https://get.enterprisedb.com/postgresql"

# What this recipe can borrow, and what EDB calls it. macOS appears twice on purpose: one universal
# archive is the build for both cells, which is measured — see the module docstring — rather than
# hoped, and `postgres --version` on the arm64 runner is what keeps it measured.
BORROWABLE = {
    ("windows", "x86_64"): "windows-x64",
    ("macos", "x86_64"): "osx",
    ("macos", "aarch64"): "osx",
}

# **Read off the archives rather than chosen.** EDB's macOS zip for 13 is a thin x86_64 Mach-O; from
# 14 on it is universal. A 13 packed here would mean *Intel* on a row where every other version means
# both architectures — one version meaning two things, decided by which cell a user installed from,
# which is the thing this repository exists not to do. 13 also went out of support in November 2025,
# so the floor is where the catalogue changes shape and where upstream stopped supporting it at once.
FLOOR = (14,)

# EDB appends a packaging revision to the version, and it is not always 1: a rebuild of the same
# PostgreSQL for a packaging fix is published as `-2`. Tried newest first, and the first that answers
# is the one taken — three HEAD requests rather than a table that goes stale silently.
BUILDS = (3, 2, 1)

# **Never extracted, and each is named in `upstream.removed` anyway.** See the module docstring for
# why this is applied during unpacking rather than after it. Matched case-insensitively against the
# first path component below the wrapper, by prefix, so that `pgAdmin 4` and `pgAdmin 4.app` and
# whatever the next major is called are one entry.
#
# `doc` is upstream's HTML manual, which is on postgresql.org and searchable; `include` is the SDK
# half that `lib/*.a` and `lib/pkgconfig` complete, and this installs a database rather than a
# development kit — the same argument MariaDB's `PRUNE` makes in the same words.
UNWANTED = ("pgadmin", "stackbuilder", "doc", "include", "symbols")

# What a database server does not need, by path, once the tree is out. Directories, taken whole.
#
# `pgxs` is PostgreSQL's build system for out-of-tree extensions: makefiles, an `install-sh`, and the
# regression harness (`pg_regress`, `isolationtester`) beside them. A user compiling an extension
# needs it *and* the headers that are not here, so shipping half of it would ship something that
# cannot work. `pkgconfig` is the same fact in a `.pc` file.
PRUNE = ("share/man", "share/doc", "share/postgresql/man", "share/postgresql/doc",
         "lib/pkgconfig", "lib/postgresql/pgxs", "lib/pgxs")

# **What MixEngine does not ship, stated once so that the cells of one version contain the same
# PostgreSQL.** Every entry is here because it fails one of two tests: it is not part of PostgreSQL,
# or it cannot work on a machine that has only this archive.
NOT_SHIPPED = (
    # The SDK, which `include` above already halves. Static libraries and import libraries are
    # linker inputs; `pg_config` prints the flags for using them and `ecpg` is a preprocessor that
    # turns embedded SQL into C to be compiled against headers this archive no longer carries.
    # Without `include/` there is nothing for any of them to do — the argument that removed
    # `mariadb_config` and `mysql_config`, in another project's spelling.
    "*.a", "*.lib", "pg_config", "pg_config.exe", "ecpg", "ecpg.exe",
    # **The procedural languages that are not PostgreSQL's own.** `plpgsql` is compiled into the
    # server and stays. `plperl`, `plpython3u` and `pltcl` each need an *interpreter installed on
    # the user's machine* — EDB's `plperl.dll` links a Perl this archive does not contain — so
    # `CREATE EXTENSION plperl` on a clean machine fails with a message about a missing DLL. Debian
    # ships each as its own `postgresql-plperl-N` package, so the Linux cells of P7a would not have
    # them either: one version meaning different things on different systems, which is what the rule
    # is against. The globs also take `hstore_plperl`, `jsonb_plpython3u` and the rest of the
    # transform modules, which are useless without the language they transform for.
    "*plperl*", "*plpython3*", "*pltcl*",
    # EDB's own additions, which are the clearest case of all: `plugin_debugger` is the server half
    # of pgAdmin's debugger and `system_stats` reports the *host's* CPU and memory. Neither is in
    # PostgreSQL, neither is in Debian's packages, and the two EDB archives do not even agree with
    # each other — 18.6 ships `system_stats.control` on macOS and not on Windows, which is one
    # version meaning two things inside a single publisher's own release.
    "pldbgapi*", "plugin_debugger*", "system_stats*",
    # The test suite's modules and harness, which live beside the real ones rather than in a
    # directory of their own. `test_decoding` is upstream's *example* logical-decoding plugin —
    # `pgoutput` is the one replication actually uses and it stays — and `test_cloexec` and
    # `testplug` are checks upstream runs at build time.
    "test_decoding*", "test_cloexec*", "testplug*", "pg_regress*", "isolationtester*",
    "pg_isolation_regress*",
    # wxWidgets, eight DLLs of it, which exist in this archive for StackBuilder's window and for
    # nothing else. They are in `bin/` beside the server rather than inside the application that
    # uses them, which is why removing StackBuilder alone leaves 14 MB of a GUI toolkit behind.
    "wx*.dll", "wxbase*", "wxmsw*",
)

# Debug information, by extension, wherever it sits — the same list `mariadb.DEBRIS` carries and for
# the same reason. EDB ships none today; a recipe that only removes what it has seen is a recipe that
# ships the first `.pdb` upstream adds.
DEBRIS = ("*.pdb", "*.dSYM")


def series() -> dict[tuple[int, ...], dict]:
    """Every PostgreSQL major the project currently lists, keyed for comparison.

    ``versions.json`` is upstream's own catalogue and states four things per major: the newest minor,
    whether it is supported, the day support ends and the day the line was released. Everything this
    recipe needs to resolve a version and everything the index needs to describe it, from one
    document published by the people who decide it.
    """
    found: dict[tuple[int, ...], dict] = {}
    for major in json.loads(borrow.fetch(CATALOGUE)):
        key = borrow.parts(str(major["major"]))
        # PostgreSQL numbered 9.6 and 10 in the same scheme; everything in range here is a single
        # integer, and a two-part key would sort 9.6 above 10.
        if len(key) == 1 and key >= FLOOR:
            found[key] = major
    if not found:
        raise SystemExit(f"{CATALOGUE} listed no major at or above {FLOOR[0]}; its format changed")
    return found


def newest(major: dict) -> str:
    """``18.6`` from the catalogue's ``{"major": "18", "latestMinor": "6"}``."""
    return f"{major['major']}.{major['latestMinor']}"


def exists(url: str) -> bool:
    """Whether EDB serves this file, asked with a HEAD so nothing is downloaded to find out."""
    request = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(request, timeout=60):
            return True
    except urllib.error.HTTPError:
        # 403 rather than 404 is what this host answers for a file it does not have, which is worth
        # knowing: neither is a network failure and neither should be retried.
        return False
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise SystemExit(f"{url} could not be reached: {error}") from error


def resolve(spec: str, target: tuple[str, str]) -> tuple[str, str, str | None]:
    """Turn ``18``, ``18.6`` or ``latest`` into one published archive.

    Answers ``(version, url, end of life)``. A version EDB has not built for this target is an
    **empty cell and not a failure**, as everywhere else here — though for PostgreSQL the empty cells
    are whole operating systems rather than early versions, and the recipe refuses the target before
    it gets this far.
    """
    stated = series()
    if spec == "latest":
        key = max(stated)
        version = newest(stated[key])
    else:
        wanted = borrow.parts(spec)
        key = wanted[:1]
        if key not in stated:
            raise SystemExit(
                f"postgresql.org lists no major {key[0]} at or above {FLOOR[0]}. It offers "
                f"{', '.join(str(other[0]) for other in sorted(stated))}."
            )
        version = spec if len(wanted) > 1 else newest(stated[key])

    name = BORROWABLE[target]
    for build in BUILDS:
        url = f"{EDB}/postgresql-{version}-{build}-{name}-binaries.zip"
        if exists(url):
            return version, url, stated[key].get("eolDate")
    borrow.unavailable(
        f"get.enterprisedb.com publishes no {name} archive for PostgreSQL {version} under any of "
        f"the packaging revisions {', '.join(str(build) for build in sorted(BUILDS))}"
    )
    raise AssertionError("unreachable")


def plan(spec: str) -> list[str]:
    """Expand what a workflow was asked to build into the list of majors to run.

    ``all`` is the reason this exists, as it is for MariaDB: PostgreSQL supports five majors at once,
    each with its own end-of-life years apart, and a user pinning 15 in a blueprint is as ordinary as
    one pinning 18. Only *majors* are resolved here — each leg asks upstream for the newest minor of
    its major independently.
    """
    stated = series()
    if spec.strip() == "all":
        return [str(key[0]) for key in sorted(stated)]
    if spec.strip() == "latest":
        return [str(max(stated)[0])]

    wanted = []
    for piece in spec.split(","):
        piece = piece.strip()
        if not piece:
            continue
        if borrow.parts(piece)[:1] not in stated:
            raise SystemExit(
                f"postgresql.org lists no PostgreSQL {piece} at or above {FLOOR[0]}. It offers "
                f"{', '.join(str(key[0]) for key in sorted(stated))}."
            )
        wanted.append(piece)
    if not wanted:
        raise SystemExit("nothing to build: the version list is empty")
    return wanted


def unwanted(relative: str) -> bool:
    """Whether this path is under one of the roots that is never extracted. See :data:`UNWANTED`."""
    first = relative.split("/", 1)[0].lower()
    return any(first.startswith(prefix) for prefix in UNWANTED)


def extract(archive: Path, into: Path) -> tuple[Path, list[str]]:
    """Unpack everything except :data:`UNWANTED`, answering ``(tree, roots left out)``.

    Written rather than reusing ``borrow.unpack`` for the reason in the module docstring — most of
    this archive must not be written to disk at all — and it carries the one thing ``zipfile`` does
    not do for itself: **permission bits and symlinks**. A zip stores the Unix mode in the top half
    of ``external_attr``, and ``extractall`` ignores it, so a macOS tree unpacked that way has a
    ``bin/postgres`` nobody can execute and a ``lib/libpq.5.dylib`` that is a *copy* of its target
    rather than a link to it. Windows has neither concept and neither branch runs there.
    """
    into.mkdir(parents=True, exist_ok=True)
    wrappers: set[str] = set()
    skipped: set[str] = set()

    with zipfile.ZipFile(archive) as zipped:
        for info in zipped.infolist():
            name = info.filename
            wrapper, _, relative = name.partition("/")
            wrappers.add(wrapper)
            if not relative or info.is_dir():
                continue
            if unwanted(relative):
                skipped.add(relative.split("/", 1)[0])
                continue

            mode = info.external_attr >> 16
            destination = into / wrapper / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            if stat.S_ISLNK(mode):
                target = zipped.read(info).decode("utf-8")
                destination.unlink(missing_ok=True)
                destination.symlink_to(target)
                continue
            with zipped.open(info) as source, destination.open("wb") as sink:
                shutil.copyfileobj(source, sink)
            if mode & 0o111:
                destination.chmod(destination.stat().st_mode | 0o111)

    if len(wrappers) != 1:
        raise SystemExit(
            f"expected one directory inside the archive, found {sorted(wrappers)}"
        )
    return into / wrappers.pop(), sorted(skipped)


def prune(tree: Path) -> list[str]:
    """Take out what a database server does not need, and say what went.

    The layout-independent half of the decision, in one place for the same reason
    ``mariadb.prune`` is: P7a will rearrange Debian's packages into a tree that is shaped
    differently, and *what is not shipped* must be decided once rather than per recipe. What each
    route does about its own layout stays with the route.
    """
    removed = []
    for relative in PRUNE:
        path = tree / relative
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
            removed.append(relative)
        elif path.is_file():
            path.unlink()
            removed.append(relative)

    for pattern in NOT_SHIPPED + DEBRIS:
        for path in sorted(tree.rglob(pattern)):
            # Only what is still there: a directory removed a moment ago takes its contents with it.
            # `is_symlink` first, because `exists()` follows the link and answers False for one whose
            # target an earlier pattern removed — the `mysql_ldb` bug, which cost MariaDB four rounds.
            if not path.is_symlink() and not path.exists():
                continue
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink()
            removed.append(str(path.relative_to(tree)).replace("\\", "/"))

    # What is left pointing at what just went. A macOS tree is full of `libfoo.dylib ->
    # libfoo.3.dylib` chains, and a dangling link is a worse artifact than a missing file: it is a
    # library that exists until something tries to load it.
    for path in sorted(tree.rglob("*")):
        if path.is_symlink() and not path.exists():
            path.unlink()
            removed.append(str(path.relative_to(tree)).replace("\\", "/"))
    return removed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--version", required=True,
        help="a major (18), an exact version (18.6), or 'latest'",
    )
    parser.add_argument("--out", default=Path("dist"), type=Path)
    parser.add_argument(
        "--plan", action="store_true",
        help="print the majors --version expands to, as JSON, and pack nothing. Used by the "
             "workflow to fan one run out over every supported major; accepts 'all'.",
    )
    arguments = parser.parse_args()

    if arguments.plan:
        print(json.dumps(plan(arguments.version)))
        return

    target = borrow.host("PostgreSQL")
    if target not in BORROWABLE:
        borrow.unavailable(
            f"get.enterprisedb.com publishes no binary archive for {target[0]}/{target[1]}: it "
            f"offers Windows x86_64 and a universal macOS build and stopped publishing Linux "
            f"tarballs. Linux is unpacked from apt.postgresql.org's packages by postgres_deb.py."
        )

    version, url, eol = resolve(arguments.version, target)
    if version != arguments.version:
        print(f"{arguments.version} resolved to {version}")
    if eol:
        print(f"upstream supports this major until {eol}")

    work = Path(tempfile.mkdtemp(prefix="mixengine-postgres-"))
    name = url.rsplit("/", 1)[-1]
    downloaded = work / name
    print(f"borrowing {url}")
    try:
        urllib.request.urlretrieve(url, downloaded)
    except urllib.error.HTTPError as error:
        raise SystemExit(f"{url} answered {error.code}") from error

    digest = borrow.sha256(downloaded)
    print(f"sha256 {digest} (EDB publishes no digest to check this against; see the module "
          f"docstring)")

    windows = target[0] == "windows"
    tree, left_out = extract(downloaded, work / "unpacked")
    print(f"not unpacked: {', '.join(left_out)}")

    removed = left_out + prune(tree)
    print(f"not shipping {len(removed)} paths")

    provides = postgres_smoke.describe(tree, windows)
    available = postgres_smoke.extensions(tree)
    modules = postgres_smoke.where(tree, postgres_smoke.MODULES)

    manifest = {
        "schema": 1,
        "kind": "postgres",
        "version": version,
        "os": target[0],
        "arch": target[1],
        "source": "borrowed",
        "upstream": {
            "project": "EnterpriseDB PostgreSQL binaries",
            "release": f"{version}-{name.split('-')[2]}",
            "url": url,
            "sha256": digest,
            # Said in these words on purpose. Every other borrow here names a document the publisher
            # states the digest in; EDB publishes none, and an artifact that implied otherwise would
            # be making the one claim a reader cannot check for themselves.
            "verified_against": (
                "HTTPS to get.enterprisedb.com; EDB publishes no checksum for these archives"
            ),
        },
        "provides": provides,
        # `shared` rather than `enabled`: a PostgreSQL extension is not switched on in configuration,
        # it is created in a database by whoever wants it, so every one of these is available and
        # none is loaded until asked for. What matters to the rule is that the *set* is the same in
        # every cell of a version, which `tools/parity.py` compares.
        "extensions": {"shared": available},
    }
    if modules:
        manifest["extension_dir"] = modules.relative_to(tree).as_posix()
    if removed:
        borrow.declare(tree, manifest, removed=removed)

    elsewhere = borrow.moved(tree)
    if not windows:
        # **Checked and not corrected, which is a decision.** EDB's archive carries its own OpenSSL,
        # ICU, krb5, libxml2 and lz4 in `lib/` already, so the expected answer is that nothing
        # reaches outside the tree. `relocate.bundle` would make that true by rewriting load
        # commands — and rewriting them means the shipped files are no longer the bytes EDB
        # published, which is a difference this recipe would then have to declare. Asking first is
        # how a recipe finds out whether it needs to.
        problems = relocate.verify(elsewhere)
        for problem in problems:
            print(f"error: {problem}", file=sys.stderr)
        if problems:
            raise SystemExit(
                "the relocated tree reaches outside itself: EDB's archive is not self-contained "
                "after all, and this recipe would have to bundle and re-sign it"
            )

    manifest["smoke"] = {
        "relocated": True,
        "ran": postgres_smoke.server(elsewhere, version, provides, windows),
    }
    borrow.discard(elsewhere)

    measured = relocate.floor(tree) if not windows else None
    if measured:
        manifest["requires"] = {measured[0]: measured[1]}
        print(f"needs {measured[0]} {measured[1]} or newer")

    print(f"{len(available)} extension(s) available: {', '.join(available)}")
    borrow.publish(tree, manifest, arguments.out, "zip" if windows else "tar.zst")
    shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
