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
import relocate
import strip

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
# omission: Oracle has never published an ARM64 Windows build at any version, the 5.x trees are of
# an era whose published binaries still import the Visual Studio 2010 runtime and neither has been
# demonstrated building with MSVC on ARM64, and for 8.0 and newer it is a build nobody here has
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
    # A whole second server, and every plugin built against it. Upstream ships `mysqld-debug` and
    # `lib/plugin/debug/` in the same archive as the real one — a quarter of the download — and
    # `relocate.verify` is what noticed: those plugins import `mysqld-debug.exe` by name, so a tree
    # keeping them fails its own relocation check for a program MixEngine would never start.
    "lib/plugin/debug",
)

# Kept out of a directory `PRUNE` otherwise takes whole, because something the artifact has to be
# able to do needs it.
#
# **`support-files/my-default.cnf` is one file and the 5.6 cells do not bootstrap without it.**
# `scripts/mysql_install_db` — which is how a 5.6 data directory is made, there being no
# `mysqld --initialize` until 5.7 — looks for that template in `.`, `share`, `share/mysql` and
# `support-files`, and stops with `FATAL ERROR: Could not find my-default.cnf` when it is in none of
# them. It checks *before* it looks at `--keep-my-cnf`, so asking it not to write a `my.cnf` does
# not excuse the file. The rest of `support-files` is init scripts and systemd units for a system
# install nobody here performs, and it still goes.
KEPT = (
    "support-files/my-default.cnf",
)

# Deleted by pattern rather than by path, because where each of these lands moved between 5.6 and
# 9.7 and a list of paths would silently stop matching.
NOT_SHIPPED = (
    "*.a", "mysql_config*", "mysqltest*", "mysql_client_test*", "mysqlxtest*",
    "*_test_plugin*", "auth_test*", "test_*", "*.pdb", "*.lib", "*.pl",
    # The debug server itself, wherever it landed. Not `*debug*`: `lib/mecab/dic/` holds dictionary
    # files whose names a broad pattern would take, and a full-text plugin that cannot find its
    # dictionary fails at the first CJK query rather than at the pack.
    "mysqld-debug*",
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
             work: Path) -> tuple[Path, str, str, str]:
    """Fetch one asset and its signature, and refuse to return an unverified one.

    Answers with the fingerprint that signed it as well as the file, because that is what the
    manifest has to say — and verifying once and reporting what verified is a different thing from
    verifying twice and hoping both agree.
    """
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
    return archive, digest, url, fingerprint


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


def prune_around(tree: Path, directory: Path, kept: set[str]) -> list[str]:
    """Empty *directory* of everything except *kept*, and answer with what went, path by path.

    Path by path because that is what `borrow.declare` checks: ``upstream.removed`` naming a
    directory that is still there — because one file in it survived — is a declaration that fails
    the pack, and rightly.
    """
    removed = []
    for child in sorted(directory.iterdir()):
        relative = child.relative_to(tree).as_posix()
        if relative in kept:
            continue
        if any(keep.startswith(f"{relative}/") for keep in kept):
            removed += prune_around(tree, child, kept)
            continue
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child, ignore_errors=True)
        else:
            child.unlink()
        removed.append(relative)
    return removed


def prune(tree: Path) -> list[str]:
    """Take out what no artifact here ships, and answer with what was taken.

    Every path returned goes into ``upstream.removed``, where :func:`borrow.declare` checks it is
    genuinely gone — a declaration nothing verifies decays into a comment.
    """
    removed = []
    for relative in PRUNE:
        path = tree / relative
        kept = {
            keep for keep in KEPT
            if keep == relative or keep.startswith(f"{relative}/")
        }
        if path.is_dir() and kept:
            removed += prune_around(tree, path, kept)
            # 8.0 and newer ship no `my-default.cnf`, so the directory can come out of this empty,
            # and an empty directory nobody asked for is still something the artifact carries.
            if not any(path.iterdir()):
                path.rmdir()
                removed.append(relative)
        elif path.is_dir():
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


