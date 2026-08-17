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
  is the one MariaDB's aarch64 cell already takes: the project's own ``.deb`` packages, published for
  amd64 **and** arm64 with a SHA256 per file in a ``Packages`` index whose own digest a ``Release``
  file states. That is :mod:`postgres_deb`, and it is a better-checked download than this one.
* **Windows on ARM** — nothing, from anybody, and P7b asked upstream *why* rather than stopping at
  that. PostgreSQL does not compile there: ``src/tools/msvc_gendef.pl``, which generates the export
  file the server's own extensions link against, accepts ``x86 | x86_64`` on every branch through
  18 and rejects ``aarch64``, so the build stops at target 1206 of 2047 with 1205 objects already
  compiled for ``/MACHINE:ARM64``. The list gained ``aarch64`` after 18 branched. The cell opens
  with PostgreSQL 19 and is empty until then — which is a fact about upstream, not about this
  repository, and it is stated in the index rather than left for a user to discover.

Four decisions this recipe is answerable for.

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

*And the macOS tree is written down three times over, which P7b measured rather than assumed.* What
one universal archive saves on the download it spends on the disk: after the roots above are left
out and :func:`prune` has run, the tree is **362 MB**, and 199 MB of that is the same bytes written
more than once. 161 MB is machine code compiled twice, once per architecture, of which one copy is
for a machine the cell cannot run on. The other 38 MB is upstream shipping a dylib's version chain
as whole copies — ``libicudata.dylib``, ``libicudata.77.dylib`` and ``libicudata.77.1.dylib`` are
three identical 64 MB files where an ordinary install has one file and two links, and the archive
does know how to store a link: it holds 78 of them, all inside pgAdmin. So :func:`link_versions`
puts the chain back and :func:`thin` keeps the slice this cell can execute, and **362 MB becomes
82 MB** with nothing taken out that a database uses.

That is the size half. The correctness half is quieter and matters more: ``otool`` reports a
universal binary's load commands for *every* architecture in it, so ``relocate.verify`` and
``relocate.floor`` were reading two machines at once and answering for the higher of them. Each
cell now measures the binaries it actually ships.

Python 3 stdlib only, by policy: this runs on a GitHub runner with nothing installed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import stat
import struct
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
import strip  # noqa: E402

CATALOGUE = "https://www.postgresql.org/versions.json"
EDB = "https://get.enterprisedb.com/postgresql"

# What this recipe can borrow, and what EDB calls it. macOS appears twice on purpose: one universal
# archive is the build for both cells, which is measured — see the module docstring — rather than
# hoped, and `postgres --version` on the arm64 runner is what keeps it measured. What each leg
# *publishes* is not universal: `thin` keeps the slice that leg can run, so the two archives are
# alike in every respect except the one that made them two cells.
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
#
# **Patterns rather than paths, and `**` rather than three spellings of each.** The three routes that
# produce a PostgreSQL disagree about depth and about nothing else: EDB's Windows zip has
# `share/man`, its macOS zip has `share/postgresql/man`, and a tree rearranged from Debian's packages
# has `share/postgresql/<major>/man`. `**` matches zero directories as well as several, so one entry
# is all three. Writing them out instead was how this list came to have two of the three.
PRUNE = ("share/**/man", "share/**/doc", "lib/**/pkgconfig", "lib/**/pgxs")

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
    # **And `sepgsql`, which is upstream's and which only one of the three routes even has.** It is
    # built on Linux alone, it is the one module in Debian's set that neither EDB archive contains,
    # and it does nothing on a machine without an SELinux policy loaded and `shared_preload_libraries`
    # naming it — which is not a machine anyone runs a local development environment on. It ships no
    # control file either, so `parity.py` would never have seen the difference: this is the rule
    # applied by hand where the check cannot reach.
    "sepgsql*",
    # The test suite's modules and harness, which live beside the real ones rather than in a
    # directory of their own. `test_decoding` is upstream's *example* logical-decoding plugin —
    # `pgoutput` is the one replication actually uses and it stays — and `test_cloexec` and
    # `testplug` are checks upstream runs at build time.
    "test_decoding*", "test_cloexec*", "testplug*", "pg_regress*", "isolationtester*",
    "pg_isolation_regress*",
    # **StackBuilder, and it takes two entries because it is scattered across three places.**
    # `UNWANTED` keeps its own directory out at unpack time. What that leaves in `bin/` is wxWidgets
    # — eight DLLs, there for StackBuilder's window and for nothing else, sitting beside the server
    # rather than inside the application that uses them, so removing the directory alone leaves
    # 14 MB of GUI toolkit behind.
    #
    # And `stackbuilder.exe`, which is the same oversight pointing the other way and which shipped.
    # Removing the DLLs without it published **a binary that cannot load**: three unresolved wx
    # imports in `postgres-18.6-windows-x86_64.zip`, found the first minute `relocate.verify` was
    # allowed to look at a Windows tree, in an archive that had passed every check this repository
    # had. Neither half was ever wanted — a downloader for more EDB software is not something a
    # local development environment installs — and the pair is written on one entry now so that
    # deleting one of them again means deleting the sentence that explains the other.
    "stackbuilder*", "wx*.dll", "wxbase*", "wxmsw*",
    # **`libpq-oauth`, which is `stackbuilder.exe` again on the other operating system.** PostgreSQL
    # 18 adds an OAuth 2.0 device-authorization flow for libpq, loaded out of a module of its own, and
    # EDB's macOS archive ships the module without the libcurl it is built against. Its only
    # `LC_RPATH` is `@loader_path`, so `@rpath/libcurl.4.dylib` can mean exactly one file —
    # `lib/libcurl.4.dylib` — and that file is not in the archive. Read out of the published
    # `postgresql-18.6-1-osx-binaries.zip` rather than inferred, both slices of the universal binary
    # alike.
    #
    # **Dropping it closes a difference between the cells instead of opening one**, which is the fact
    # that decided this. The Windows archive carries `bin/libcurl.dll` and no `libpq-oauth` at all;
    # the Linux cells, built from the project's own `.deb`s, carry neither. macOS was the only one of
    # the six with the module, and it was the one that could not load it — so this is not a feature
    # being taken away from a row, it is a row being made to say what the other two already say.
    #
    # The alternative was to bundle libcurl and re-sign, and it costs the property `thin` exists to
    # keep: EDB signs these binaries and this recipe changes which bytes ship without ever changing
    # one. Apple's libcurl is not a file on disk to copy either — it lives in the dyld shared cache —
    # so bundling would mean Homebrew's, and its OpenSSL behind it.
    "libpq-oauth*",
)

