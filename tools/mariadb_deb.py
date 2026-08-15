#!/usr/bin/env python3
"""Assemble a relocatable MariaDB for Linux ARM64 out of the ``.deb`` packages upstream does publish.

**The cell that exists and is shaped wrong.** MariaDB publishes a binary tarball for Linux on x86_64
and none for aarch64 — not in any release from 10.2 to 13.1 — but its own apt repository carries
``arm64`` for every current series. So the payload is upstream's, built by upstream, signed into
upstream's repository; what is missing is only the *shape*, because a ``.deb`` is laid out for a
system that installs it into ``/usr`` and MixEngine installs an artifact into a directory of its own.

That makes this a borrow with a rearrangement rather than a build, and it is worth being precise
about which parts are which:

*Borrowed*: every binary, plugin, error-message file and character set — taken out of the packages
named in ``PACKAGES`` and ``OPTIONAL`` below, each verified against the SHA256 in the repository's own
``Packages`` index. Which packages those are is itself a decision the parity rule drives: a bintar is
one file containing everything, and matching it means naming each piece upstream split out.

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
#   mariadb-backup        upstream packages it separately, and the other five cells have it — see
#                         the parity rule in the README. Leaving it out produced a Linux ARM64
#                         artifact missing a tool every other artifact of the same version had,
#                         which is exactly the difference that rule exists to prevent.
PACKAGES = (
    "mariadb-server-core",
    "mariadb-server",
    "mariadb-client-core",
    "mariadb-client",
    "mariadb-common",
    "libmariadb3",
    "mariadb-backup",
)

# **The five compression providers, which are optional here and are not optional to a user.** InnoDB
# reads `innodb_compression_algorithm` at startup and loads a provider plugin for whatever it names;
# a bintar has all five compiled in beside the server, and a `.deb` installation gets them by
# installing five more packages. Left out, the same `my.cnf` with `innodb_compression_algorithm=lz4`
# starts a server on five cells and fails on the sixth — a difference nobody chose, in the one
# direction the parity rule is easy to miss, because the ARM64 artifact was *smaller* and looked
# better for it.
#
# Optional in the sense the rest of `PACKAGES` is not: compression providers arrived in 10.7, so the
# 10.6 line has no such package and never will. A missing one is reported and skipped rather than
# turning the whole cell into an empty one — which is what `wanted` does with anything required.
#
# **The compat packages are here for the same reason and are not about size at all.** Upstream's
# bintar offers every tool under its old `mysql*` name as a symlink beside the new one — `mysqldump`
# beside `mariadb-dump` — and so does the Windows zip, and so does a source build. Debian splits
# those twenty-three symlinks into `mariadb-client-compat` and `mariadb-server-compat`, so leaving
# them out gave one cell in six where `mysqldump` is not a command. They are relative links to files
# already in `bin/` and cost nothing; whichever of them names something this artifact does not carry
# is swept by `mariadb.prune` along with anything else that dangles.
OPTIONAL = (
    "mariadb-plugin-provider-bzip2",
    "mariadb-plugin-provider-lz4",
    "mariadb-plugin-provider-lzma",
    "mariadb-plugin-provider-lzo",
    "mariadb-plugin-provider-snappy",
    "mariadb-client-compat",
    "mariadb-server-compat",
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

# **The client plugins, which arrive as symlinks pointing out of the tree.** `mariadb-server` installs
# `dialog.so`, `client_ed25519.so`, `caching_sha2_password.so`, `mysql_clear_password.so` and
# `sha256_password.so` into `/usr/lib/mysql/plugin` as relative links to
# `../../<triplet>/libmariadb3/plugin/`, which is where `libmariadb3` really keeps them. Moved into
# this layout the links still say `../../<triplet>/…` and there is no such directory, so every Linux
# ARM64 artifact so far has shipped five plugins that resolve to nothing — invisible because a
# dangling link answers False to `exists()`, which is the same blind spot that kept `mysql_ldb` alive
# in `mariadb.prune`. Copying the real files over them is the whole fix, and it also brings across
# `parsec.so`, which the links did not name and the x86_64 bintar has.
PLUGIN_GLOBS = ("usr/lib/*/libmariadb3/plugin/*",)

# **Only what is wrong with the *shape*.** What a MariaDB artifact does not contain is decided in
# `mariadb.PRUNE` and the pattern lists beside it, and `rearrange` runs those too; this list is the
# part that is true of a `.deb` and of nothing else — the `usr/` and `etc/` trees left behind once
# their contents have been moved, a systemd unit that registers the system service MixEngine
# supervises instead, and the packaging metadata Debian requires and a relocatable tree has no reader
# for.
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
    aliases = {alias: name
               for name in PACKAGES + OPTIONAL
               for alias in (name, f"{name}-{series}")}

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
    absent = [name for name in OPTIONAL if name not in found]
    if absent:
        print(f"this series publishes no {', '.join(absent)}; whatever each provides will be "
              f"missing from this artifact and present in the other five")
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
    for name in [name for name in PACKAGES + OPTIONAL if name in stanzas]:
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
        # Into one root on purpose: these packages are designed to overlay each other on a real
        # system, and nothing in them collides.
        run("dpkg-deb", "-x", str(archive), str(root))
    return root


def rearrange(root: Path, work: Path) -> tuple[Path, list[str]]:
    """Turn an installed-into-``/usr`` layout into the one upstream's own bintar publishes.

    Answers the tree and what was taken out of it — the second half being ``mariadb.prune``'s, which
    is the same list of things MixEngine does not ship that the bintar goes through. This recipe used
    to run only ``PRUNE`` below, and the difference was visible in the artifact: PAM plugins that
    cannot work without a setuid helper, and Galera scripts with no provider to talk to, in the one
    cell whose payload comes from packaging rather than from a tarball.
    """
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

    # **`share/mariadb` pointing at `share`, because the two halves of MariaDB disagree with each
    # other.** `mariadb-install-db` reads its bootstrap SQL from `$basedir/share/mariadb` — that
    # path is written into the script — while `mariadbd` looks for `english/errmsg.sys` under
    # `$basedir/share`, which is where a bintar puts everything. Upstream's packaging satisfies both
    # by installing into `/usr/share/mariadb` and setting `basedir=/usr`; a relocatable tree laid out
    # like a bintar satisfies the server and not the script.
    #
    # A symlink is the whole of the fix, costs nothing, and keeps one copy of a 20 MB message
    # directory. Made relative so it survives the tree being moved, which is the point of all of this.
    share = tree / "share"
    if share.is_dir() and not (share / "mariadb").exists():
        (share / "mariadb").symlink_to(".", target_is_directory=True)

    # And `sbin` pointing at `bin`, for the same reason one directory along: the packaging installs
    # the server as `/usr/sbin/mariadbd` and `mariadb-install-db` looks for it under `$basedir/sbin`,
    # while a bintar — and therefore `mariadb_smoke.LAYOUT`, and therefore MixEngine — has one `bin`.
    # Moving the file to `bin` is what makes the artifact one shape; this is what makes upstream's
    # own first-run script still find it.
    if (tree / "bin").is_dir() and not (tree / "sbin").exists():
        (tree / "sbin").symlink_to("bin", target_is_directory=True)

    libraries = tree / "lib"
    libraries.mkdir(parents=True, exist_ok=True)
    for pattern in LIBRARY_GLOBS:
        for path in sorted(root.glob(pattern)):
            if path.is_file() or path.is_symlink():
                shutil.copy2(path, libraries / path.name, follow_symlinks=False)

    # After the move, so that the real file lands on top of the link that named it. See PLUGIN_GLOBS.
    for pattern in PLUGIN_GLOBS:
        for path in sorted(root.glob(pattern)):
            if not path.is_file() or path.is_symlink():
                continue
            (libraries / "plugin").mkdir(parents=True, exist_ok=True)
            destination = libraries / "plugin" / path.name
            if destination.is_symlink() or destination.exists():
                destination.unlink()
            shutil.copy2(path, destination)

    # **The licence text, before `PRUNE` takes `share/doc` and the whole of `usr/`.** Debian states
    # each package's licensing in `usr/share/doc/<package>/copyright`, and this recipe was throwing
    # all of them away with the manual pages — publishing GPL binaries with no licence beside them,
    # which the other five cells do not do: the bintar carries `COPYING` and `THIRDPARTY` at its root
    # and `mariadb_build.collect_licences` writes a `licenses/` directory. `licenses/` is the
    # spelling the compiled cells use, so this uses it too.
    licences = tree / "licenses"
    for copyright_file in sorted(root.glob("usr/share/doc/*/copyright")):
        licences.mkdir(parents=True, exist_ok=True)
        shutil.copy2(copyright_file, licences / f"{copyright_file.parent.name}-copyright")

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

    # Last, so that it runs over the finished layout rather than over `usr/`: the paths and patterns
    # in `mariadb.PRUNE`, `NOT_SHIPPED`, `GALERA` and `DEBRIS` are all written against the bintar
    # shape this function has just produced.
    removed = mariadb.prune(tree)
    if removed:
        print(f"not shipping {len(removed)} paths: {', '.join(removed)}")
    return tree, removed


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
    tree, dropped = rearrange(unpack(version, stanzas, work), work)

    provides = mariadb_smoke.describe(tree, windows=False)
    # Shared with the bintar recipe rather than reimplemented: a plugin needing a library nobody has
    # is the same fact whichever route the payload took here. See `mariadb.unshippable_plugins`.
    dropped += mariadb.unshippable_plugins(tree)
    # Expected to find nothing and run anyway: Debian strips its binaries and ships the symbols in a
    # separate `-dbg` package, which is the whole reason this route produces an archive an order of
    # magnitude smaller than the bintar one. Calling it keeps that a measurement rather than a
    # belief about somebody else's packaging policy.
    stripped = mariadb.strip_debug(tree)
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
            "variant": f"{', '.join(name for name in PACKAGES + OPTIONAL if name in stanzas)} "
                       f"rearranged into upstream's own bintar layout",
            "added": sorted(f"lib/{library}" for library in added),
            **({"removed": sorted(set(dropped))} if dropped else {}),
            **({"stripped": stripped} if stripped else {}),
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
