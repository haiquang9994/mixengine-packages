#!/usr/bin/env python3
"""Borrow a Node.js build from nodejs.org and repack it as a MixEngine artifact.

Nothing here compiles Node. The official builds are already relocatable — they are the same tarball
every version manager on earth unpacks into a directory of its own — so the whole job is to fetch the
right one, prove it still runs after being moved, describe what is inside it, and repack.

**One recipe for every target**, unlike PHP, which needs three. There is nothing per-OS here beyond
a file name and where the executables sit inside the tree, so a second script would be the same
script with a different table in it.

Three things this deliberately does not do:

*It does not rearrange the directory.* ``npm`` is a script that finds ``node`` relative to its own
location, so moving the two apart produces an archive that fails at run time and not at pack time.
The layout stays as its publisher shipped it and ``mixengine-artifact.json`` says where things are.

*It does not keep the top-level ``node-v22.11.0-linux-x64/`` directory.* That is the one rearrangement
made, and it is forced: MixEngine unpacks an archive straight into ``runtimes/node/<version>/`` and
every path in ``provides`` is relative to that, so a preserved wrapper directory would put the whole
runtime one level below where the index says it is.

*It does not verify a GPG signature.* Node signs ``SHASUMS256.txt`` with a keyring of a dozen
release keys that has to be fetched from a keyserver, which is a moving dependency on a machine with
nothing installed. What is checked is the published SHA-256 over HTTPS to the publisher, which is
exactly what the PHP recipe settles for and is recorded in the manifest as such.

Everything mechanical — downloading, hashing, unwrapping, packing, and running something with a
``PATH`` the runner cannot answer — moved to :mod:`borrow` when Python and Ruby arrived and needed
the identical five functions. What stays here is the resolution against upstream's index and the
smoke test, because those are claims about Node.js rather than about archives.

Python 3 stdlib only, by policy: this runs on a GitHub runner with nothing installed.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import borrow  # noqa: E402  — siblings, and this directory is not importable as a package
import relocate  # noqa: E402

DIST = "https://nodejs.org/dist"

# (os, arch) -> the name upstream builds it under, the archive suffix, and the key the same build is
# listed under in `index.json`'s `files`. The two spellings are not derivable from each other —
# `darwin-arm64` is the file and `osx-arm64-tar` is the listing — so both are written down.
TARGETS = {
    ("windows", "x86_64"): ("win-x64", "zip", "win-x64-zip"),
    ("windows", "aarch64"): ("win-arm64", "zip", "win-arm64-zip"),
    ("macos", "aarch64"): ("darwin-arm64", "tar.gz", "osx-arm64-tar"),
    ("macos", "x86_64"): ("darwin-x64", "tar.gz", "osx-x64-tar"),
    ("linux", "x86_64"): ("linux-x64", "tar.gz", "linux-x64"),
    ("linux", "aarch64"): ("linux-arm64", "tar.gz", "linux-arm64"),
}

# The first release that offers a target at all, so a version below it is refused with the reason
# rather than with a 404. Only three of the six have a floor worth stating: macOS on Apple Silicon
# starts at 16.0.0 — before that the only darwin build is x86_64, which MixEngine will not offer to
# an arm64 machine because it would run under Rosetta — and Windows on ARM starts at 20.0.0. Linux
# arm64 has been built since the v4 line, which is below anything MixEngine offers.
FIRST = {
    ("macos", "aarch64"): (16, 0, 0),
    ("windows", "aarch64"): (20, 0, 0),
}

# Where each command lives inside the tree, per OS. On Windows these sit at the root beside
# `node.exe`; on Unix they are symlinks in `bin/` pointing into `lib/node_modules/`.
#
# **The Windows entries are `.cmd` files and that is deliberate.** `npm` and `npm.cmd` are both in
# the zip: the first is a shell script for Git Bash and Cygwin, the second is what a Windows process
# can actually start. `std::process::Command` runs a `.cmd` — it goes through `cmd.exe`, passes the
# exit code back, and escapes arguments against `&`-style batch injection since the fix for
# CVE-2024-24576 — so the shim needs to learn nothing about scripts to front `npm` here. Measured on
# Windows 11 with rustc 1.97 before this table was written, because the alternative reading is that
# CreateProcess refuses a batch file, which is also true and is what `cmd.exe` exists to bridge.
LAYOUT = {
    "windows": {
        "node": "node.exe",
        "npm": "npm.cmd",
        "npx": "npx.cmd",
        "corepack": "corepack.cmd",
    },
    "unix": {
        "node": "bin/node",
        "npm": "bin/npm",
        "npx": "bin/npx",
        "corepack": "bin/corepack",
    },
}

# Refuse to publish an archive that does not offer these. `corepack` is not among them: it was
# removed from the distribution in Node 25, and an artifact that simply does not carry it is a fact
# about that release rather than a broken pack.
REQUIRED = ("node", "npm")


def resolve(spec: str, target: tuple[str, str]) -> str:
    """Turn ``22``, ``22.11``, ``lts`` or an exact version into a version upstream actually built.

    ``index.json`` is the whole release history, newest first, and it carries two things this needs
    that a URL guess cannot: which line is currently LTS, and whether *this* target has a build at
    all. A version resolved without the second check produces a 404 half a download later, on the
    one runner in the matrix whose architecture is the reason.
    """
    releases = json.loads(borrow.fetch(f"{DIST}/index.json"))
    listed = TARGETS[target][2]

    if spec == "lts":
        candidates = [entry for entry in releases if entry.get("lts")]
    else:
        prefix = tuple(int(piece) for piece in spec.split("."))
        candidates = [
            entry for entry in releases
            # `-` never appears in a version under /dist — release candidates live under
            # /download/rc — but a channel that is not stable would be a channel nobody asked for,
            # and one comparison keeps that true if upstream ever changes its mind.
            if "-" not in entry["version"]
            and borrow.parts(entry["version"].lstrip("v"))[: len(prefix)] == prefix
        ]

    if not candidates:
        raise SystemExit(f"nodejs.org lists no stable release matching {spec!r}")

    buildable = [entry for entry in candidates if listed in entry.get("files", [])]
    if not buildable:
        said = (
            f"node {spec} exists ({candidates[0]['version']}) but upstream publishes no {listed} "
            f"build of it"
        )
        if target in FIRST:
            said += f"; {target[0]}/{target[1]} starts at {'.'.join(str(p) for p in FIRST[target])}"
        borrow.unavailable(said)

    # Newest first in the document, and sorted here anyway: the order is upstream's promise rather
    # than upstream's guarantee, and "newest patch" is the one claim this function makes.
    version = max((entry["version"].lstrip("v") for entry in buildable), key=borrow.parts)

    floor = FIRST.get(target)
    if floor and borrow.parts(version) < floor:
        borrow.unavailable(
            f"node {version} predates the first native build for {target[0]}/{target[1]} "
            f"({'.'.join(str(p) for p in floor)}). An emulated build is not offered."
        )
    return version


def published_hash(version: str, name: str) -> str:
    """The SHA-256 upstream states for *name*, from the release's own ``SHASUMS256.txt``.

    Not optional, unlike the PHP archives — Node has published this file for every release in the
    range MixEngine offers, so a missing entry means the file being fetched is not the file upstream
    describes, and that is a refusal rather than a note in the manifest.
    """
    sums = borrow.fetch(f"{DIST}/v{version}/SHASUMS256.txt").decode("utf-8", "replace")
    for line in sums.splitlines():
        digest, _, filename = line.partition("  ")
        if filename.strip() == name:
            return digest.strip()
    raise SystemExit(f"{name} is not in SHASUMS256.txt for v{version}")


def describe(tree: Path, version: str, target: tuple[str, str], url: str, digest: str) -> dict:
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

    return {
        "schema": 1,
        "kind": "node",
        "version": version,
        "os": operating_system,
        "arch": arch,
        "source": "borrowed",
        "upstream": {
            "url": url,
            "sha256": digest,
            "verified_against": "nodejs.org SHASUMS256.txt over HTTPS to the publisher",
        },
        "provides": provides,
    }


def smoke(tree: Path, version: str, manifest: dict) -> dict:
    """Run the artifact from somewhere it has never been, and make it be itself while doing it.

    ``node --version`` alone would pass on an archive that is unusable, twice over: the runner's own
    Node could be answering, and ``npm`` — a script that has to find its own interpreter and its own
    JavaScript relative to a path it computes at run time — is where a moved tree actually breaks.
    So four things are proven rather than one.

    *It is this Node.* ``process.execPath`` has to be inside the relocated tree.

    *It is this npm.* The version ``npm`` reports has to be the one written in the ``package.json``
    that came in the same archive, which is a fact no other Node.js on the machine can produce.

    *It carries a full ICU.* A Node built with ``small-icu`` formats every locale as English and
    fails nothing while doing it, which surfaces months later as a date in the wrong language.

    *It carries a working OpenSSL*, called rather than merely reported: hashing a string exercises
    the bundled library, where reading ``process.versions.openssl`` exercises a string constant.
    """
    elsewhere = borrow.moved(tree)

    if sys.platform != "win32":
        problems = relocate.verify(elsewhere)
        for problem in problems:
            print(f"error: {problem}", file=sys.stderr)
        if problems:
            raise SystemExit("the relocated tree reaches outside itself")

    node = elsewhere / manifest["provides"]["node"]
    path = borrow.clean_path(node.parent)

    banner = borrow.run(node, "--version", path=path)
    if banner != f"v{version}":
        raise SystemExit(f"node reports {banner!r}, expected v{version}")

    running = borrow.run(node, "-p", "process.execPath", path=path)
    if not Path(running).resolve().is_relative_to(elsewhere.resolve()):
        raise SystemExit(f"node ran from {running}, which is not the relocated tree")

    formatted = borrow.run(node, "-p", "new Intl.NumberFormat('de-DE').format(1234.5)", path=path)
    if formatted != "1.234,5":
        raise SystemExit(
            f"Intl formatted 1234.5 as {formatted!r} rather than '1.234,5', so this build has a "
            "small-icu and would silently format every locale as English"
        )

    hashed = borrow.run(
        node, "-p", "require('crypto').createHash('sha256').update('mixengine').digest('hex')",
        path=path,
    )
    if len(hashed) != 64:
        raise SystemExit(f"the bundled OpenSSL answered {hashed!r}")

    ran = [f"{manifest['provides']['node']} --version", "node -p (execPath, Intl, crypto)"]

    packages = elsewhere / ("node_modules" if sys.platform == "win32" else "lib/node_modules")
    for name in sorted(set(manifest["provides"]) - {"node"}):
        reported = borrow.run(elsewhere / manifest["provides"][name], "--version", path=path)
        packaged = packages / name / "package.json"
        if packaged.exists():
            expected = json.loads(packaged.read_text(encoding="utf-8"))["version"]
            if reported != expected:
                raise SystemExit(
                    f"{name} reports {reported}, but the {name} inside this archive is {expected} — "
                    f"something else on this machine answered"
                )
        print(f"{manifest['provides'][name]}: {reported}")
        ran.append(f"{manifest['provides'][name]} --version")

    borrow.discard(elsewhere)
    return {"relocated": True, "ran": ran}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--version", required=True,
        help="exact version (22.11.0), a line (22 or 22.11) for its newest release, or 'lts'",
    )
    parser.add_argument("--out", default="dist", type=Path)
    args = parser.parse_args()

    target = borrow.host("Node.js")
    upstream_name, suffix, _ = TARGETS[target]

    version = resolve(args.version, target)
    if version != args.version:
        print(f"{args.version} resolved to {version}")

    name = f"node-v{version}-{upstream_name}"
    url = f"{DIST}/v{version}/{name}.{suffix}"
    expected = published_hash(version, f"{name}.{suffix}")

    work = Path(tempfile.mkdtemp(prefix="mixengine-node-"))
    downloaded = work / f"{name}.{suffix}"
    print(f"borrowing {url}")
    try:
        urllib.request.urlretrieve(url, downloaded)
    except urllib.error.HTTPError as error:
        raise SystemExit(f"{url} answered {error.code}") from error

    actual = borrow.sha256(downloaded)
    if actual != expected:
        raise SystemExit(f"sha256 mismatch: got {actual}, SHASUMS256.txt says {expected}")
    print(f"sha256 {actual} (verified against SHASUMS256.txt)")

    tree = borrow.unpack(downloaded, work / "unpacked", suffix)

    manifest = describe(tree, version, target, url, actual)
    manifest["smoke"] = smoke(tree, version, manifest)

    # Measured off the archive rather than assumed from the runner: an official Linux build is not
    # static, so it carries a glibc floor of upstream's choosing rather than of ours, and a macOS
    # one carries the minimum its own load commands state. Neither is a number this recipe controls,
    # which is exactly why it is read rather than written down.
    measured = relocate.floor(tree) if sys.platform != "win32" else None
    if measured:
        manifest["requires"] = {measured[0]: measured[1]}
        print(f"needs {measured[0]} {measured[1]} or newer")

    borrow.publish(tree, manifest, args.out, suffix)
    shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