# Debug information, by extension, wherever it sits — the same list `mariadb.DEBRIS` carries and for
# the same reason. EDB ships none today; a recipe that only removes what it has seen is a recipe that
# ships the first `.pdb` upstream adds.
DEBRIS = ("*.pdb", "*.dSYM")

# The two headers a universal binary can begin with, mapped to the width of one entry in the table
# that follows: the older 32-bit form and the `FAT_MAGIC_64` one, which differs only in that offsets
# and lengths are eight bytes rather than four. Both are big-endian in a file whose slices are not.
FAT_MAGICS = {0xCAFEBABE: 20, 0xCAFEBABF: 32}

# Apple's CPU type numbers, and both spellings of what they mean: this repository's, which is what
# the manifest's `arch` field holds and what a slice has to be matched against, and Apple's, which is
# what `lipo -info` prints to somebody checking the declaration. One translation in one place, rather
# than two vocabularies meeting at a call site.
CPU_TYPES = {0x01000007: ("x86_64", "x86_64"), 0x0100000C: ("aarch64", "arm64")}


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
    for pattern in PRUNE:
        # `sorted` so that a tree with two matches removes them in a stated order, and the *matched*
        # path is what goes into `upstream.removed` rather than the pattern: a reader holding the
        # publisher's archive and this one is comparing paths, not globs.
        for path in sorted(tree.glob(pattern)):
            relative = str(path.relative_to(tree)).replace("\\", "/")
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


def slices(path: Path) -> dict[str, tuple[int, int]] | None:
    """``{architecture: (offset, length)}`` for a universal binary, ``None`` for anything else.

    A fat file is a big-endian table of architectures glued to the front of several complete Mach-O
    files, and **every offset inside a slice is relative to the slice**. That is the whole reason
    :func:`thin` can be a byte-range copy rather than a rewrite: the slice does not know it was ever
    part of a larger file, so lifting it out changes nothing in it — its code signature included.
    """
    with path.open("rb") as handle:
        header = handle.read(8)
        if len(header) < 8:
            return None
        magic, count = struct.unpack(">II", header)
        if magic not in FAT_MAGICS:
            return None
        width = FAT_MAGICS[magic]
        table = handle.read(count * width)

    found: dict[str, tuple[int, int]] = {}
    for index in range(count):
        entry = table[index * width:(index + 1) * width]
        if width == 32:
            cpu, _subtype, offset, length = struct.unpack(">IIQQ", entry[:24])
        else:
            cpu, _subtype, offset, length = struct.unpack(">IIII", entry[:16])
        name = CPU_TYPES.get(cpu, (f"cputype {cpu:#x}",))[0]
        if name in found:
            # Two slices of one architecture is legal — `arm64` and `arm64e` differ by subtype
            # alone — and this recipe would have to be told which one a PostgreSQL server wants.
            # It has never happened in an EDB archive; if it starts, that is a decision for a
            # person rather than for `max`.
            raise SystemExit(f"{path.name} carries two {name} slices, and nothing here can choose")
        found[name] = (offset, length)
    return found