def strip_debug(tree: Path) -> dict[str, str]:
    """Take the debug symbols out of a Linux tree, and answer with which files that changed.

    **Measured, because the five cells of one version did not agree with each other.** MySQL 9.7.1
    packs to 109 MB on macOS and 118 MB on Windows, and to **609 MB on Linux** — the same server,
    the same pruning, the same compression. The difference is not what is in the archive: the log of
    that pack names the same fifteen paths removed on Linux as on macOS. It is what is inside the
    files, and both halves of this row have the same answer for a different reason.

    A *borrowed* Linux bintar carries `.debug_*` in `bin/mysqld` and in every plugin, where Oracle
    ships macOS stripped and files Windows' symbols in separate `.pdb` that `NOT_SHIPPED` already
    drops. A *built* cell reaches the same place without anyone's help: DWARF is linked into an ELF
    executable and is not linked into a Mach-O one — it stays behind in the object files — so the
    5.6 cells this repository compiles itself come out at 131 MB on Linux against 76 MB on macOS
    from one set of flags. Nothing in MixEngine reads either, and a user downloads them once per
    version per machine.

    ``--strip-debug`` rather than ``--strip-all``, which is `strip.IMAGES`' Linux row and the reason
    this asks for something else: `lib/plugin/*.so` is opened by `dlopen` and
    `lib/libmysqlclient.so` is what a client extension links against, so the dynamic symbol table
    has to stay — and removing it would save almost nothing, because it is not what makes these
    files large.

    Linux only. Not portability caution: on macOS there is nothing here to take, which the sizes
    above are the measurement of, and `strip -x` on a signed arm64 binary costs a re-signature
    (`strip.countersigned`) to buy back a rounding error.

    What comes back is ``upstream.changed`` — see :func:`strip.symbols`, which refuses to return
    unless the loader's and the linker's whole view of every file survived the operation. A built
    cell has no upstream binary to differ from and records it in ``recipe`` instead.
    """
    if sys.platform != "linux" or not shutil.which("strip"):
        return {}

    files = relocate.machine_files(tree)
    before = sum(path.stat().st_size for path in files)
    changed = strip.symbols(tree, files, ["--strip-debug"], "linux")
    after = sum(path.stat().st_size for path in files)
    if changed:
        print(f"stripped debug symbols from {len(changed)} of {len(files)} files: "
              f"{before / 1e6:,.0f} MB of machine code became {after / 1e6:,.0f} MB")
    return changed


def unloadable_libraries(tree: Path) -> list[str]:
    """Delete the libraries upstream shipped against other libraries it did not ship, naming each.

    Not a hypothetical tidy-up. **MySQL 5.7.44's Windows zip carries `bin/saslSCRAM.dll`, which
    imports `libcrypto-3-x64.dll`, and that zip contains no OpenSSL DLL at all** — so the file
    cannot load on any machine, upstream's own included, and `relocate.verify` refuses the whole
    tree over it. The choice is to ship a tree that fails its own relocation check, to weaken the
    check, or to take the file out and say which. MariaDB reached the same fork on Linux, over
    plugins rather than a helper in `bin/`, and this is the same answer.

    **Libraries only, never a program.** An executable whose dependency does not resolve is a
    broken build and has to fail the pack — that is the check doing its job. A plugin nobody here
    loads is upstream shipping something it did not finish, and deleting it is the smallest honest
    repair. The pass repeats while anything is still moving, because one deletion can orphan the
    next, and it stops rather than looping if it ever stops converging.

    Every path returned goes into ``upstream.removed``, where :func:`borrow.declare` checks it — a
    file declared gone that is still in the tree fails the pack.
    """
    dropped: list[str] = []
    for _ in range(4):
        search = relocate.loader_search(tree)
        executable_dir = tree / "bin" if (tree / "bin").is_dir() else tree
        rejected = []
        for path in relocate.machine_files(tree):
            if not any(suffix in (".dll", ".so", ".dylib") for suffix in path.suffixes):
                continue
            missing = [
                spelling
                for spelling, resolved in relocate.dependencies(path, executable_dir, search)
                if resolved is None and not relocate.is_system(spelling, resolved)
            ]
            if missing:
                print(f"not shipping {path.relative_to(tree)}: it needs {', '.join(missing)}, "
                      f"which upstream did not ship beside it and a user's machine would not have")
                path.unlink()
                rejected.append(str(path.relative_to(tree)).replace("\\", "/"))
        dropped += rejected
        if not rejected:
            return sorted(dict.fromkeys(dropped))
    raise SystemExit(
        "four passes of deleting unloadable libraries have not settled, which means each one is "
        "orphaning the next. That is a tree missing something central rather than a few plugins "
        f"upstream did not finish: {', '.join(dropped[-10:])}"
    )


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
