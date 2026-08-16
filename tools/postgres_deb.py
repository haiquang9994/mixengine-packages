#!/usr/bin/env python3
"""Assemble a relocatable PostgreSQL for Linux out of the ``.deb`` packages the project publishes.

**The cell P7's evaluation moved rather than filled.** EDB's ``linux-x64-binaries.tar.gz`` answers
403 at every version and the PostgreSQL project itself publishes source and nothing else — so the
route is the one MariaDB's aarch64 cell already takes, and here it covers *both* Linux cells rather
than one: ``apt.postgresql.org`` is run by the same people who tag the releases, and it builds
``amd64`` and ``arm64`` alike.

It is also better *checked* than the archives P7 borrows. EDB publishes no digest at all; this
repository states one at two removes — the suite's ``Release`` file gives the SHA256 of the
``Packages`` index, and ``Packages`` gives the SHA256 of every ``.deb`` in it. Both are followed
here, so the only thing taken on trust is TLS to the publisher's own host, and the failure of any
link in the chain is a stopped run rather than a quiet substitution.

**The archive host and not the live one, and this is the whole reason a blueprint can pin a
version.** ``apt.postgresql.org`` keeps roughly the last three minors of each major and drops the
rest: at the time of writing its ``jammy-pgdg`` suite offers 18.3, 18.4 and 18.6 and nothing before
them. ``apt-archive.postgresql.org`` keeps every build ever pushed — twenty-five of PostgreSQL 14
alone — under ``<suite>-pgdg-archive``, and it is still being written to daily, so nothing is traded
away by using it for current versions too. The same trade ``mariadb_deb.py`` makes between
``deb.mariadb.org`` and ``archive.mariadb.org``, for the same promise.

**The tree keeps Debian's ``/usr`` shape, and that is a requirement rather than laziness.** This is
where PostgreSQL differs from MariaDB in a way that reverses the answer. MariaDB is told where it
lives — ``--basedir`` — so its ``.deb`` payload can be rearranged into upstream's own bintar layout
and everything still resolves. PostgreSQL is told nothing and *works it out*, in
``make_relative_path`` in ``src/port/path.c``: it takes the ``bindir`` compiled into the binary,
strips the part it shares with the ``sharedir`` compiled into the binary, and then requires the
directory it is *actually* running from to end in what is left. Debian configures
``--bindir=/usr/lib/postgresql/18/bin --datadir=/usr/share/postgresql/18``, so what is left is
``lib/postgresql/18/bin`` — and a ``postgres`` moved to ``<root>/bin`` does not end in that, the
match fails, and the binary falls back to the absolute ``/usr/share/postgresql/18`` that no artifact
has. It would start; ``initdb`` would fail on a machine with no PostgreSQL installed, and succeed on
the packager's. So the layout is preserved exactly and ``bin`` is laid over it as a symlink, which
``find_my_exec`` resolves before it measures anything — one shape for MixEngine, upstream's own
shape for upstream's own binaries.

**One thing this cell needs from the machine that the other cells carry.** Debian builds with
``--with-system-tzdata``, so the timezone database is *not* in the archive and the compiled-in
``/usr/share/zoneinfo`` is the one path PostgreSQL never relocates. EDB's archives ship 646 files of
their own under ``share/timezone``; these do not, and cannot be made to — copying the files in would
produce a directory the server does not read. It is named in ``requires`` beside the glibc floor,
because a dependency a user can install is a fact to state rather than a reason to refuse.

``dpkg-deb`` is the one tool here that is not Python, for the reason ``mariadb_deb.py`` gives: it is
on every machine that can install a ``.deb``, and unpacking an ``ar`` archive containing a
``tar.zst`` by hand would need a decompressor Python 3.12 has not got.

Python 3 stdlib only otherwise, by policy: this runs on a GitHub runner with nothing installed.
"""

from __future__ import annotations

import argparse
import bz2
import hashlib
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import borrow  # noqa: E402  — siblings, and this directory is not importable as a package
import postgres  # noqa: E402
import postgres_smoke  # noqa: E402
import relocate  # noqa: E402

# See the module docstring: the host that keeps everything, rather than the one that keeps what is
# current. Both are the PostgreSQL project's own.
ARCHIVE = "https://apt-archive.postgresql.org/pub/repos/apt"

