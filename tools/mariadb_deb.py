#!/usr/bin/env python3
"""Assemble a relocatable MariaDB for Linux ARM64 out of the ``.deb`` packages upstream does publish.

**The cell that exists and is shaped wrong.** MariaDB publishes a binary tarball for Linux on x86_64
and none for aarch64 — not in any release from 10.2 to 13.1 — but its own apt repository carries
``arm64`` for every current series. So the payload is upstream's, built by upstream, signed into
upstream's repository; what is missing is only the *shape*, because a ``.deb`` is laid out for a
system that installs it into ``/usr`` and MixEngine installs an artifact into a directory of its own.

That makes this a borrow with a rearrangement rather than a build, and it is worth being precise
about which parts are which:

*Borrowed*: every binary, plugin, error-message file and character set — taken out of upstream's own
``mariadb-server``, ``mariadb-server-core``, ``mariadb-client``, ``mariadb-client-core``,
``mariadb-common`` and ``libmariadb3`` packages, each verified against the SHA256 in the repository's
own ``Packages`` index.

*This repository's*: the layout — ``usr/sbin/mariadbd`` and ``usr/bin/*`` become ``bin/``,
``usr/lib/mysql/plugin`` becomes ``lib/plugin``, ``usr/share/mysql`` becomes ``share`` — which is
exactly the layout upstream's own bintar uses, so that ``mariadb_smoke`` and MixEngine see one shape
whichever recipe produced the tree. And the libraries: a ``.deb`` names ``libssl.so.3`` and expects
the distribution to have supplied it, so ``relocate.bundle`` copies each one in and rewrites every
reference to ``$ORIGIN``.

**Jammy rather than the runner's own release.** The packages are taken from the ``jammy`` suite
(Ubuntu 22.04) and built on a 22.04 runner, because the glibc floor of the finished artifact is the
highest floor of anything in it — the binaries *and* every library bundled beside them. Taking noble
packages on a noble runner would publish an artifact that refuses to start on Debian 12, which is a
machine somebody runs a local development environment on. The floor is measured rather than claimed;
see ``requires`` in the manifest.

``dpkg-deb`` is the one tool here that is not Python. It is on every machine that can install a
``.deb`` at all, which is the same machine this recipe is restricted to, and unpacking an ``ar``
archive containing a ``tar.zst`` by hand would be a compression format Python 3.12 has no reader for.

Python 3 stdlib only otherwise, by policy: this runs on a GitHub runner with nothing installed.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import borrow  # noqa: E402  — siblings, and this directory is not importable as a package
import mariadb  # noqa: E402
import mariadb_smoke  # noqa: E402
import relocate  # noqa: E402

# Upstream's own repository, per release, kept forever beside the tarballs. `deb.mariadb.org` carries
# only what is current; this carries the exact version being asked for, which is the whole reason the
# index can promise that a blueprint pinning 11.8.8 keeps working.
REPO = "https://archive.mariadb.org/mariadb-{version}/repo/ubuntu"

# See the module docstring: the suite is chosen for the floor it produces, not for the runner.
SUITE = "jammy"

# What has to be unpacked for a server to run, and why each is not optional.
#
#   mariadb-server-core   mariadbd itself, and the storage engine plugins beside it
#   mariadb-server        the first-run job — mariadb-install-db — and the SQL it feeds a bootstrap
#                         server, which is the one thing a data directory cannot be created without
#   mariadb-client-core   mariadb, mariadb-admin, mariadb-dump: the client half of every check T33
#                         performs against a running server
#   mariadb-client        mariadb-upgrade, and the scripts that go with a client installation
#   mariadb-common        the character sets. A server whose `charsets/Index.xml` is missing starts
#                         and then refuses every connection that asks for a collation.
#   libmariadb3           what the client programs link against; the server does not use it
PACKAGES = (
    "mariadb-server-core",
    "mariadb-server",
    "mariadb-client-core",
    "mariadb-client",
    "mariadb-common",
    "libmariadb3",
)

# `usr/…` in the unpacked package -> where it goes in the artifact. The destination side is upstream's
# own bintar layout rather than an invention: `mariadb_smoke.LAYOUT` looks in `bin/`, mariadbd derives
# its plugin directory and its error messages from `basedir`, and an artifact that arranged itself
# differently would need a daemon that knows which recipe made it.
MOVES = (
    ("usr/sbin", "bin"),
    ("usr/bin", "bin"),
    ("usr/lib/mysql/plugin", "lib/plugin"),
    # **Both spellings of the data directory, and the first one is the one that exists.** MariaDB's
    # own packages install their bootstrap SQL — `mariadb_system_tables.sql` and the rest, which
    # `mariadb-install-db` reads from `$basedir/share` — into `usr/share/mariadb`, not the
    # `usr/share/mysql` a bintar suggests. The compatibility spelling is kept for whichever series
    # still uses it; a missing source directory is skipped rather than being an error.
    ("usr/share/mariadb", "share"),
    ("usr/share/mysql", "share"),
)

# Copied as a whole directory rather than moved into the layout: the client libraries, which live
# under a triplet directory whose name differs per architecture.
LIBRARY_GLOBS = ("usr/lib/*/libmariadb.so*", "usr/lib/*/libmariadb3/*")

# Taken out for the same reason `mariadb.py` drops the test suite, plus the two a `.deb` carries that
# a relocatable tree cannot use: init scripts and systemd units name absolute paths and register a
# system service, which is precisely what MixEngine supervises instead.
PRUNE = ("usr", "etc", "lib/systemd", "share/man", "share/doc", "share/lintian", "share/bug")


def run(*command: str, cwd: Path | None = None, timeout: int = 1800) -> str:
    print("$ " + " ".join(command), flush=True)
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise SystemExit(f"{command[0]} exited {result.returncode}\n{result.stderr.strip()}")
    return result.stdout


def packages_index(version: str, arch: str) -> list[dict[str, str]]:
    """The suite's own ``Packages`` file, parsed into one dictionary per stanza.

    Debian's control format, which is RFC-822 with continuations. Only the four fields this recipe
    reads are kept — a continuation line belongs to whatever field preceded it, and ``Description``
    is the only one here that ever has them.
    """
    url = f"{REPO.format(version=version)}/dists/{SUITE}/main/binary-{arch}/Packages"
    text = borrow.fetch(url, timeout=300).decode("utf-8", "replace")
    stanzas, current = [], {}
    for line in text.splitlines():
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
        raise SystemExit(f"{url} parsed to nothing; the repository's format has changed")
    return stanzas


def upstream_version(stated: str) -> str:
    """A Debian version with its epoch removed, which is the only part that names a MariaDB release.

    Measured rather than assumed. These packages are versioned ``1:11.8.8+maria~ubu2204``: the
    ``1:`` is an *epoch*, Debian's way of saying "newer than anything before it whatever the numbers
    suggest", and it is not part of the upstream version at all. A prefix match that forgets it
    matches nothing, and the failure reads as "this suite has no MariaDB 11.8.8" about a suite that
    has exactly that.
    """
    return stated.split(":", 1)[-1]


def wanted(stanzas: list[dict[str, str]], version: str) -> dict[str, dict[str, str]]:
    """The one stanza per package that belongs to *version*, refusing to guess between two.

    A suite's ``Packages`` holds every version MariaDB ever pushed to it, so the version has to be
    matched rather than the newest taken: upstream's own versioning appends the distribution —
    ``11.8.8+maria~ubu2204`` — and a prefix match on ``11.8.8`` is exact enough to be unambiguous and
    loose enough not to encode that suffix here.
    """
    # **The core packages carry the series in their name on older lines** — `mariadb-server-core-10.6`
    # — and dropped it when MariaDB stopped co-installing two servers. Both spellings are accepted and
    # normalised to the plain one, so nothing downstream has to know which era a series is from. Found
    # by running this against 10.6, where the unsuffixed lookup reported two packages missing from a
    # repository that has both.
    series = ".".join(version.split(".")[:2])
    aliases = {alias: name for name in PACKAGES for alias in (name, f"{name}-{series}")}

    found: dict[str, dict[str, str]] = {}
    for stanza in stanzas:
        name = aliases.get(stanza.get("Package", ""))
        if name is None:
            continue
        if not upstream_version(stanza.get("Version", "")).startswith(f"{version}+"):
            continue
        if name in found and found[name]["Version"] != stanza["Version"]:
            raise SystemExit(
                f"the {SUITE} suite offers two builds of {name} {version}: "
                f"{found[name]['Version']} and {stanza['Version']}"
            )
        found[name] = stanza

    missing = [name for name in PACKAGES if name not in found]
    if missing:
        borrow.unavailable(
            f"MariaDB's {SUITE} repository has no {', '.join(missing)} at {version}. Not every "
            f"series is published for every Ubuntu suite."
        )
    return found


def system_libraries(stanzas: dict[str, dict[str, str]]) -> list[str]:
    """The libraries these packages depend on, read out of their own ``Depends`` fields.

    Bundling copies a library **from this machine**, so every library the packages name has to be
    installed here first — and which ones those are is stated by the packages rather than guessable.
    A hand-written list would have been wrong on the first run: `mariadbd` needs `liburing.so.2`,
    which no GitHub runner carries and which nothing about MariaDB suggests, and CI stopped on it
    exactly as it should have.

    Only the `lib*` names are taken. The rest of a server package's dependencies — `adduser`,
    `lsb-base`, `procps` — are a system's packaging conventions rather than anything a relocatable
    tree loads, and installing them would be asking a runner to become a machine running MariaDB.
    """
    names: set[str] = set()
    for stanza in stanzas.values():
        for clause in stanza.get("Depends", "").split(","):
            # `a | b` is Debian's "either of these"; the first is the packaging's own preference and
            # is what apt would pick unasked.
            first = clause.split("|")[0].strip()
            if not first:
                continue
            name = first.split()[0]
            # Not `libmariadb*`: those are in this archive already, and installing the distribution's
            # copy would put a second one on the machine for bundling to choose between.
            if name.startswith("lib") and not name.startswith("libmariadb"):
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


def unpack(version: str, stanzas: dict[str, dict[str, str]], work: Path) -> Path:
    """Download each package, check it against the index, and unpack them all into one root."""
    base = REPO.format(version=version)
    root = work / "deb"
    root.mkdir(parents=True, exist_ok=True)
    for name in PACKAGES:
        stanza = stanzas[name]
        url = f"{base}/{stanza['Filename']}"
        archive = work / Path(stanza["Filename"]).name
        print(f"borrowing {url}")
        archive.write_bytes(borrow.fetch(url, timeout=900))
        actual = borrow.sha256(archive)
        if actual != stanza["SHA256"]:
            raise SystemExit(
                f"{archive.name} hashes to {actual}, and the {SUITE} Packages index states "
                f"{stanza['SHA256']}"
            )
        # Into one root on purpose: these six packages are designed to overlay each other on a real
        # system, and nothing in them collides.
        run("dpkg-deb", "-x", str(archive), str(root))
    return root


def rearrange(root: Path, work: Path) -> Path:
    """Turn an installed-into-``/usr`` layout into the one upstream's own bintar publishes."""
    tree = work / "tree"
    for source, destination in MOVES:
        origin = root / source
        if not origin.is_dir():
            continue
        target = tree / destination
        target.mkdir(parents=True, exist_ok=True)
        for path in sorted(origin.iterdir()):
            # `copytree` rather than `move` for directories so that two sources can merge into one
            # destination — `usr/sbin` and `usr/bin` both become `bin/`.
            if path.is_dir() and not path.is_symlink():
                shutil.copytree(path, target / path.name, symlinks=True, dirs_exist_ok=True)
            else:
                shutil.copy2(path, target / path.name, follow_symlinks=False)

    libraries = tree / "lib"
    libraries.mkdir(parents=True, exist_ok=True)
    for pattern in LIBRARY_GLOBS:
        for path in sorted(root.glob(pattern)):
            if path.is_file() or path.is_symlink():
                shutil.copy2(path, libraries / path.name, follow_symlinks=False)

    for relative in PRUNE:
        pruned = tree / relative
        if pruned.is_dir():
            shutil.rmtree(pruned, ignore_errors=True)
        elif pruned.is_file():
            pruned.unlink()

    # A `.deb` ships its executables mode 755 and `dpkg-deb -x` preserves that, but a file copied
    # out of a data.tar as a symlink to `/etc/alternatives` points at a system path that will not
    # exist in the artifact. Those are the packaging's own indirection and the target is in the tree.
    for path in sorted(tree.rglob("*")):
        if path.is_symlink() and str(path.readlink()).startswith("/"):
            target = root / str(path.readlink()).lstrip("/")
            path.unlink()
            if target.exists():
                shutil.copy2(target, path)
    return tree


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True, help="a series (11.8), an exact version, 'latest'")
    parser.add_argument("--out", default=Path("dist"), type=Path)
    arguments = parser.parse_args()

    operating_system, arch = borrow.host("MariaDB")
    if operating_system != "linux":
        raise SystemExit(
            "this recipe unpacks Linux .deb packages. Windows and Linux on x86_64 are borrowed by "
            "mariadb.py; macOS and Windows on ARM64 are compiled by mariadb_build.py."
        )
    if not shutil.which("dpkg-deb"):
        raise SystemExit("dpkg-deb is not on this machine, and it is how a .deb is unpacked")

    # Which *version* exists is asked of the REST API, so that this recipe and the bintar one cannot
    # disagree about what `11.8` means — even though the files themselves come from the repository.
    series = mariadb.lines()
    if arguments.version == "latest":
        version = max(mariadb.get(f"{mariadb.API}/{series[max(series)]['release_id']}/")["releases"],
                      key=borrow.parts)
        eol = series[max(series)].get("release_eol_date")
    else:
        prefix = borrow.parts(arguments.version)[:2]
        matching = [key for key in series if key[:2] == prefix]
        if not matching:
            raise SystemExit(
                f"downloads.mariadb.org lists no stable {arguments.version}. It offers "
                f"{', '.join(series[key]['release_id'] for key in sorted(series))}."
            )
        catalogue = mariadb.get(f"{mariadb.API}/{series[matching[0]]['release_id']}/")["releases"]
        candidates = [name for name in catalogue
                      if borrow.parts(name)[:len(borrow.parts(arguments.version))]
                      == borrow.parts(arguments.version)]
        if not candidates:
            raise SystemExit(f"downloads.mariadb.org lists no {arguments.version}")
        version = max(candidates, key=borrow.parts)
        eol = series[matching[0]].get("release_eol_date")
    if version != arguments.version:
        print(f"{arguments.version} resolved to {version}")
    if eol:
        print(f"upstream supports this series until {eol}")

    work = Path(tempfile.mkdtemp(prefix="mixengine-mariadb-"))
    stanzas = wanted(packages_index(version, "arm64" if arch == "aarch64" else "amd64"), version)
    install(system_libraries(stanzas))
    tree = rearrange(unpack(version, stanzas, work), work)

    provides = mariadb_smoke.describe(tree, windows=False)
    added = relocate.bundle(tree, search=[tree / "lib"])
    if added:
        print(f"bundled {len(added)} librar{'y' if len(added) == 1 else 'ies'}: "
              f"{', '.join(sorted(added))}")

    manifest = {
        "schema": 1,
        "kind": "mariadb",
        "version": version,
        "os": operating_system,
        "arch": arch,
        "source": "borrowed",
        "upstream": {
            "project": "MariaDB/server",
            "release": stanzas["mariadb-server"]["Version"],
            "url": f"{REPO.format(version=version)}/dists/{SUITE}/main/binary-"
                   f"{'arm64' if arch == 'aarch64' else 'amd64'}/Packages",
            "sha256": stanzas["mariadb-server"]["SHA256"],
            "verified_against": (
                f"the {SUITE} Packages index (SHA256 per .deb) over HTTPS to archive.mariadb.org"
            ),
            "variant": f"{', '.join(PACKAGES)} rearranged into upstream's own bintar layout",
            "added": sorted(f"lib/{library}" for library in added),
        },
        "provides": provides,
    }

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
        "ran": mariadb_smoke.server(elsewhere, version, provides, windows=False),
    }
    borrow.discard(elsewhere)

    borrow.publish(tree, manifest, arguments.out, "tar")
    shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
