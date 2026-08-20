#!/usr/bin/env python3
"""What both MySQL recipes have to agree about: which versions exist, what upstream published for
each of them, who signed it, and what a MySQL tree is cut down to before it is packed.

**Oracle publishes no REST API and no ``versions.json``.** MariaDB states its catalogue and its
checksums in one document; MySQL states its catalogue in a rendered archive page and its checksums
in an MD5 nothing here would write into ``upstream.verified_against``. So the catalogue is parsed —
which is a cost, and it is smaller than the alternative of a version list kept in this repository
that goes stale the day a patch is published.

Two things measured on a runner rather than assumed, and both read backwards:

*Send urllib's default User-Agent and never a browser's.* Every archive URL carrying a query string
answers **403** to a browser ``User-Agent`` from a datacentre address and **200** to
``Python-urllib/3``. The local probe that found the 403 had been told to be polite and send one, so
the courteous choice is the one that gets blocked — which is the sort of thing
``building-from-source.md`` exists to record.

*A 200 is not an answer.* The archive returns ``200 text/html`` with a body reading "Technical
Difficulties" rather than a status a client can branch on, so :func:`page` reads what came back and
not only how it came back.

Nothing here packs anything. :mod:`mysql_borrow` takes the cells upstream publishes a binary for,
:mod:`mysql_build` compiles the ones it does not, and ``--plan`` tells the workflow which is which.

Python 3 stdlib only, by policy: this runs on a GitHub runner with nothing installed.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
from functools import lru_cache
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import borrow

ARCHIVE = "https://downloads.mysql.com/archives"
COMMUNITY = f"{ARCHIVE}/community/"
GET = f"{ARCHIVE}/get/p/23/file/"
CDN = "https://cdn.mysql.com/archives"

# The five lines this repository packs. 5.6 and 5.7 are here because an application maintained
# against one of them is exactly what a local development environment is for; 8.0, 8.4 and 9.7
# because they are what upstream ships. Adding a line is a roadmap entry rather than an input, which
# is why this is a constant and not a `--version` anybody can widen.
LINES = ("5.6", "5.7", "8.0", "8.4", "9.7")

# The two whose Unix cells are compiled. **Upstream withdrew macOS from both lines while they were
# alive** — 5.7.31 offers `macos10.14-x86_64`, 5.7.44 offers no macOS asset at all and lists no macOS
# entry in its own operating-system menu — and it has never published an ARM build of either, on any
# system. So the newest patch of a dead line is *less* portable than one from the middle of it.
BUILT_LINES = ("5.6", "5.7")

# (os, arch) -> (the archive's operating-system id, what the asset for that cell is called).
#
# Anchored patterns rather than `in`, because the same listing carries `mysql-test-<version>-…`,
# `-debug-test`, `-minimal` and an uncompressed `.tar` beside every `.tar.xz`, and a substring match
# would pack the test suite.
CELLS = {
    ("macos", "aarch64"): ("33", re.compile(r"^mysql-[\d.]+-macos\d+-arm64\.tar\.gz$")),
    ("macos", "x86_64"): ("33", re.compile(r"^mysql-[\d.]+-macos\d+-x86_64\.tar\.gz$")),
    # glibc2.28 on both Linux cells, deliberately. 8.0 additionally publishes a whole second set at
    # `glibc2.17` and **that set has no aarch64**: taking it for x86_64 would buy a lower floor and
    # cost the thing the floor is for, which is that both Linux artifacts of one version are one
    # build. See one-version-means-one-thing.md.
    ("linux", "x86_64"): ("2", re.compile(r"^mysql-[\d.]+-linux-glibc2\.28-x86_64\.tar\.xz$")),
    ("linux", "aarch64"): ("2", re.compile(r"^mysql-[\d.]+-linux-glibc2\.28-aarch64\.tar\.xz$")),
    ("windows", "x86_64"): ("3", re.compile(r"^mysql-[\d.]+-winx64\.zip$")),
}

# Which runner packs which cell. Here rather than in the workflow because :func:`legs` decides the
# matrix, and a runner named in two places is a runner that will disagree with itself.
#
# Linux x86_64 on 22.04 for the borrowed cells, for the reason build-php.yml states at length: the
# runner choice *is* the glibc floor. The compiled Linux cells override it — they run inside a
# manylinux_2_28 container the workflow names, so their floor is the image's 2.28 and not the host's.
#
# There is no ("windows", "aarch64") row, and that is the empty cell of the table rather than an
# omission: Oracle has never published an ARM64 Windows build at any version, 5.6 targets Visual
# Studio 2013 and 5.7 Visual Studio 2015, and for 8.0 and newer it is a build nobody here has
# attempted.
RUNNERS = {
    ("macos", "aarch64"): "macos-14",
    ("macos", "x86_64"): "macos-15-intel",
    ("linux", "x86_64"): "ubuntu-22.04",
    ("linux", "aarch64"): "ubuntu-22.04-arm",
    ("windows", "x86_64"): "windows-2022",
}

# The compiled Linux cells do not use the row above. They run inside AlmaLinux 8 — the same
# `manylinux_2_28` image build-php.yml compiles PHP 7 in — so the floor is the image's glibc 2.28
# and the host is only what starts the container. A current host is the better one for that job,
# and the image is named here beside its runner so that "which glibc does a 5.6 artifact need" has
# one answer in one file.
BUILD_LINUX = {
    "x86_64": ("ubuntu-24.04", "quay.io/pypa/manylinux_2_28_x86_64"),
    "aarch64": ("ubuntu-24.04-arm", "quay.io/pypa/manylinux_2_28_aarch64"),
}

# The operating-system id the archive files a source release under.
SOURCE_OS = "src"

KEYS = "https://repo.mysql.com"

# Three keys, because five lines were signed by three of them — and **all three are expired**: the
# 2013 key on 2022-02-16, the 2022 key on 2023-12-14, the 2023 key on 2025-10-22. That is not a
# reason to refuse a signature. A signature made while a key was valid stays valid, and gpg says so
# by emitting `EXPKEYSIG` beside `VALIDSIG`. What it is a reason for is pinning fingerprints here
# rather than trusting a keyring's own notion of validity, which would reject every line MySQL has.
FINGERPRINTS = {
    "RPM-GPG-KEY-mysql": ("A4A9406876FCBD3C456770C88C718D3B5072E1F5",),
    "RPM-GPG-KEY-mysql-2022": ("859BE8D7C586F538430B19C2467B942D3A79BD29",),
    "RPM-GPG-KEY-mysql-2023": ("BCA43417C3B485DD128EC6D4B7B3B788A8D3785C",),
}

# Every fingerprint above, as the one set :func:`signed` will accept a signature from.
PINNED = frozenset(fingerprint for group in FINGERPRINTS.values() for fingerprint in group)

# Where gpg is, in the order a machine is likely to have one. The same list nginx.py keeps, for the
# same reason: tried rather than required by a single spelling.
GPG = (
    "gpg", "gpg2",
    "/usr/bin/gpg", "/opt/homebrew/bin/gpg", "/usr/local/bin/gpg",
    r"C:\Program Files\Git\usr\bin\gpg.exe",
    r"C:\Program Files (x86)\GnuPG\bin\gpg.exe",
)

# What no MixEngine artifact ships. The second half of "repack, do not rearrange" throws out headers,
# import libraries, manual pages and test suites; the rest is MySQL's own furniture — a bintar
# carries its packaging scripts, its systemd units and a benchmark suite nobody installs.
PRUNE = (
    "mysql-test", "sql-bench", "share/man", "man", "docs", "include", "lib/pkgconfig",
    "share/aclocal", "support-files", "share/doc", "share/mysql-test", "share/sql-bench",
)

# Deleted by pattern rather than by path, because where each of these lands moved between 5.6 and
# 9.7 and a list of paths would silently stop matching.
NOT_SHIPPED = (
    "*.a", "mysql_config*", "mysqltest*", "mysql_client_test*", "mysqlxtest*",
    "*_test_plugin*", "auth_test*", "test_*", "*.pdb", "*.lib", "*.pl", "*.def",
)


def page(url: str, timeout: int = 60) -> str:
    """Read an archive page, and refuse the error page it serves with a 200.

    The status is not the answer here. `downloads.mysql.com` answers `200 text/html` with a body
    reading "Technical Difficulties" when it is unwell, and a client that branched on the status
    would parse that page for a version list and conclude MySQL has none.
    """
    body = borrow.fetch(url, timeout=timeout).decode("utf-8", "replace")
    if "Technical Difficulties" in body:
        raise SystemExit(
            f"{url} answered 200 with Oracle's 'Technical Difficulties' page rather than content. "
            f"That is the archive being unwell, not a version that does not exist."
        )
    return body


@lru_cache(maxsize=None)
def versions() -> tuple[str, ...]:
    """Every version the archive lists, newest first, read off its own version menu."""
    body = page(COMMUNITY)
    menu = re.search(r'<select[^>]+id="version".*?</select>', body, re.S)
    if not menu:
        raise SystemExit(
            f"{COMMUNITY} has no version menu. The archive page changed shape, and guessing at the "
            f"catalogue would pack whatever the guess found."
        )
    found = re.findall(r'<option[^>]+value="([\d.]+)"', menu.group(0))
    if not found:
        raise SystemExit(f"{COMMUNITY}'s version menu lists nothing")
    return tuple(found)


def in_line(line: str) -> list[str]:
    """The versions of one line, newest first."""
    prefix = borrow.parts(line)
    return sorted(
        (version for version in versions() if borrow.parts(version)[:len(prefix)] == prefix),
        key=borrow.parts, reverse=True,
    )


@lru_cache(maxsize=None)
def assets(version: str, os_id: str) -> tuple[str, ...]:
    """What upstream published for one version on one operating system.

    Cached, because `resolve` asks about five cells of a version and may ask about several versions
    before one of them has a complete signed set — and each question is a rendered page.
    """
    body = page(f"{COMMUNITY}?tpl=files&os={os_id}&version={version}&osva=")
    return tuple(sorted(set(re.findall(r"/archives/get/p/23/file/([^\"'>\s]+)", body))))


def asset_for(version: str, target: tuple[str, str]) -> str | None:
    """The one asset for this cell at this version, or ``None`` if upstream published none."""
    os_id, pattern = CELLS[target]
    matching = [name for name in assets(version, os_id) if pattern.match(name)]
    if len(matching) > 1:
        raise SystemExit(
            f"{version} offers {len(matching)} assets for {target[0]}/{target[1]}: "
            f"{', '.join(matching)}. One of them is not what this pattern was written for."
        )
    return matching[0] if matching else None


def source_asset(version: str) -> str | None:
    """The source tarball :mod:`mysql_build` compiles, or ``None`` if there is not one."""
    name = f"mysql-{version}.tar.gz"
    return name if name in assets(version, SOURCE_OS) else None


def signature(name: str, version: str) -> bytes | None:
    """The detached signature for one asset, or ``None`` when upstream published none.

    Two routes, because neither covers everything and **the shape of the failure differs between
    them**: the CDN answers 404 for an asset it has no signature for, and the archive's own gpg
    endpoint answers `200` with a one-byte body. A client that only read the status would write an
    empty file and hand it to gpg.

    There is a third route everybody tries first — ``<asset>.asc`` beside the asset under
    ``/archives/get/`` — and it 404s for every asset at every version. It is named here so that the
    next person does not spend an afternoon finding that out again.
    """
    line = ".".join(version.split(".")[:2])
    for url in (f"{CDN}/mysql-{line}/{name}.asc", f"{ARCHIVE}/gpg/?file={name}&p=23"):
        try:
            body = borrow.fetch(url, timeout=120)
        except urllib.error.HTTPError:
            continue
        if body.startswith(b"-----BEGIN PGP SIGNATURE-----"):
            return body
    return None


def gpg() -> str:
    """The first gpg on this machine, or a refusal naming everywhere that was looked.

    **Never a shrug.** The archive page publishes an MD5 and nothing else, which is not something
    this repository writes into ``upstream.verified_against``, so a run without gpg is a run that
    would publish an archive nothing checked — and an unverified artifact that looks exactly like a
    verified one is worse than a failed job.
    """
    for candidate in GPG:
        found = shutil.which(candidate) or (candidate if Path(candidate).exists() else None)
        if found:
            return found
    raise SystemExit(
        "no gpg on this machine, and the only thing MySQL publishes beside an archive is a detached "
        f"PGP signature — its page's MD5 is not verification. Looked for: {', '.join(GPG)}."
    )


def _gpg(program: str, home: Path, *args: str, stdin: bytes | None = None) -> str:
    """gpg against a keyring of our own, in the one spelling both gpg builds understand.

    ``--homedir .`` with the working directory set, rather than ``--homedir <absolute path>``,
    because the gpg that exists on a Windows runner is Git's — an MSYS program, which reads
    ``C:\\Users\\...`` as a *relative* path and prepends its own cwd to it.
    """
    result = subprocess.run(
        [program, "--batch", "--no-tty", "--homedir", ".", *args],
        cwd=str(home), input=stdin, capture_output=True, timeout=300,
    )
    return result.stdout.decode("utf-8", "replace")


def primaries(colons: str) -> list[str]:
    """The fingerprints of the *primary* keys in gpg's colon output, in order.

    Subkeys have ``fpr`` rows too, so reading every one of them would count an encryption subkey as
    a key this repository pinned. Only the first ``fpr`` after each ``pub`` is a primary.
    """
    found, expecting = [], False
    for row in colons.splitlines():
        fields = row.split(":")
        if fields[0] == "pub":
            expecting = True
        elif fields[0] == "fpr" and expecting:
            found.append(fields[9])
            expecting = False
    return found


def keyring(work: Path) -> tuple[str, Path]:
    """Fetch MySQL's signing keys, refuse any file that is not exactly what is pinned, and import.

    The order is the point. A file is read and checked *before* it is imported, so a substituted one
    never reaches the keyring at all and a later ``--verify`` cannot be satisfied by it. The
    comparison is an equality rather than a membership test, because the interesting failure is a
    key **added** to a file, not one missing from it.
    """
    program = gpg()
    home = work / "keys"
    home.mkdir(parents=True, exist_ok=True)
    os.chmod(home, 0o700)

    for name, pinned in sorted(FINGERPRINTS.items()):
        material = borrow.fetch(f"{KEYS}/{name}")
        offered = primaries(
            _gpg(program, home, "--show-keys", "--with-colons", "--with-fingerprint",
                 stdin=material)
        )
        if set(offered) != set(pinned):
            raise SystemExit(
                f"{KEYS}/{name} is not the key file this recipe pins.\n"
                f"  it carries: {', '.join(offered) or 'nothing gpg could read'}\n"
                f"  pinned:     {', '.join(pinned)}\n"
                f"Either Oracle rotated a signing key or this is not Oracle's key file, and telling "
                f"those apart is a person's job rather than this one's."
            )
        _gpg(program, home, "--import", stdin=material)

    imported = primaries(_gpg(program, home, "--list-keys", "--with-colons"))
    if set(imported) != PINNED:
        raise SystemExit(
            f"the keyring holds {len(imported)} keys and {len(PINNED)} were pinned: "
            f"{', '.join(sorted(set(imported) ^ PINNED))} is the difference"
        )
    print(f"gpg {program}: {len(imported)} pinned MySQL keys imported")
    return program, home


def signed(program: str, home: Path, archive: Path, detached: Path) -> str:
    """Verify *archive* against *detached*, and answer with the fingerprint that signed it.

    Read from ``--status-fd`` rather than from the exit code alone. gpg exits non-zero for a
    signature from an **expired** key, which is every MySQL key there has ever been, and the
    machine-readable ``VALIDSIG`` line is the only place the fingerprint that actually signed the
    bytes is stated. Requiring it to be one that was pinned closes the gap between "gpg was happy"
    and "the people who sign MySQL signed this" — and it is what lets an expired key be accepted
    without accepting anything else: the trust here comes from :data:`FINGERPRINTS`, never from a
    keyring's own opinion.

    ``--allow-weak-digest-algos`` because **MySQL 5.6 is signed with DSA over SHA-1** — the key
    dates from 2003 and the signature from 2021 — and a gpg that rejects it would fail one line of
    five for a property of the key rather than of the artifact. gpg 2.4.9 accepted it without the
    flag and an older one on an older runner may not, which is exactly why it is passed explicitly:
    what a recipe here does must not depend on which gpg a runner happens to carry.
    """
    status = _gpg(program, home, "--allow-weak-digest-algos", "--status-fd", "1",
                  "--verify", str(detached), str(archive))
    valid = [line.split()[2] for line in status.splitlines()
             if line.startswith("[GNUPG:] VALIDSIG ")]
    if len(valid) != 1 or valid[0] not in PINNED:
        raise SystemExit(
            f"{archive.name} is not signed by a key this recipe pins. gpg said:\n"
            + "\n".join(line for line in status.splitlines() if line.startswith("[GNUPG:]"))
        )
    return valid[0]


def download(program: str, home: Path, name: str, version: str,
             work: Path) -> tuple[Path, str, str]:
    """Fetch one asset and its signature, and refuse to return an unverified one."""
    url = f"{GET}{name}"
    archive, detached = work / name, work / f"{name}.asc"
    material = signature(name, version)
    if material is None:
        raise SystemExit(
            f"upstream published no detached signature for {name}. Its page states an MD5, which is "
            f"not something this repository writes into upstream.verified_against."
        )
    detached.write_bytes(material)
    print(f"fetching {url}")
    try:
        archive.write_bytes(borrow.fetch(url, timeout=1800))
    except urllib.error.HTTPError as error:
        raise SystemExit(f"{url} answered {error.code}") from error

    fingerprint = signed(program, home, archive, detached)
    who = next(file for file, group in FINGERPRINTS.items() if fingerprint in group)
    digest = borrow.sha256(archive)
    print(f"{name}: good signature from {who} ({fingerprint})")
    print(f"sha256 {digest} (computed here; MySQL's page publishes only an MD5)")
    return archive, digest, url


def verified_against(fingerprint: str) -> str:
    """What the manifest says about how the download was checked."""
    who = next(file for file, group in FINGERPRINTS.items() if fingerprint in group)
    return (
        f"a detached PGP signature from {who} ({fingerprint}), fetched separately from the archive "
        f"and checked against a fingerprint pinned in tools/mysql.py"
    )


def needs(version: str) -> list[tuple[str, str]]:
    """(what has to exist, what it is for) — every asset any leg of this version will ask for."""
    line = ".".join(version.split(".")[:2])
    if line in BUILT_LINES:
        return [
            (source_asset(version) or "", "the source the four Unix cells compile"),
            (asset_for(version, ("windows", "x86_64")) or "", "the Windows x86_64 zip"),
        ]
    return [(asset_for(version, target) or "", f"{target[0]}/{target[1]}") for target in CELLS]


def resolve(line: str) -> str:
    """The newest version of *line* whose every asset exists **and is signed**.

    Not simply the newest, and not resolved per leg either — both of which this repository does
    elsewhere and neither of which is right here. **MySQL 8.0.45 published its Linux tarballs
    without signatures**, while its own macOS and Windows assets have them and 8.0.44's Linux ones
    do. A recipe resolving per leg would pack 8.0.45 on three cells and 8.0.44 on two, and "MySQL
    8.0" would be two databases in two releases, each with a table three-fifths empty. So the whole
    line is resolved once, here, against everything every cell needs, and what was refused is
    printed rather than skipped quietly.

    An unsigned asset is refused rather than packed with a weaker claim, for the reason
    :func:`gpg` gives: the page's MD5 is not verification, and an unverified artifact that looks
    exactly like a verified one is worse than a missing one.
    """
    for version in in_line(line):
        wanted = needs(version)
        missing = [why for name, why in wanted if not name]
        if missing:
            print(f"{version}: upstream published nothing for {', '.join(missing)}")
            continue
        unsigned = [name for name, _ in wanted if signature(name, version) is None]
        if unsigned:
            print(f"{version}: no detached signature for {', '.join(unsigned)}")
            continue
        return version
    raise SystemExit(
        f"no version of MySQL {line} has a complete, signed set of assets. Every candidate and what "
        f"it was missing is printed above."
    )


def legs(spec: str) -> dict[str, list[dict[str, str]]]:
    """What the workflow's three matrices are built from: one exact version per line, and its cells.

    Three lists rather than a version list, because **which recipe a cell uses depends on the
    version and not only on the cell** — 5.6 compiles on macOS where 9.7 borrows — and an ``if:`` in
    the workflow deciding that would be the same fact written in two places. Splitting `linux` from
    `macos` is the third: the compiled Linux legs run inside a container and the macOS ones cannot.
    """
    wanted = LINES if spec.strip() == "all" else tuple(
        piece.strip() for piece in spec.split(",") if piece.strip()
    )
    unknown = [line for line in wanted if line not in LINES]
    if unknown:
        raise SystemExit(
            f"this repository packs MySQL {', '.join(LINES)}; it was asked for "
            f"{', '.join(unknown)}. Adding a line is a roadmap entry rather than an input."
        )
    if not wanted:
        raise SystemExit("nothing to build: the version list is empty")

    planned: dict[str, list[dict[str, str]]] = {"borrow": [], "macos": [], "linux": []}
    for line in wanted:
        version = resolve(line)
        print(f"{line} resolves to {version}")
        built = line in BUILT_LINES
        for (system, arch), runner in RUNNERS.items():
            leg = {"version": version, "os": system, "arch": arch, "runner": runner}
            if built and system == "linux":
                host, image = BUILD_LINUX[arch]
                planned["linux"].append({**leg, "runner": host, "image": image})
            elif built and system == "macos":
                planned["macos"].append(leg)
            else:
                planned["borrow"].append(leg)
    return planned


def prune(tree: Path) -> list[str]:
    """Take out what no artifact here ships, and answer with what was taken.

    Every path returned goes into ``upstream.removed``, where :func:`borrow.declare` checks it is
    genuinely gone — a declaration nothing verifies decays into a comment.
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
    for pattern in NOT_SHIPPED:
        for path in sorted(tree.rglob(pattern)):
            if not path.is_symlink() and not path.exists():
                continue
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink()
            removed.append(str(path.relative_to(tree)).replace("\\", "/"))
    # A symlink whose target was just deleted is still a file in the archive, and `exists` follows
    # the link and answers no — which is how MariaDB shipped `mysql_ldb` for four rounds while
    # declaring it removed.
    for path in sorted(tree.rglob("*")):
        if path.is_symlink() and not path.exists():
            path.unlink()
            removed.append(str(path.relative_to(tree)).replace("\\", "/"))
    return sorted(dict.fromkeys(removed))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default="all",
                        help="'all', or a comma-separated list of lines (5.6,8.4)")
    parser.add_argument("--plan", action="store_true",
                        help="print the legs --version expands to, as JSON, and pack nothing. Used "
                             "by the workflow to fan one run out over five lines and three recipes.")
    arguments = parser.parse_args()
    if not arguments.plan:
        raise SystemExit(
            "mysql.py is the catalogue, not a recipe: it packs nothing. Use mysql_borrow.py for a "
            "cell upstream publishes a binary for, mysql_build.py for one it does not, or --plan."
        )
    # Everything explanatory has already gone to stdout through `resolve`, so the workflow reads the
    # last line rather than the whole of it.
    print(json.dumps(legs(arguments.version)))


if __name__ == "__main__":
    main()