def link_versions(tree: Path, libdir: str = "lib") -> dict[str, str]:
    """Put back the dylib version chains upstream shipped as whole copies. See the module docstring.

    ``libicudata.dylib``, ``libicudata.77.dylib`` and ``libicudata.77.1.dylib`` are three identical
    64 MB files in EDB's macOS archive, and an ordinary ICU install is one file and two links. All
    23 groups this finds are that shape — a library, its major, and its full version — which is why
    the most-versioned spelling is the one kept: it is the file, and the shorter names are what
    something asks for. dyld follows a link like any other path, so nothing has to be rewritten.

    **Identical bytes in one directory, and not across directories**, which is a narrower rule than
    it could be and deliberately so. `share/postgresql/timezone` holds 175 files that are byte-equal
    to another somewhere else in it — `Cuba` and `America/Havana` are one file — and those are
    aliases rather than a chain, worth 0.1 MB, and linking them would put 175 lines into
    ``upstream.changed`` for nothing anybody is going to check.
    """
    made: dict[str, str] = {}
    root = tree / libdir
    if not root.is_dir():
        return made

    directories: dict[Path, list[Path]] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and not path.is_symlink():
            directories.setdefault(path.parent, []).append(path)

    for _parent, paths in sorted(directories.items()):
        by_size: dict[int, list[Path]] = {}
        for path in paths:
            by_size.setdefault(path.stat().st_size, []).append(path)
        for candidates in by_size.values():
            if len(candidates) < 2:
                continue        # a size nothing else shares cannot be a duplicate of anything
            groups: dict[str, list[Path]] = {}
            for path in candidates:
                groups.setdefault(hashlib.sha256(path.read_bytes()).hexdigest(), []).append(path)
            for group in groups.values():
                if len(group) < 2:
                    continue
                keep = max(group, key=lambda path: (len(path.name), path.name))
                for path in group:
                    if path == keep:
                        continue
                    path.unlink()
                    path.symlink_to(keep.name)
                    made[path.relative_to(tree).as_posix()] = (
                        f"a symlink to {keep.name}, which upstream ships as a second copy of the "
                        f"same bytes"
                    )
    return made


def signature(blob: bytes) -> bool:
    """Whether this thin Mach-O carries an ``LC_CODE_SIGNATURE``, which decides what was proven.

    :func:`strip.countersigned` answers ``None`` both for a signature that checks out and for a file
    that has none, and those are not the same claim. Counted here so that the line this recipe
    prints says how many extractions were actually verified rather than how many were looked at — an
    archive whose publisher stops signing would otherwise go on reporting a check nobody made.
    """
    if blob[:4] != strip.MACHO_MAGIC:
        # Not a 64-bit little-endian Mach-O, which is the only shape whose load commands begin
        # where this reads them. Everything in these archives is one; answering False rather than
        # walking a header this does not understand is the difference between an unproven file
        # being counted as unproven and this function reading whatever lies at offset 16.
        return False
    ncmds, offset = struct.unpack_from("<I", blob, 16)[0], 32
    for _ in range(ncmds):
        command, size = struct.unpack_from("<II", blob, offset)
        if command == 0x1D:
            return True
        offset += size
    return False


