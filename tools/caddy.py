#!/usr/bin/env python3
"""Borrow a Caddy build from caddyserver/caddy and repack it as a MixEngine artifact.

**The first service in this repository, and the shortest borrow in it.** Caddy releases one
statically linked Go binary per target, with no interpreter to find, no standard library to locate
and no CA bundle to resolve — the three things every runtime recipe here spends its length on. What
is left is a download, a checksum, and a proof that the binary MixEngine is going to *supervise* can
actually be supervised.

That proof is why this recipe is not simply :mod:`node` with a different table. A runtime is packed
to be *executed*: `php -v` answering from the moved tree is the whole claim. A service is packed to
be *run, configured, watched and stopped*, and each of those is something MixEngine's Caddy recipe
(T31) will do through a specific mechanism — a Caddyfile it renders, ``caddy validate`` before it
installs one, an admin endpoint it health-checks, and ``caddy stop`` against that same endpoint. So
the smoke test does all four, against the archive, from a directory it was moved to. An artifact
that answers ``caddy version`` and cannot be reloaded is one T31 would find out about at run time.

Three things this deliberately does not do:

*It does not keep the ``.sig``/``.pem`` pair beside each asset.* Caddy signs its releases with
sigstore, which is checked with ``cosign`` — a tool no runner in this matrix carries and one more
moving dependency on a machine with nothing installed. What is checked instead is the SHA-512 in
upstream's own ``caddy_<version>_checksums.txt``, fetched over HTTPS from the publisher, which is
exactly the trade the Node.js recipe records for ``SHASUMS256.txt``.

*It does not build a custom Caddy.* ``xcaddy`` exists and plugins are the reason anybody uses it, but
a plugin set baked into an artifact is a plugin set that cannot change without a repack, and
MixEngine's promise is a web server that works out of the box rather than one nobody else can
reproduce. The standard distribution is what is packed; T31's Caddyfile uses nothing outside it.

*It does not put a Caddyfile in the archive.* Configuration is generated from state by
``core::generate`` and is disposable by design — an archive shipping a default one would be shipping
a file the daemon must then decide whether to overwrite.

Python 3 stdlib only, by policy: this runs on a GitHub runner with nothing installed.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import borrow  # noqa: E402  — siblings, and this directory is not importable as a package
import relocate  # noqa: E402

API = "https://api.github.com/repos/caddyserver/caddy/releases"

# (os, arch) -> the name upstream builds it under, and the archive suffix. `mac` rather than
# `darwin` is upstream's own spelling and is the only place the two tables disagree.
TARGETS = {
    ("windows", "x86_64"): ("windows_amd64", "zip"),
    ("windows", "aarch64"): ("windows_arm64", "zip"),
    ("macos", "aarch64"): ("mac_arm64", "tar.gz"),
    ("macos", "x86_64"): ("mac_amd64", "tar.gz"),
    ("linux", "x86_64"): ("linux_amd64", "tar.gz"),
    ("linux", "aarch64"): ("linux_arm64", "tar.gz"),
}

# There is no `FIRST` table here, unlike the Node.js recipe: which targets a release built is read
# off the release's own asset list, so an empty cell states itself rather than being written down
# and going stale. For the record, the two that have a floor at all: `mac_arm64` starts at 2.4.0 and
# `windows_arm64` at 2.4.5.

# Caddy 1 is a different program that happens to share a name: no admin API, no JSON config, and a
# Caddyfile whose directives mean other things. Refusing it by number is clearer than letting the
# asset lookup fail on a file naming scheme that also changed.
MAJOR = 2

# What the archive is expected to hold, per OS. One entry, because Caddy is one binary — and it is
# a table anyway so that the check below can name what it looked for.
LAYOUT = {"windows": {"caddy": "caddy.exe"}, "unix": {"caddy": "caddy"}}


def releases() -> list[dict]:
    """Every Caddy release, newest page first.

    The GitHub API for the same reason :mod:`ruby` uses it — the tags are the catalogue, stated
    nowhere else — and with the same token handling: unauthenticated requests are limited to sixty
    an hour per IP address, and GitHub's runners share those.
    """
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    found: list[dict] = []
    for page in (1, 2):
        request = urllib.request.Request(f"{API}?per_page=100&page={page}", headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                page_of = json.loads(response.read())
        except urllib.error.HTTPError as error:
            if error.code in (403, 429) and not token:
                raise SystemExit(
                    "github.com rate-limited the release listing and no GITHUB_TOKEN was set"
                ) from error
            raise SystemExit(f"the Caddy release listing answered {error.code}") from error
        found += page_of
        if len(page_of) < 100:
            break
    return found


def resolve(spec: str, target: tuple[str, str]) -> tuple[str, str, str]:
    """Turn ``2``, ``2.11``, ``2.11.4`` or ``latest`` into one published archive.

    Answers ``(version, archive url, checksums url)``. Both URLs come out of the same release, so a
    checksum can never be read from a different one than the archive it is being compared against —
    which is the whole reason the checksums file is resolved here rather than composed from a
    template later.

    A line with no build for this target is an **empty cell and not a failure**: Caddy 2.0 has no
    Apple Silicon archive, and asking for one should not take the other five legs' release down.
    """
    offered: dict[tuple[int, ...], tuple[str, str, str]] = {}
    lines: set[str] = set()
    name, suffix = TARGETS[target]

    for release in releases():
        if release.get("draft") or release.get("prerelease"):
            continue
        match = re.fullmatch(r"v(\d+\.\d+\.\d+)", release.get("tag_name", ""))
        if not match:
            continue
        version = match.group(1)
        if borrow.parts(version)[0] != MAJOR:
            continue

        assets = {asset["name"]: asset["browser_download_url"] for asset in release.get("assets", ())}
        archive = f"caddy_{version}_{name}.{suffix}"
        checksums = f"caddy_{version}_checksums.txt"
        if archive not in assets:
            continue
        if checksums not in assets:
            # Not skipped quietly: a release whose archive exists and whose checksums file does not
            # is one this recipe would have to publish unverified, and it would rather say so.
            raise SystemExit(f"v{version} publishes {archive} but no {checksums}")
        lines.add(".".join(version.split(".")[:2]))
        offered[borrow.parts(version)] = (version, assets[archive], assets[checksums])

    if not offered:
        borrow.unavailable(f"caddyserver/caddy publishes no {name} archive in any {MAJOR}.x release")

    if spec == "latest":
        candidates = sorted(offered)
    else:
        prefix = borrow.parts(spec)
        if prefix[0] != MAJOR:
            raise SystemExit(
                f"MixEngine offers Caddy {MAJOR}.x only: it renders a Caddyfile for that language "
                f"and reloads through the admin API, and Caddy 1 has neither"
            )
        candidates = sorted(key for key in offered if key[: len(prefix)] == prefix)

    if not candidates:
        borrow.unavailable(
            f"caddyserver/caddy has no {spec} archive for {name}. It offers "
            f"{', '.join(sorted(lines, key=borrow.parts))}."
        )
    return offered[candidates[-1]]


def published_hash(checksums_url: str, archive_name: str) -> str:
    """The SHA-512 upstream states for *archive_name*, from the release's own checksums file.

    Not optional. Every Caddy release in the range MixEngine offers publishes one, so a missing
    entry means the file being fetched is not the file upstream describes.
    """
    listing = borrow.fetch(checksums_url).decode("utf-8", "replace")
    for line in listing.splitlines():
        digest, _, filename = line.partition("  ")
        if filename.strip() == archive_name:
            return digest.strip()
    raise SystemExit(f"{archive_name} is not in {checksums_url.rsplit('/', 1)[-1]}")


def describe(tree: Path, version: str, target: tuple[str, str], url: str, digest: str) -> dict:
    """What is in the archive, as the daemon will read it."""
    operating_system, arch = target
    layout = LAYOUT["windows" if operating_system == "windows" else "unix"]

    provides = {name: path for name, path in layout.items() if (tree / path).exists()}
    if "caddy" not in provides:
        raise SystemExit(
            f"the archive provides no caddy — expected at {layout['caddy']}. Contents: "
            f"{sorted(path.name for path in tree.iterdir())[:20]}"
        )

    return {
        "schema": 1,
        "kind": "caddy",
        "version": version,
        "os": operating_system,
        "arch": arch,
        "source": "borrowed",
        "upstream": {
            "project": "caddyserver/caddy",
            "release": f"v{version}",
            "url": url,
            # The archive's SHA-256, computed here, because that is the field the schema carries and
            # what the index will state. What it was *checked* against is upstream's SHA-512, which
            # is the algorithm Caddy publishes — the two are not in competition: the download was
            # accepted because it matched upstream's digest, and this is how the same bytes are
            # named everywhere else in this repository.
            "sha256": digest,
            "verified_against": (
                "caddy_<version>_checksums.txt (SHA-512) over HTTPS to the publisher"
            ),
        },
        "provides": provides,
    }


def free_port() -> int:
    """A port nothing is listening on, as the kernel's own answer rather than as a guess.

    Racy in principle — it is closed before Caddy binds it — and the alternative is a hard-coded
    2019, which is *reliably* wrong on a machine already running a Caddy, including a developer's.
    """
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def get(url: str, timeout: float = 5) -> bytes:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return response.read()


def await_admin(url: str, process: subprocess.Popen, log: Path, seconds: float = 30) -> None:
    """Wait for the admin endpoint to answer, or say what Caddy said instead."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise SystemExit(
                f"caddy exited {process.returncode} before its admin endpoint answered\n"
                f"{log.read_text(encoding='utf-8', errors='replace')}"
            )
        try:
            get(url, timeout=2)
            return
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
            time.sleep(0.2)
    process.kill()
    raise SystemExit(
        f"{url} never answered\n{log.read_text(encoding='utf-8', errors='replace')}"
    )