# **Jammy, and the argument is `mariadb_deb.py`'s rather than a new one.** The glibc floor of a
# finished artifact is the highest floor of anything inside it — upstream's binaries *and* every
# library bundled beside them, which come from the runner — so the suite and the runner are one
# choice. 22.04 measures out at 2.35 and takes in Debian 12; a noble suite on a noble runner would
# publish a PostgreSQL that refuses to start on a machine somebody develops on. Using the same suite
# as the MariaDB cells means MixEngine's Linux artifacts have one floor rather than two.
SUITE = "jammy-pgdg-archive"

# What one PostgreSQL is, in Debian's split. Three packages and no more:
#
#   postgresql-<major>          the server, and every contrib module and control file with it —
#                               Debian stopped shipping a separate `postgresql-contrib` at 10
#   postgresql-client-<major>   psql, pg_dump, pg_isready and the rest of the client half
#   libpq5                      what the client programs link against, and the one piece with a
#                               name that does not carry the major. It is built from each major's
#                               own source, so the build of it that matches this server exists and
#                               is what gets taken — the alternative is a client talking to a
#                               server through a libpq from a different release.
# **And `postgresql-<major>-jit` is not among them, which is a parity decision rather than a size
# one.** It is PostgreSQL's LLVM expression compiler, and Debian is the only publisher here that
# offers one: EDB's Windows archive contains no JIT provider at all, and its macOS archive contains
# only the headers, which this repository drops with the rest of the SDK. Taking it on Linux alone
# would make `jit = on` — PostgreSQL's own default — mean *compile the query* on one cell of a
# version and *do not* on the other two, with no error and no log line either way. It also drags a
# 90 MB LLVM runtime in behind it. `parity.py` could never catch this: a JIT provider is neither a
# command nor an extension, so it is the rule applied by hand where the check cannot reach.
#
# `postgresql-plperl-<major>`, `-plpython3-` and `-pltcl-` are absent for the reason
# `postgres.NOT_SHIPPED` gives about the same three languages on the EDB archives; `-doc-`,
# `-server-dev-` and every `-dbgsym-` for the reasons `postgres.PRUNE` and `postgres.DEBRIS` give.
# Debian having split each of them into its own package is what makes this list an argument the
# other route has to make with a delete.
PACKAGES = ("postgresql-{major}", "postgresql-client-{major}", "libpq5")

# `usr/…` in the unpacked packages -> where it goes in the artifact. The first two keep Debian's own
# depth for the reason the module docstring gives at length; only the `usr/` prefix comes off.
MOVES = (
    ("usr/lib/postgresql/{major}", "lib/postgresql/{major}"),
    ("usr/share/postgresql/{major}", "share/postgresql/{major}"),
    # The translated messages, which EDB's archives carry too — 24 languages of them. A cell whose
    # server answers in English while its siblings answer in the user's own language is a version
    # meaning two things in the most literally visible way there is.
    ("usr/share/locale", "share/locale"),
)

# libpq, out of the architecture-triplet directory whose name differs per cell, into the one library
# directory `relocate.bundle` will fill with everything else.
LIBRARY_GLOBS = ("usr/lib/*/libpq.so*",)


def run(*command: str, timeout: int = 1800) -> str:
    print("$ " + " ".join(command), flush=True)
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise SystemExit(f"{command[0]} exited {result.returncode}\n{result.stderr.strip()}")
    return result.stdout


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def index(arch: str) -> tuple[list[dict[str, str]], str]:
    """The suite's ``Packages``, checked against the digest its ``Release`` file states.

    Answers the stanzas and the digest, the second so that the manifest can name what this run
    verified everything else against. Debian's control format is RFC-822 with continuations, and
    only the fields this recipe reads are kept; a continuation belongs to whatever field preceded
    it and ``Description`` is the only one here that ever has them.

    **The `Release` file is fetched first and is not decoration.** Without it the `.deb` digests
    below are checked against a document nothing checked, which is a chain that proves the download
    was not corrupted and nothing about where it came from. With it there is one document to trust —
    and it is the one the repository signs, so a later version of this recipe that grows a GnuPG
    dependency has somewhere to attach it.
    """
    release = borrow.fetch(f"{ARCHIVE}/dists/{SUITE}/Release", timeout=300).decode("utf-8", "replace")
    wanted = f"main/binary-{arch}/Packages.bz2"
    stated = None
    section = None
    for line in release.splitlines():
        if line and line[0] not in " \t":
            section = line.split(":", 1)[0]
            continue
        pieces = line.split()
        if section == "SHA256" and len(pieces) == 3 and pieces[2] == wanted:
            stated = pieces[0]
            break
    if stated is None:
        raise SystemExit(f"{SUITE}'s Release file states no SHA256 for {wanted}")

    compressed = borrow.fetch(f"{ARCHIVE}/dists/{SUITE}/{wanted}", timeout=900)
    actual = digest(compressed)
    if actual != stated:
        raise SystemExit(
            f"{wanted} hashes to {actual} and the Release file states {stated}"
        )
    print(f"{wanted}: {len(compressed):,} bytes, sha256 matches the suite's Release file")

    stanzas, current = [], {}
    for line in bz2.decompress(compressed).decode("utf-8", "replace").splitlines():
        if not line.strip():
            if current:
                stanzas.append(current)
                current = {}
            continue
        if line[0] in " \t":
            continue
        field, _, value = line.partition(":")
        if field in ("Package", "Version", "Filename", "SHA256", "Depends"):
            current[field] = value.strip()
    if current:
        stanzas.append(current)
    if not stanzas:
        raise SystemExit(f"{wanted} parsed to nothing; the repository's format has changed")
    return stanzas, stated


