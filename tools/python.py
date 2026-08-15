#!/usr/bin/env python3
"""Borrow a CPython from python-build-standalone and repack it as a MixEngine artifact.

This is the row the whole "borrow before you build" rule was named after: MixEngine's runtime table
has written *"python-build-standalone (relocatable, all platforms)"* since before there was a
pipeline, on the strength of what the project says about itself. This is the task that checks it
rather than repeating it, and the answer is yes — with two things the table did not say.

*The Unix entry points are already relocatable, and by a trick worth knowing.* ``bin/pip3`` is not a
Python script with a ``#!`` pointing at an interpreter that will not be there; it is a ``/bin/sh``
script whose second line re-executes ``$(dirname "$(realpath "$0")")/python3.12`` on itself. So the
whole tree moves and every console script keeps working, with nothing for this recipe to fix.

*On Windows there is no pip to run at all.* ``Scripts/`` is empty — the pip *package* is installed in
``Lib/site-packages`` and only ``python -m pip`` reaches it. See :func:`ensure_pip` for why the fix
is a two-line batch file written here rather than the post-install hook
``.claude/features/runtime-versions.md`` reserved for it.

Everything mechanical is in :mod:`borrow`, shared with the Node.js and Ruby recipes. What stays here
is the resolution against upstream's release and the smoke test, which is a claim about Python and
not about downloads.

Python 3 stdlib only, by policy: this runs on a GitHub runner with nothing installed.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import borrow  # noqa: E402  — siblings, and this directory is not importable as a package
import relocate  # noqa: E402

REPOSITORY = "https://github.com/astral-sh/python-build-standalone"

# (os, arch) -> the Rust target triple upstream names its builds after. There is no second spelling
# to keep in step here, unlike Node: the triple is the whole per-target difference.
TARGETS = {
    ("windows", "x86_64"): "x86_64-pc-windows-msvc",
    ("windows", "aarch64"): "aarch64-pc-windows-msvc",
    ("macos", "aarch64"): "aarch64-apple-darwin",
    ("macos", "x86_64"): "x86_64-apple-darwin",
    ("linux", "x86_64"): "x86_64-unknown-linux-gnu",
    ("linux", "aarch64"): "aarch64-unknown-linux-gnu",
}

# **`gnu` and never `musl`, for the reason PHP's row already records**: a musl build of anything in
# this table is a build that cannot `dlopen`, and a Python that cannot load a compiled wheel is not
# a Python anyone can develop against. And plain `x86_64` rather than the `x86_64_v2`/`v3`/`v4`
# variants upstream also publishes, because those raise the CPU floor to buy a benchmark nobody
# running a local development environment is measuring.
#
# `install_only_stripped` rather than `install_only`: the same tree without the debug symbols, and
# the saving is not marginal — 46.1 MB against 22.0 MB on Windows and 109.2 MB against 34.1 MB on
# Linux, measured on 3.12.14. macOS is identical either way because those builds carry no separate
# symbols to strip. Every `install_only` asset in the release has a stripped counterpart, so this is
# not a variant that exists for some targets and not others.
VARIANT = "install_only_stripped"

# Where each command lives inside the tree, per OS. `python3` and `pip3` are absent from both halves
# on purpose: they are *command* names, which `core::shims::COMMANDS` maps onto these *executable*
# names, and an artifact that published both spellings would be inviting the two to disagree.
LAYOUT = {
    "windows": {"python": "python.exe", "pip": "Scripts/pip.cmd"},
    "unix": {"python": "bin/python3", "pip": "bin/pip3"},
}

REQUIRED = ("python", "pip")

# Imported from the relocated tree, and every one of them is a compiled extension module or the thing
# that proves one works. A CPython missing any of these starts, answers `--version` correctly and
# then fails on the first real project: no `ssl` is no `pip install`, no `sqlite3` is no Django
# development database, no `ctypes` is no `cffi`, and `lzma`/`bz2`/`zlib` are how wheels and
# tarballs are opened. `tkinter` is deliberately not here — upstream ships it, it needs a display
# library on Linux, and nothing a local web development environment does touches it.
MODULES = (
    "ssl", "hashlib", "sqlite3", "zlib", "bz2", "lzma", "ctypes", "decimal", "socket",
    "venv", "ensurepip", "json", "uuid",
)

# The batch file the Windows artifacts get in place of the `pip.exe` upstream does not ship. Two
# lines of it are a comment, because a file this repository wrote into a borrowed archive should say
# so to whoever finds it.
PIP_CMD = """@echo off
rem Written by MixEngine's tools/python.py, because python-build-standalone ships Scripts/ empty.
rem `pip.exe` would be a launcher with this machine's interpreter path baked into it, and this tree
rem is going to move. Computing the interpreter from the batch file's own location cannot go stale.
"%~dp0..\\python.exe" -m pip %*
"""


def release_tag() -> str:
    """The newest python-build-standalone release, without asking the GitHub API for it.

    ``/releases/latest`` is a redirect to ``/releases/tag/<date>``, so following it costs one request
    and no token. The API would answer the same question and is rate limited per IP — which on a
    six-legged matrix of shared GitHub runners is a limit six builds at once can genuinely reach,
    and it would fail as "resolution broke" rather than as "too many builds".
    """
    with urllib.request.urlopen(f"{REPOSITORY}/releases/latest", timeout=120) as response:
        location = response.url
    tag = location.rstrip("/").rsplit("/", 1)[-1]
    if not re.fullmatch(r"\d{8}", tag):
        raise SystemExit(f"{location} does not look like a dated release; got tag {tag!r}")
    return tag


def catalogue(tag: str) -> dict[str, str]:
    """Every asset in the release and its published SHA-256, from the release's own ``SHA256SUMS``.

    One file answers both questions this recipe has — *what exists* and *what it should hash to* —
    which is why nothing here enumerates assets any other way. A name absent from it is a name
    upstream does not describe, and downloading it anyway would be downloading something nobody
    stated.
    """
    try:
        sums = borrow.fetch(f"{REPOSITORY}/releases/download/{tag}/SHA256SUMS").decode(
            "utf-8", "replace"
        )
    except urllib.error.HTTPError as error:
        raise SystemExit(f"release {tag} has no SHA256SUMS ({error.code})") from error

    published = {}
    for line in sums.splitlines():
        digest, _, name = line.partition("  ")
        if name.strip():
            published[name.strip()] = digest.strip()
    if not published:
        raise SystemExit(f"SHA256SUMS for {tag} is empty")
    return published


def resolve(spec: str, triple: str, published: dict[str, str], tag: str) -> tuple[str, str]:
    """Turn ``3.12``, ``3.12.14`` or ``latest`` into ``(version, asset name)``.

    Read off the release's own file list, so "does this target have a build" is answered before the
    download rather than by a 404 half of one later — on the one leg in the matrix whose
    architecture is the reason. Python 3.10 has no ``aarch64-pc-windows-msvc`` build, which is an
    empty cell of the table and not a failure.

    Release candidates are refused rather than ranked: upstream publishes ``3.15.0rc1`` beside the
    stable lines, and a version whose channel nobody asked for should not be what ``latest`` means.
    """
    offered: dict[str, str] = {}
    for name in published:
        match = re.fullmatch(
            rf"cpython-(\d+\.\d+\.\d+)\+{tag}-{re.escape(triple)}-{VARIANT}\.tar\.gz", name
        )
        if match:
            offered[match.group(1)] = name

    if not offered:
        borrow.unavailable(
            f"python-build-standalone {tag} publishes no {triple} build at all; "
            f"{spec} is not available for this target"
        )

    if spec == "latest":
        candidates = sorted(offered, key=borrow.parts)
    else:
        prefix = borrow.parts(spec)
        candidates = sorted(
            (version for version in offered if borrow.parts(version)[: len(prefix)] == prefix),
            key=borrow.parts,
        )

    if not candidates:
        lines = sorted({".".join(version.split(".")[:2]) for version in offered}, key=borrow.parts)
        borrow.unavailable(
            f"python-build-standalone {tag} has no {spec} for {triple}. It offers "
            f"{', '.join(lines)}."
        )

    version = candidates[-1]
    return version, offered[version]


def site_packages(tree: Path) -> Path | None:
    """Where this build put third-party packages, without asking it.

    Read off the tree rather than out of ``sysconfig`` because it is used to check what ``pip``
    reports *against* the archive, and asking the interpreter both questions would let one wrong
    answer confirm the other.
    """
    windows = tree / "Lib" / "site-packages"
    if windows.is_dir():
        return windows
    found = sorted((tree / "lib").glob("python3.*/site-packages"))
    return found[0] if found else None


def ensure_pip(tree: Path, operating_system: str) -> list[str]:
    """Give Windows a ``pip`` that can be *run*, and answer T27's open question while doing it.

    ``.claude/features/runtime-versions.md`` reserves a per-runtime post-install hook and names
    *ensure pip* as Python's. Measured, the hook is not needed, and the reason is worth writing down
    because it is the same reason on any runtime whose archive moves.

    What upstream ships on Windows is the pip *package*, in ``Lib/site-packages``, with ``Scripts/``
    holding a single ``.empty``. So ``python -m pip`` works and there is nothing named ``pip`` to put
    in a ``provides`` map. The obvious repair — run ``ensurepip`` and let it generate ``pip.exe`` —
    produces a launcher with the *absolute path of the interpreter that generated it* written inside,
    which is precisely the thing every artifact here is built to survive. Generated at build time it
    names the runner; generated by a post-install hook it names the install directory, and breaks the
    first time the user moves ``~/.mixengine`` or restores it onto another machine.

    A batch file that computes the interpreter from its own location has neither problem, cannot go
    stale, and needs nothing to run it: ``std::process::Command`` starts a ``.cmd`` through
    ``cmd.exe``, returns its exit code and escapes its arguments — measured for ``npm.cmd`` in T27's
    Node half and pinned by a test there, so this recipe is leaning on a proven mechanism rather than
    on a hope.

    If upstream ever does ship ``Scripts/pip.exe``, that is used instead and nothing is written:
    a publisher's own entry point beats one of ours, and this stops being needed without anyone
    noticing.
    """
    if operating_system != "windows":
        return []

    scripts = tree / "Scripts"
    if (scripts / "pip.exe").exists():
        print("upstream now ships Scripts/pip.exe; nothing generated")
        return []

    packages = site_packages(tree)
    if packages is None or not sorted(packages.glob("pip-*.dist-info")):
        raise SystemExit(
            "this archive has neither Scripts/pip.exe nor a pip package in site-packages, "
            "so there is nothing for a generated pip.cmd to call"
        )

    scripts.mkdir(exist_ok=True)
    # `newline=""` so the CRLF written here is the CRLF that lands: a batch file with bare LF line
    # endings is read by some cmd.exe versions as one long line.
    with (scripts / "pip.cmd").open("w", encoding="ascii", newline="") as handle:
        handle.write(PIP_CMD.replace("\n", "\r\n"))
    print("wrote Scripts/pip.cmd (upstream ships Scripts/ empty on Windows)")
    return ["Scripts/pip.cmd"]


def describe(
    tree: Path, version: str, target: tuple[str, str], url: str, digest: str, tag: str,
    added: list[str],
) -> dict:
    """What is in the archive, as the daemon will read it."""
    operating_system, arch = target
    layout = LAYOUT["windows" if operating_system == "windows" else "unix"]

    provides = {name: path for name, path in layout.items() if (tree / path).exists()}
    missing = [name for name in REQUIRED if name not in provides]
    if missing:
        raise SystemExit(
            f"the archive provides no {', '.join(missing)} — expected at "
            f"{', '.join(layout[name] for name in missing)}. Contents: "
            f"{sorted(path.name for path in tree.iterdir())[:20]}"
        )

    manifest = {
        "schema": 1,
        "kind": "python",
        "version": version,
        "os": operating_system,
        "arch": arch,
        "source": "borrowed",
        "upstream": {
            "project": "astral-sh/python-build-standalone",
            "release": tag,
            "variant": VARIANT,
            "url": url,
            "sha256": digest,
            "verified_against": "the release's own SHA256SUMS over HTTPS to the publisher",
        },
        "provides": provides,
    }
    if added:
        # Named rather than implied: this archive is not byte-for-byte what upstream published, and
        # somebody comparing hashes a year from now should find the difference written down here
        # instead of deducing it.
        manifest["upstream"]["added"] = added
    return manifest


PROBE = """
import json, sys, sysconfig
import ssl, hashlib, sqlite3