def smoke(tree: Path, version: str, manifest: dict) -> dict:
    """Run the artifact from somewhere it has never been, and make it be a *web server* while there.

    ``caddy version`` proves the binary starts. It does not prove any of the four things MixEngine
    will actually do to it, so each is done here instead, in the order T31 will do them:

    *It validates a Caddyfile.* ``caddy validate`` is what ``core::generate`` runs against a staged
    configuration before renaming it into place, and it exercises the caddyfile *adapter* — a module
    inside the binary rather than a flag, and the one thing a stripped-down custom build would be
    missing.

    *It runs the configuration.* ``caddy run`` rather than ``caddy start``, because that is what the
    supervisor will exec and because ``start`` hands its child the parent's stdout and then returns:
    anything capturing that output waits for the *server* to exit, which is a hang rather than a
    failure and took a two-minute timeout to see.

    *It answers on its admin endpoint.* ``GET /config/`` is the ``ReadyCheck::Http`` T31 declares,
    and this is the check that would have caught an artifact whose admin API is compiled out.

    *It serves a request*, which is the only one of the four that proves the thing anybody wants.

    *And it stops the way it will be stopped* — ``caddy stop`` against the admin address, which is
    T31's ``StopBehaviour::Command`` and is what makes a reload possible at all.

    Automatic HTTPS is off throughout: a smoke test that reached out to Let's Encrypt would be
    testing the runner's network, and a certificate is [T48](phase-5-https.md)'s subject rather than
    this archive's.
    """
    elsewhere = borrow.moved(tree)

    if sys.platform != "win32":
        # The payload is at the root of the tree, so `relocate` has to be told where to look —
        # its default is a list of subdirectories and this archive has none. A Go binary imports
        # nothing outside libSystem on macOS and nothing at all on Linux, so the expected answer
        # is "no problems"; what is being guarded against is a future artifact that is not one
        # static binary, which would otherwise pass by being unexamined.
        problems = relocate.verify(elsewhere, directories=("",))
        for problem in problems:
            print(f"error: {problem}", file=sys.stderr)
        if problems:
            raise SystemExit("the relocated tree reaches outside itself")

    caddy = elsewhere / manifest["provides"]["caddy"]
    path = borrow.clean_path(caddy.parent)

    banner = borrow.run(caddy, "version", path=path)
    if not banner.startswith(f"v{version} "):
        raise SystemExit(f"caddy reports {banner!r}, expected a v{version} build")
    print(f"caddy version: {banner}")

    admin, http = free_port(), free_port()
    work = elsewhere.parent / "config"
    work.mkdir(parents=True, exist_ok=True)
    expected = f"mixengine {version} on {manifest['os']}/{manifest['arch']}"
    caddyfile = work / "Caddyfile"
    caddyfile.write_text(
        # `persist_config off` because a smoke test must not write into the user's — or the
        # runner's — application data directory, which is where Caddy autosaves the last
        # configuration it was given and where it would then find it again on an unrelated run.
        f"{{\n"
        f"\tadmin localhost:{admin}\n"
        f"\tauto_https off\n"
        f"\tpersist_config off\n"
        f"}}\n"
        f"http://localhost:{http} {{\n"
        f"\trespond \"{expected}\"\n"
        f"}}\n",
        encoding="utf-8",
    )

    borrow.run(caddy, "validate", "--adapter", "caddyfile", "--config", str(caddyfile), path=path)
    print("caddy validate: accepted the rendered Caddyfile")

    log = work / "caddy.log"
    environment = {**os.environ, "PATH": path}
    with log.open("wb") as sink:
        process = subprocess.Popen(
            [str(caddy), "run", "--adapter", "caddyfile", "--config", str(caddyfile)],
            stdout=sink, stderr=subprocess.STDOUT, env=environment, cwd=str(work),
        )

    try:
        await_admin(f"http://localhost:{admin}/config/", process, log)
        configured = json.loads(get(f"http://localhost:{admin}/config/"))
        if configured.get("admin", {}).get("listen") != f"localhost:{admin}":
            raise SystemExit(f"the admin endpoint answered with somebody else's config: {configured}")
        print(f"caddy admin: /config/ answered on localhost:{admin}")

        served = get(f"http://localhost:{http}/").decode("utf-8", "replace")
        if served != expected:
            raise SystemExit(f"the server answered {served!r}, expected {expected!r}")
        print(f"caddy served: {served}")

        borrow.run(caddy, "stop", "--address", f"localhost:{admin}", path=path)
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            process.kill()
            raise SystemExit("caddy stop returned and the server was still running") from None
        print("caddy stop: the server exited")
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=30)

    borrow.discard(elsewhere)
    return {
        "relocated": True,
        "ran": [
            f"{manifest['provides']['caddy']} version",
            "caddy validate --adapter caddyfile",
            "caddy run, GET /config/ on the admin endpoint, a request served",
            "caddy stop --address",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--version", required=True,
        help="exact version (2.11.4), a line (2 or 2.11) for its newest release, or 'latest'",
    )
    parser.add_argument("--out", default="dist", type=Path)
    args = parser.parse_args()

    target = borrow.host("Caddy")
    _, suffix = TARGETS[target]

    version, url, checksums = resolve(args.version, target)
    if version != args.version:
        print(f"{args.version} resolved to {version}")

    name = url.rsplit("/", 1)[-1]
    expected = published_hash(checksums, name)

    work = Path(tempfile.mkdtemp(prefix="mixengine-caddy-"))
    downloaded = work / name
    print(f"borrowing {url}")
    try:
        urllib.request.urlretrieve(url, downloaded)
    except urllib.error.HTTPError as error:
        raise SystemExit(f"{url} answered {error.code}") from error

    actual512 = borrow.sha512(downloaded)
    if actual512 != expected:
        raise SystemExit(f"sha512 mismatch: got {actual512}, checksums.txt says {expected}")
    print(f"sha512 {actual512} (verified against caddy_{version}_checksums.txt)")

    # The payload is at the root of this archive rather than inside a wrapper directory, which is
    # true of no runtime here and of every single-binary service.
    tree = borrow.unpack(downloaded, work / "unpacked", suffix, wrapped=False)

    manifest = describe(tree, version, target, url, borrow.sha256(downloaded))
    manifest["smoke"] = smoke(tree, version, manifest)

    # Read rather than assumed, as in the Node.js recipe — with the difference that the expected
    # answer here is *none*: Caddy is built with CGO off, so a Linux binary imports no glibc symbol
    # to carry a floor. Measuring it is how that stays a fact about the artifact rather than a
    # belief about upstream's build flags.
    measured = relocate.floor(tree, directories=("",)) if sys.platform != "win32" else None
    if measured:
        manifest["requires"] = {measured[0]: measured[1]}
        print(f"needs {measured[0]} {measured[1]} or newer")

    borrow.publish(tree, manifest, args.out, suffix)
    shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