def order(version: str) -> tuple[int, ...]:
    """A Debian version as something that compares numerically. Ordering only, never equality.

    ``18.6-1.pgdg22.04+2`` against ``18.6-1.pgdg22.04+1``: the same PostgreSQL rebuilt for a
    packaging fix, and the newer one is wanted. Every digit group in order is enough to say which
    that is, and it is deliberately *not* Debian's own comparison algorithm — the strings being
    compared here are always two builds of one upstream version by one packager, which is the narrow
    case where counting numbers is exactly right and `~` never appears.
    """
    return tuple(int(piece) for piece in re.findall(r"\d+", version))


def wanted(stanzas: list[dict[str, str]], version: str, major: str) -> dict[str, dict[str, str]]:
    """The one stanza per package that belongs to *version*, at a single packaging build.

    A suite's ``Packages`` holds every build ever pushed to it, so the version has to be matched and
    then the newest *build* of it taken: ``18.6-1.pgdg22.04+1`` and ``+2`` are the same PostgreSQL
    packaged twice. All three packages are then required at exactly the build the server was found
    at, which is what Debian's own ``Depends`` says between the first two and what makes the third —
    ``libpq5``, whose name carries no major at all — unambiguous.
    """
    names = [name.format(major=major) for name in PACKAGES]
    server = names[0]

    builds: dict[str, dict[str, dict[str, str]]] = {}
    for stanza in stanzas:
        name = stanza.get("Package", "")
        if name not in names:
            continue
        if not stanza.get("Version", "").startswith(f"{version}-"):
            continue
        builds.setdefault(stanza["Version"], {})[name] = stanza

    if not builds:
        borrow.unavailable(
            f"{SUITE} has no {server} at {version}. Upstream tags a release before Debian packages "
            f"it, so this is what a few days between the two looks like; it is also what a major "
            f"whose packages this suite never carried looks like."
        )
    build = max(builds, key=order)
    found = builds[build]

    missing = [name for name in names if name not in found]
    if missing:
        raise SystemExit(
            f"{SUITE} has {server} {build} and no {', '.join(missing)} at that build. One "
            f"PostgreSQL is these three packages together, and a server packaged without the "
            f"client it Depends on is a repository mid-push rather than a cell to publish."
        )
    print(f"{version} is packaged as {build}")
    return found


def system_libraries(stanzas: dict[str, dict[str, str]]) -> list[str]:
    """The libraries these packages name, read out of their own ``Depends`` fields.

    Bundling copies a library **from this machine**, so everything the packages depend on has to be
    installed here first, and which ones those are is stated by the packages rather than guessable —
    the argument `mariadb_deb.system_libraries` makes, and the same code, because the fact is about
    Debian packaging rather than about either database. Only the `lib*` names are taken: `locales`,
    `ssl-cert`, `tzdata` and `debconf` are a system's own conventions rather than anything a
    relocatable tree loads.
    """
    names: set[str] = set()
    for stanza in stanzas.values():
        for clause in stanza.get("Depends", "").split(","):
            first = clause.split("|")[0].strip()
            if not first:
                continue
            name = first.split()[0]
            # Not `libpq5`: it is in this archive already, and installing the distribution's copy
            # would put a second one on the machine for bundling to choose between.
            if name.startswith("lib") and name != "libpq5":
                names.add(name)
    return sorted(names)