def thin(tree: Path, arch: str) -> dict[str, str]:
    """Keep the slice this cell can execute, drop the other, and prove the copy was exact.

    The proof is the file's own code signature, and it is a real one rather than a formality. Every
    binary in this archive carries an ad-hoc signature whose CodeDirectory holds a SHA-256 of each
    4 KB page — 173 of 173, measured — and :func:`strip.countersigned` recomputes every one of them
    against the bytes now on disk. An extraction off by a byte fails there, and on arm64 a file that
    failed there is one the kernel answers with ``SIGKILL`` rather than an error anything can print.
    So the operation is a byte-range copy taken from the file's own header, and the check is
    arithmetic that had to agree with a publisher who signed the slice before it was ever fat.

    Nothing is run afterwards to establish this, because for once nothing needs to be: the shipped
    file is a byte-for-byte extract of something EDB compiled and signed. What *is* run afterwards
    is the server, from a directory the tree was moved to, as on every other cell.
    """
    apple = next(names[1] for names in CPU_TYPES.values() if names[0] == arch)
    changed: dict[str, str] = {}
    countersigned = 0
    for path in relocate.machine_files(tree):
        found = slices(path)
        if found is None:
            continue            # already thin: a slice of one, and nothing to choose between
        if arch not in found:
            raise SystemExit(
                f"{path.relative_to(tree).as_posix()} carries {', '.join(sorted(found))} and not "
                f"{arch}, so this archive is not the universal build both Apple cells are packed "
                f"from"
            )
        offset, length = found[arch]
        with path.open("rb") as handle:
            handle.seek(offset)
            body = handle.read(length)
        if len(body) != length:
            raise SystemExit(f"{path.name} ends before its own {arch} slice does")

        # The permission bits alone: `st_mode` also carries the file type, and `chmod` is only
        # specified for the twelve bits below it. Rewriting in place keeps the mode anyway; this is
        # here so that the file's being executable does not depend on that staying true.
        mode = stat.S_IMODE(path.stat().st_mode)
        path.write_bytes(body)
        path.chmod(mode)

        wrong = strip.countersigned(path)
        if wrong:
            raise SystemExit(wrong)
        countersigned += signature(body)
        changed[path.relative_to(tree).as_posix()] = (
            f"the {apple} slice of upstream's universal binary, extracted whole"
        )

    # Nothing universal may survive this, anywhere — not only under the directories
    # `relocate.machine_files` looks in. A fat file left behind is one architecture of dead weight
    # that every measurement in `relocate` would go on reading as though it were this machine's.
    left = [path.relative_to(tree).as_posix() for path in sorted(tree.rglob("*"))
            if path.is_file() and not path.is_symlink() and slices(path) is not None]
    if left:
        raise SystemExit(
            f"{len(left)} universal binar{'y' if len(left) == 1 else 'ies'} outside the "
            f"directories this looked in — {', '.join(left[:4])} — so the artifact still carries "
            f"an architecture it cannot run"
        )
    print(f"thinned {len(changed)} binaries to {arch}, {countersigned} of them re-checked against "
          f"their own code signature")
    return changed


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

    # **Before anything measures this tree**, which is the order rather than a preference: `otool`
    # reports a universal binary's load commands once per architecture, so `relocate.verify` and
    # `relocate.floor` below would otherwise be answering for two machines and reporting the higher
    # of them as this cell's. De-duplicating first means the 35 links are skipped by
    # `relocate.machine_files`, which does not follow one, and the slice is lifted out once per
    # distinct file rather than once per spelling of its name.
    changed: dict[str, str] = {}
    if target[0] == "macos":
        changed.update(link_versions(tree))
        print(f"linked {len(changed)} name(s) onto the file upstream copied them from")
        changed.update(thin(tree, target[1]))

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
    if removed or changed:
        borrow.declare(tree, manifest, removed=removed, changed=changed)

    elsewhere = borrow.moved(tree)
    # **Checked and not corrected, which is a decision.** EDB's archive carries its own OpenSSL,
    # ICU, krb5, libxml2 and lz4 in `lib/` already, so the expected answer is that nothing reaches
    # outside the tree. `relocate.bundle` would make that true by rewriting load commands, and a
    # rewritten load command is a *modified* binary: the signature over it stops matching and every
    # file has to be signed again by this repository rather than by the publisher. `thin` above
    # changes which bytes ship without ever changing a byte, which is why it needs no signature of
    # its own; bundling would be the other kind of change. Asking first is how a recipe finds out
    # whether it needs to.
    #
    # Windows was outside this until P6a, on the true-at-the-time grounds that `relocate` could not
    # read a PE. The first run with the guard off failed, on `bin/stackbuilder.exe` — see
    # `NOT_SHIPPED` — so the cell that was assumed to have nothing to say had been shipping an
    # unloadable binary. The default `directories` is right here: this tree keeps its binaries in
    # `bin` and `lib`, measured at 135 files either way against a root scan.
    #
    # It has now caught the same thing on macOS — `libpq-oauth-18.dylib` against a libcurl EDB does
    # not ship — and the answer was the same one, for the same reason, in `NOT_SHIPPED`. Two of the
    # three routes have been checked and corrected by *removal* rather than by rewriting, which is
    # what "checked and not corrected" was always going to mean in practice: the correction available
    # to a recipe that must not touch a signature is to decline to ship the file.
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