report = {
    "version": ".".join(str(part) for part in sys.version_info[:3]),
    "executable": sys.executable,
    "prefix": sys.prefix,
    "openssl": ssl.OPENSSL_VERSION,
    "sha256": hashlib.sha256(b"mixengine").hexdigest(),
    "sqlite": sqlite3.connect(":memory:").execute("select sqlite_version()").fetchone()[0],
    "trusted": ssl.create_default_context().cert_store_stats()["x509_ca"],
    "site": sysconfig.get_paths()["purelib"],
}
print(json.dumps(report))
"""


def smoke(tree: Path, version: str, manifest: dict) -> dict:
    """Run the artifact from somewhere it has never been, and make it be itself while doing it.

    ``python --version`` alone would pass on an archive that is unusable, three ways over: the
    runner's own Python could be answering, an interpreter that found the *runner's* standard
    library would answer the same, and a build whose compiled modules did not survive being moved
    answers it perfectly right up to the first ``import ssl``. So five things are proven.

    *It is this Python.* ``sys.executable`` has to be inside the relocated tree.

    *It found its own standard library after moving.* ``sys.prefix`` has to be inside it too — this
    is the whole relocation claim for a runtime that computes its ``prefix`` from ``argv[0]`` at
    every start.

    *Its compiled modules load, and two of them are called rather than reported*: OpenSSL hashes a
    string and SQLite answers a query, where reading ``ssl.OPENSSL_VERSION`` alone exercises a
    string constant that a broken build states just as confidently.

    *It can verify a certificate.* A Python whose default context loads no CA at all starts,
    imports ``ssl``, and then fails every ``pip install`` with a handshake error — the failure
    furthest from its cause in this whole table.

    *It is this pip.* The version ``pip`` reports has to match the ``pip-*.dist-info`` in the
    ``site-packages`` that came in the same archive, which is a fact no other Python on the machine
    can produce.
    """
    elsewhere = borrow.moved(tree)

    if sys.platform != "win32":
        problems = relocate.verify(elsewhere)
        for problem in problems:
            print(f"error: {problem}", file=sys.stderr)
        if problems:
            raise SystemExit("the relocated tree reaches outside itself")

    python = elsewhere / manifest["provides"]["python"]
    path = borrow.clean_path(python.parent)
    drop = ("PYTHON",)      # PYTHONHOME and PYTHONPATH both make this test lie if they survive

    banner = borrow.run(python, "--version", path=path, drop=drop)
    if banner.split()[-1] != version:
        raise SystemExit(f"python reports {banner!r}, expected {version}")

    for module in MODULES:
        borrow.run(python, "-c", f"import {module}", path=path, drop=drop)

    report = json.loads(borrow.run(python, "-c", PROBE, path=path, drop=drop))

    if report["version"] != version:
        raise SystemExit(f"sys.version_info says {report['version']}, expected {version}")
    for field in ("executable", "prefix", "site"):
        where = Path(report[field])
        if not where.resolve().is_relative_to(elsewhere.resolve()):
            raise SystemExit(
                f"sys.{field} is {where}, which is not inside the tree this interpreter was "
                f"copied to — it is answering from somewhere else on this machine"
            )
    if len(report["sha256"]) != 64:
        raise SystemExit(f"the bundled OpenSSL answered {report['sha256']!r}")
    if not report["trusted"]:
        raise SystemExit(
            "ssl.create_default_context() loaded no certificate authorities, so every HTTPS "
            "request this Python makes would fail verification and `pip install` with it"
        )

    print(f"python {report['version']}, {report['openssl']}, sqlite {report['sqlite']}, "
          f"{report['trusted']} trusted CAs")
    ran = ["python --version", "import " + ", ".join(MODULES), "python -c (execPath, ssl, sqlite3)"]

    packages = site_packages(elsewhere)
    for name in sorted(set(manifest["provides"]) - {"python"}):
        reported = borrow.run(
            elsewhere / manifest["provides"][name], "--version", path=path, drop=drop
        )
        # `pip 26.2.1 from ...` — the version is the second word, and the rest of the line is the
        # path it is running from, which the check below is about to make its own assertion about.
        installed = sorted(packages.glob(f"{name}-*.dist-info")) if packages else []
        if installed:
            expected = installed[0].name[len(name) + 1: -len(".dist-info")]
            if expected not in reported.split():
                raise SystemExit(
                    f"{name} reports {reported!r}, but the {name} inside this archive is "
                    f"{expected} — something else on this machine answered"
                )
        # `.resolve()` on both sides, not just the tree's: macOS puts a temporary directory under
        # `/var`, which is a symlink to `/private/var`, and `pip` reports whichever spelling it was
        # started through. Comparing one resolved path against one unresolved one fails there and
        # nowhere else.
        running = Path(reported.split(" from ")[-1].split(" (")[0]).resolve()
        if not running.is_relative_to(elsewhere.resolve()):
            raise SystemExit(f"{name} is running from outside the tree: {reported}")
        print(f"{manifest['provides'][name]}: {reported}")
        ran.append(f"{manifest['provides'][name]} --version")

    borrow.discard(elsewhere)
    return {"relocated": True, "ran": ran, "openssl": report["openssl"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--version", required=True,
        help="exact version (3.12.14), a line (3.12) for its newest release, or 'latest'",
    )
    parser.add_argument(
        "--release",
        help="a python-build-standalone release tag (a date, 20260814). Default: the newest. "
             "Pin it to reproduce an older build of the same CPython version.",
    )
    parser.add_argument("--out", default="dist", type=Path)
    args = parser.parse_args()

    target = borrow.host("Python")
    triple = TARGETS[target]

    tag = args.release or release_tag()
    published = catalogue(tag)
    version, asset = resolve(args.version, triple, published, tag)
    print(f"python-build-standalone {tag}: {args.version} resolves to {version} ({triple})")

    url = f"{REPOSITORY}/releases/download/{tag}/{asset.replace('+', '%2B')}"
    work = Path(tempfile.mkdtemp(prefix="mixengine-python-"))
    downloaded = work / asset
    print(f"borrowing {url}")
    try:
        urllib.request.urlretrieve(url, downloaded)
    except urllib.error.HTTPError as error:
        raise SystemExit(f"{url} answered {error.code}") from error

    actual = borrow.sha256(downloaded)
    if actual != published[asset]:
        raise SystemExit(f"sha256 mismatch: got {actual}, SHA256SUMS says {published[asset]}")
    print(f"sha256 {actual} (verified against SHA256SUMS)")

    tree = borrow.unpack(downloaded, work / "unpacked", "tar.gz")

    added = ensure_pip(tree, target[0])
    manifest = describe(tree, version, target, url, actual, tag, added)
    manifest["smoke"] = smoke(tree, version, manifest)

    # Measured off the archive rather than assumed from the runner. Upstream chose these floors when
    # it chose its build image, which is exactly why they are read rather than written down.
    measured = relocate.floor(tree) if sys.platform != "win32" else None
    if measured:
        manifest["requires"] = {measured[0]: measured[1]}
        print(f"needs {measured[0]} {measured[1]} or newer")

    borrow.publish(tree, manifest, args.out, "zip" if target[0] == "windows" else "tar.zst")
    shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