def install(names: list[str]) -> None:
    """Put those libraries on the machine, so that there is something to bundle."""
    if not names:
        return
    print(f"installing {len(names)} librar{'y' if len(names) == 1 else 'ies'} these packages name: "
          f"{', '.join(names)}")
    run("sudo", "apt-get", "update", "-qq", timeout=900)
    run("sudo", "apt-get", "install", "-y", "--no-install-recommends", *names, timeout=1800)


def unpack(stanzas: dict[str, dict[str, str]], work: Path) -> Path:
    """Download each package, check it against the index, and unpack them all into one root."""
    root = work / "deb"
    root.mkdir(parents=True, exist_ok=True)
    for name in sorted(stanzas):
        stanza = stanzas[name]
        url = f"{ARCHIVE}/{stanza['Filename']}"
        archive = work / Path(stanza["Filename"]).name
        print(f"borrowing {url}")
        archive.write_bytes(borrow.fetch(url, timeout=900))
        actual = borrow.sha256(archive)
        if actual != stanza["SHA256"]:
            raise SystemExit(
                f"{archive.name} hashes to {actual}, and the {SUITE} Packages index states "
                f"{stanza['SHA256']}"
            )
        # Into one root on purpose: these packages are designed to overlay each other on a real
        # system, and nothing in them collides.
        run("dpkg-deb", "-x", str(archive), str(root))
    return root


def rearrange(root: Path, work: Path, major: str) -> tuple[Path, list[str]]:
    """Lift the payload out of ``usr/`` without changing its shape, and say what was left behind.

    Answers the tree and what was taken out of it. The second half is ``postgres.prune``'s — what a
    PostgreSQL artifact does not contain is decided once for all three routes — and this function
    contributes only what is true of a ``.deb`` and of nothing else.
    """
    tree = work / "tree"
    for source, destination in MOVES:
        origin = root / source.format(major=major)
        if not origin.is_dir():
            continue
        target = tree / destination.format(major=major)
        target.parent.mkdir(parents=True, exist_ok=True)
        # `copytree` rather than `move`, `symlinks=True`, and merging into whatever is already
        # there: two packages both install into `usr/lib/postgresql/<major>/bin` and the second
        # must not replace the first.
        shutil.copytree(origin, target, symlinks=True, dirs_exist_ok=True)

    libraries = tree / "lib"
    libraries.mkdir(parents=True, exist_ok=True)
    for pattern in LIBRARY_GLOBS:
        for path in sorted(root.glob(pattern)):
            if path.is_file() or path.is_symlink():
                shutil.copy2(path, libraries / path.name, follow_symlinks=False)

    # **Debian states each package's licensing in `usr/share/doc/<package>/copyright`**, and nothing
    # else in these packages says it at all — there is no `COPYING` at the root the way EDB ships a
    # `server_license.txt`. `licenses/` is the spelling every other recipe here uses, and
    # `relocate.bundled_licences` will add the runner's libraries to the same directory later.
    licences = tree / "licenses"
    for copyright_file in sorted(root.glob("usr/share/doc/*/copyright")):
        licences.mkdir(parents=True, exist_ok=True)
        shutil.copy2(copyright_file, licences / f"{copyright_file.parent.name}-copyright")

    removed = postgres.prune(tree)
    if removed:
        print(f"not shipping {len(removed)} path(s): {', '.join(removed[:12])}"
              f"{' …' if len(removed) > 12 else ''}")
    return tree, removed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--version", required=True,
        help="a major (18), an exact version (18.6), or 'latest'",
    )
    parser.add_argument("--out", default=Path("dist"), type=Path)
    arguments = parser.parse_args()

    operating_system, arch = borrow.host("PostgreSQL")
    if operating_system != "linux":
        raise SystemExit(
            "this recipe unpacks Linux .deb packages. Windows and macOS are borrowed from "
            "EnterpriseDB by postgres.py."
        )
    if not shutil.which("dpkg-deb"):
        raise SystemExit("dpkg-deb is not on this machine, and it is how a .deb is unpacked")

    # **Which version exists is asked of postgresql.org, not of the repository**, so that this
    # recipe and the EDB one cannot disagree about what `18` means — even though the files
    # themselves come from the repository. It is also where the end-of-life date comes from, and
    # `postgres.FLOOR` applies here as it does there: 13's absence is decided by what EDB's macOS
    # archive is, and a Linux 13 would be a version this catalogue offers on one row only.
    stated = postgres.series()
    if arguments.version == "latest":
        key = max(stated)
        version = postgres.newest(stated[key])
    else:
        asked = borrow.parts(arguments.version)
        key = asked[:1]
        if key not in stated:
            raise SystemExit(
                f"postgresql.org lists no major {key[0]} at or above {postgres.FLOOR[0]}. It "
                f"offers {', '.join(str(other[0]) for other in sorted(stated))}."
            )
        version = arguments.version if len(asked) > 1 else postgres.newest(stated[key])
    major = version.split(".")[0]
    if version != arguments.version:
        print(f"{arguments.version} resolved to {version}")
    eol = stated[key].get("eolDate")
    if eol:
        print(f"upstream supports this major until {eol}")

    work = Path(tempfile.mkdtemp(prefix="mixengine-postgres-deb-"))
    debian_arch = "arm64" if arch == "aarch64" else "amd64"
    stanzas, verified = index(debian_arch)
    chosen = wanted(stanzas, version, major)
    install(system_libraries(chosen))
    tree, removed = rearrange(unpack(chosen, work), work, major)

    added = relocate.bundle(tree, search=[tree / "lib"])
    if added:
        print(f"bundled {len(added)} librar{'y' if len(added) == 1 else 'ies'}: "
              f"{', '.join(sorted(added))}")
    relocate.bundled_licences(tree, added)

    # **After bundling, and the order is a finding rather than a preference.** `$ORIGIN` in an ELF
    # search path is the *resolved* directory of the object being loaded, so a `bin` symlink laid
    # first makes `relocate.rewrite` compute `$ORIGIN/../lib` from a path the loader will never use
    # — pointing at `lib/postgresql/<major>/lib`, which holds the extension modules and no bundled
    # library at all. Everything would resolve on this machine, where the distribution's own copies
    # are still installed, and nothing would resolve on a user's.
    link = tree / "bin"
    if not link.exists():
        link.symlink_to(Path("lib") / "postgresql" / major / "bin", target_is_directory=True)

    provides = postgres_smoke.describe(tree, windows=False)
    available = postgres_smoke.extensions(tree)
    modules = postgres_smoke.where(tree, postgres_smoke.MODULES)

    manifest = {
        "schema": 1,
        "kind": "postgres",
        "version": version,
        "os": operating_system,
        "arch": arch,
        "source": "borrowed",
        "upstream": {
            "project": "PostgreSQL Global Development Group (apt.postgresql.org)",
            "release": chosen[f"postgresql-{major}"]["Version"],
            "url": f"{ARCHIVE}/dists/{SUITE}/main/binary-{debian_arch}/Packages.bz2",
            "sha256": verified,
            # Two links, and both were followed. Worth spelling out beside the EDB recipe's
            # admission that it has none: these are the same database and only one of them can be
            # checked against something its publisher wrote down.
            "verified_against": (
                f"the {SUITE} Release file (SHA256 of the Packages index), and that index's SHA256 "
                f"for each .deb, over HTTPS to apt-archive.postgresql.org"
            ),
            "variant": f"{', '.join(sorted(chosen))} lifted out of /usr with their own layout kept",
            "added": sorted(f"lib/{library}" for library in added),
        },
        "provides": provides,
        "extensions": {"shared": available},
    }
    if modules:
        manifest["extension_dir"] = modules.relative_to(tree).as_posix()
    if removed:
        borrow.declare(tree, manifest, removed=removed)

    requires = {}
    measured = relocate.floor(tree)
    if measured:
        requires[measured[0]] = measured[1]
        print(f"needs {measured[0]} {measured[1]} or newer")
    # See the module docstring. Stated rather than solved, because there is nothing to solve: a
    # server built `--with-system-tzdata` reads an absolute path it will not relocate, so shipping
    # the files would ship a directory it never opens.
    requires["tzdata"] = (
        "the system timezone database at /usr/share/zoneinfo — Debian builds PostgreSQL "
        "--with-system-tzdata, so unlike the Windows and macOS cells this one does not carry its own"
    )
    manifest["requires"] = requires

    elsewhere = borrow.moved(tree)
    problems = relocate.verify(elsewhere)
    for problem in problems:
        print(f"error: {problem}", file=sys.stderr)
    if problems:
        raise SystemExit("the relocated tree reaches outside itself")

    manifest["smoke"] = {
        "relocated": True,
        "ran": postgres_smoke.server(elsewhere, version, provides, windows=False),
    }
    borrow.discard(elsewhere)

    print(f"{len(available)} extension(s) available: {', '.join(available)}")
    borrow.publish(tree, manifest, arguments.out, "tar.zst")
    shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
