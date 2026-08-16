#!/usr/bin/env python3
"""Compile memcached from upstream source for the four cells that can run it, and say so about two.

**The Windows cells are empty for the same reason Redis's are, and the reason is not a hard build.**
memcached is autotools and POSIX: ``configure.ac`` and ``Makefile.am``, no ``CMakeLists.txt``, no
``win32/`` directory and no project file, with a privilege-dropping source file per Unix —
``linux_priv.c``, ``darwin_priv.c``, ``freebsd_priv.c``, ``openbsd_priv.c``, ``solaris_priv.c`` — and
none for Windows. Its own download page offers a source tarball and points Linux users at their
distribution's packages; it offers nothing for Windows and never has. So P8's question, *compile
natively on a Windows runner or declare the cell empty*, has no first option to weigh. The cell is
stated rather than filled, and the Windows leg of the workflow runs and exits 75 so that it is
stated on every run.

What the four remaining cells get:

*A static libevent, pinned here rather than taken from the machine.* memcached is a thin layer over
an event loop and links nothing else. Taking the runner's libevent would make each artifact carry
whatever that image happened to have — the thing this repository levels out everywhere else — and
would leave a shared object to bundle and re-point afterwards. Compiled static into the binary
instead, the artifact is one file that imports nothing outside the C runtime, and ``relocate.verify``
is what says so rather than this paragraph. The version is written down with its SHA-256 and checked,
which is one better than :mod:`ruby_unix` does for the three libraries it pins, and it costs three
lines.

*No TLS, no SASL, no proxy.* Each is a ``configure`` flag, a dependency and a feature of a cache
somebody else operates. MixEngine supervises one instance on loopback for one developer.

*No ``shutdown`` command, deliberately.* ``--enable-shutdown`` would give the supervisor a graceful
stop to send, and what it actually gives is an unauthenticated ``shutdown`` verb on a loopback port
that any page served by the same machine can reach.
[ADR 0008](https://github.com/haiquang9994/MixEngine/blob/master/.claude/decisions/0008-no-signal-stop-on-windows.md)
already names Memcached as a service where stopping without a signal costs nothing — a cache has
nothing unflushed to lose — so the smoke test stops it the way the supervisor will, by terminating
it, and checks that it goes.

*The digest is upstream's SHA-1, and that is worth saying plainly rather than smoothing over.*
memcached publishes a ``<tarball>.sha1`` beside every release and publishes nothing stronger. SHA-1
is not collision-resistant, so what this check is worth is what it is: proof that the bytes fetched
over TLS from memcached.org are the bytes memcached.org describes, and not proof against an attacker
who can choose both halves of a pair. The transport is doing most of the work here, which is the
honest description and the reason the recipe prints which algorithm it used.

Python 3 stdlib only, by policy: this runs on a GitHub runner with nothing installed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import borrow  # noqa: E402  — siblings, and this directory is not importable as a package
import relocate  # noqa: E402

# memcached publishes no GitHub releases at all — the API answers with an empty list — so the tags
# are the catalogue, and www.memcached.org/files/ is the distribution. Two documents rather than one,
# which is a step down from what `caddy.py` and `redis.py` get, and the sidecar below is what keeps
# a digest from ever being read out of a different release than the archive it describes: it lives
# at the archive's own URL plus a suffix.
TAGS = "https://api.github.com/repos/memcached/memcached/tags"
FILES = "https://www.memcached.org/files"

# The line MixEngine's services table names, and the only one upstream has patched since 2018.
FLOOR = (1, 6)

# Pinned rather than taken from the runner. 2.1.13-stable is the current stable release — July 2026,
# carrying security fixes to evbuffer, bufferevent, evdns and evhttp — and 2.2.x is still alpha.
LIBEVENT = {
    "version": "2.1.13-stable",
    "url": (
        "https://github.com/libevent/libevent/releases/download/"
        "release-2.1.13-stable/libevent-2.1.13-stable.tar.gz"
    ),
    "sha256": "f7e9383b8c0baa81b687e5b5eecc01beefaf1b19b64151d95ed61647fe7a315c",
}

# One binary, which is the whole of what memcached installs into `bin/`: `bin_PROGRAMS = memcached`.
LAYOUT = {"memcached": "bin/memcached"}

# Installed by upstream and then thrown away, because the second half of *One version means one
# thing, and no more than is needed* names both outright. `include/memcached/` is
# `protocol_binary.h` and `xxhash.h` — a linker's input, and this installs a cache rather than an
# SDK — and `share/man` is a manual page in an archive nobody reads a manual page out of.
PRUNE = ("include", "share/man")

# Everything redistributed by this archive, and the second line is not optional: libevent is
# compiled *into* the binary, so its BSD-3 text has to travel with it exactly as if it were a
# separate file in the tree. The two extra memcached files are third-party code vendored into its
# own sources under their own terms.
OWN_LICENCES = ("COPYING", "LICENSE", "LICENSE.bipbuffer", "LICENSE.itoa_ljust")
LIBEVENT_LICENCES = ("LICENSE",)


def run(*command: str, cwd: Path | None = None, env: dict | None = None,
        timeout: int = 3600) -> None:
    print("$ " + " ".join(str(part) for part in command), flush=True)
    result = subprocess.run([str(part) for part in command], cwd=cwd, env=env, timeout=timeout)
    if result.returncode != 0:
        raise SystemExit(f"{command[0]} exited {result.returncode}")


def jobs() -> str:
    return str(os.cpu_count() or 2)


def tags() -> list[str]:
    """Every release memcached has tagged, newest first.

    The GitHub API for the reason :mod:`caddy` uses it — the tags are the catalogue and nothing else
    states it — and with the same token handling, because unauthenticated requests are limited to
    sixty an hour *per IP address* and GitHub's runners share those.
    """
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    found: list[str] = []
    for page in (1, 2):
        request = urllib.request.Request(f"{TAGS}?per_page=100&page={page}", headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                listing = json.loads(response.read())
        except urllib.error.HTTPError as error:
            if error.code in (403, 429) and not token:
                raise SystemExit(
                    "github.com rate-limited the tag listing and no GITHUB_TOKEN was set"
                ) from error
            raise SystemExit(f"the memcached tag listing answered {error.code}") from error
        found += [tag.get("name", "") for tag in listing]
        if len(listing) < 100:
            break
    return found


def resolve(spec: str) -> str:
    """Turn ``1``, ``1.6``, ``1.6.45`` or ``latest`` into one tagged release.

    memcached tags a few branches by name as well — ``flash-with-wbuf-stack`` is at the top of the
    listing at the time of writing — so the pattern insists on three numeric components rather than
    trusting the order.
    """
    offered = {
        borrow.parts(name) for name in tags() if re.fullmatch(r"\d+\.\d+\.\d+", name)
    }
    offered = {key for key in offered if key[:2] >= FLOOR}
    if not offered:
        raise SystemExit("the memcached tag listing named no x.y.z release; upstream changed shape")

    if spec == "latest":
        candidates = sorted(offered)
    else:
        prefix = borrow.parts(spec)
        candidates = sorted(key for key in offered if key[: len(prefix)] == prefix)
    if not candidates:
        # On the tuple rather than on the text, for the reason `redis.resolve` states: `1.10` would
        # otherwise print before `1.6`.
        lines = [".".join(str(part) for part in line) for line in sorted({key[:2] for key in offered})]
        raise SystemExit(
            f"memcached has no {spec} at or above {'.'.join(str(part) for part in FLOOR)}. "
            f"It offers {', '.join(lines)}."
        )
    return ".".join(str(part) for part in candidates[-1])


def published_sha1(name: str) -> str:
    """The SHA-1 upstream states for *name*, from the sidecar beside the archive itself.

    Not optional, and not the strongest thing a publisher could offer — see the module docstring.
    The line is ``<digest>  ./<name>``, and the file name in it is checked rather than skipped: a
    sidecar naming a different archive is a sidecar that was fetched for a different release.
    """
    listing = borrow.fetch(f"{FILES}/{name}.sha1").decode("utf-8", "replace").split()
    if len(listing) != 2 or Path(listing[1]).name != name:
        raise SystemExit(f"{name}.sha1 does not describe {name}: {' '.join(listing)!r}")
    return listing[0]


def sha1(path: Path) -> str:
    """The one algorithm memcached publishes. :mod:`borrow` has no helper for it, and should not."""
    digest = hashlib.sha1()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def source(spec: str, work: Path) -> tuple[str, Path, str, str]:
    """Fetch and unpack the release tarball, checked against the digest beside it."""
    version = resolve(spec)
    if version != spec:
        print(f"{spec} resolves to memcached {version}")

    name = f"memcached-{version}.tar.gz"
    url = f"{FILES}/{name}"
    stated = published_sha1(name)

    tarball = work / name
    print(f"fetching {url}")
    tarball.write_bytes(borrow.fetch(url, timeout=1800))
    actual = sha1(tarball)
    if actual != stated:
        raise SystemExit(f"{name} hashes to sha1 {actual}, {name}.sha1 states {stated}")
    print(f"sha1 {actual} (verified against {name}.sha1, which is what memcached.org publishes)")

    with tarfile.open(tarball) as archive:
        archive.extractall(work, filter="data")
    unpacked = work / f"memcached-{version}"
    if not (unpacked / "configure").is_file():
        raise SystemExit(f"{unpacked} has no configure; this is not a memcached release tarball")
    return version, unpacked, actual, url


def build_libevent(work: Path, prefix: Path) -> Path:
    """Compile the pinned libevent as a static library, and answer with where it went."""
    directory = work / "libevent"
    directory.mkdir(parents=True, exist_ok=True)
    tarball = directory / LIBEVENT["url"].rsplit("/", 1)[-1]
    print(f"fetching {LIBEVENT['url']}")
    tarball.write_bytes(borrow.fetch(LIBEVENT["url"], timeout=1800))
    actual = borrow.sha256(tarball)
    if actual != LIBEVENT["sha256"]:
        raise SystemExit(
            f"{tarball.name} hashes to {actual}, this recipe pins {LIBEVENT['sha256']}. Either the "
            f"download is not upstream's or the pin is stale — check the release before changing it."
        )
    print(f"sha256 {actual} (the version this recipe pins)")

    with tarfile.open(tarball) as archive:
        archive.extractall(directory, filter="data")
    unpacked = directory / f"libevent-{LIBEVENT['version']}"

    run(
        "./configure", f"--prefix={prefix}", "--disable-shared", "--enable-static",
        # Off because none of it is linked into memcached and each is either a dependency or a
        # build this artifact would be waiting on: `openssl` is the TLS the docstring declines,
        # `samples` and `libevent-regress` are demonstration programs and a test suite.
        "--disable-openssl", "--disable-samples", "--disable-libevent-regress",
        cwd=unpacked, env=os.environ.copy(),
    )
    run("make", f"-j{jobs()}", cwd=unpacked)
    run("make", "install", cwd=unpacked)
    return unpacked


def build(source_tree: Path, prefix: Path, libevent_prefix: Path) -> list[str]:
    """Configure, compile and install memcached against the libevent just built."""
    asked = ["./configure", f"--prefix={prefix}", f"--with-libevent={libevent_prefix}"]
    run(*asked, cwd=source_tree, env=os.environ.copy())
    run("make", f"-j{jobs()}", cwd=source_tree)
    run("make", "install", cwd=source_tree)
    return [f"./configure --prefix=… --with-libevent=… (libevent {LIBEVENT['version']}, static)"]


def licences(tree: Path, source_tree: Path, libevent_source: Path) -> None:
    """Ship the licence of memcached and of the library compiled into it.

    Both require their text to travel with the binary, so this is a condition of redistributing the
    archive rather than tidiness — and libevent is the half a walk over the *tree* would miss
    entirely, because after a static link there is no file in the archive that came from it.
    """
    into = tree / "licenses"
    into.mkdir(exist_ok=True)

    shipped = []
    for name in OWN_LICENCES:
        if (source_tree / name).is_file():
            shutil.copy2(source_tree / name, into / f"memcached-{name}")
            shipped.append(name)
    if "COPYING" not in shipped:
        raise SystemExit("the tarball has no COPYING; nothing states memcached's own terms")

    for name in LIBEVENT_LICENCES:
        text = libevent_source / name
        if not text.is_file():
            raise SystemExit(
                f"libevent {LIBEVENT['version']} has no {name}, and it is linked into every binary "
                f"in this archive — there is nothing to redistribute it under"
            )
        shutil.copy2(text, into / f"libevent-{name}")
        shipped.append(f"libevent {name}")
    print(f"shipping {len(shipped)} licence file(s): {', '.join(shipped)}")


def assemble(prefix: Path, work: Path) -> tuple[Path, dict[str, str]]:
    """The one file that ships, lifted out of a prefix that holds two projects' installs.

    **Copied in rather than pruned out**, which is the opposite of what every other recipe here
    does, and the reason is that this prefix is not memcached's. libevent installed into it too —
    static archives, an ``event2/`` header tree, pkg-config files and a code generator — and none of
    that ships, because a static link leaves nothing in the archive that came from it. Listing what
    to delete would be a list that goes stale the next time libevent's install target grows a file;
    listing what to keep is :data:`LAYOUT`, which is already the manifest's own claim.

    :data:`PRUNE` is what memcached itself installs beyond that, named so the rule it falls under is
    written down rather than implied by an empty copy list.
    """
    installed = sorted(
        path.relative_to(prefix).as_posix() for path in prefix.rglob("*") if path.is_file()
    )
    tree = work / "tree"
    for relative in LAYOUT.values():
        wanted = prefix / relative
        if not wanted.is_file():
            raise SystemExit(
                f"the build installed no {relative}. The prefix holds: {', '.join(installed[:20])}"
            )
        (tree / relative).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(wanted, tree / relative)

    dropped = [name for name in installed if name not in set(LAYOUT.values())]
    under_rule = [name for name in dropped if name.startswith(PRUNE)]
    print(
        f"shipping {', '.join(LAYOUT.values())}; leaving {len(dropped)} installed file(s) behind, "
        f"{len(under_rule)} of them memcached's own ({', '.join(PRUNE)}) and the rest libevent's"
    )
    return tree, dict(LAYOUT)


def free_port() -> int:
    """A port nothing is listening on, as the kernel's own answer rather than as a guess.

    The alternative is a hard-coded 11211, which is *reliably* wrong on a machine already running a
    memcached, including a developer's — and which would let this check pass against somebody
    else's cache rather than against the binary it just built.
    """
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def talk(port: int, request: str, timeout: float = 5) -> str:
    """Say something in memcached's text protocol and read until it stops.

    Raw sockets rather than a client, because memcached ships none: ``bin/memcached`` is the whole
    archive, and there is no ``memcached-cli`` to prove anything with. The protocol is
    newline-terminated and every reply here ends in a line this can recognise, so reading to
    ``END``/``STORED``/``VERSION`` is the terminator rather than waiting for a close.
    """
    endings = ("END\r\n", "STORED\r\n", "ERROR\r\n")
    with socket.create_connection(("127.0.0.1", port), timeout=timeout) as connection:
        connection.sendall(request.encode("utf-8"))
        received = b""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            chunk = connection.recv(65536)
            if not chunk:
                break
            received += chunk
            text = received.decode("utf-8", "replace")
            if text.endswith(endings) or (text.startswith("VERSION ") and text.endswith("\r\n")):
                return text
        return received.decode("utf-8", "replace")


def await_version(port: int, process: subprocess.Popen, log: Path, seconds: float = 30) -> str:
    """Wait for the cache to answer, or say what it said instead."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise SystemExit(
                f"memcached exited {process.returncode} before it answered\n"
                f"{log.read_text(encoding='utf-8', errors='replace')}"
            )
        try:
            answer = talk(port, "version\r\n", timeout=2)
        except (ConnectionError, TimeoutError, OSError):
            time.sleep(0.2)
            continue
        if answer.startswith("VERSION "):
            return answer.strip()
        time.sleep(0.2)
    process.kill()
    raise SystemExit(
        f"memcached never answered on {port}\n{log.read_text(encoding='utf-8', errors='replace')}"
    )


def smoke(tree: Path, version: str, provides: dict[str, str]) -> dict:
    """Run the artifact from somewhere it has never been, and make it be a *cache* while there.

    The four things a service is packed for, in the order T35 will do them: it starts with the flags
    ``core::generate`` will render — memcached takes no configuration file, so its command line *is*
    its configuration — it answers ``version`` as the ``ReadyCheck``, it stores and returns a value,
    and it is stopped the way the supervisor will stop it. That last one is the difference from
    every other service here: there is no ``shutdown`` verb to send, on purpose, so ``terminate``
    and a bounded wait is not a shortcut in the check but the mechanism itself.
    """
    elsewhere = borrow.moved(tree)

    problems = relocate.verify(elsewhere)
    for problem in problems:
        print(f"error: {problem}", file=sys.stderr)
    if problems:
        raise SystemExit("the relocated tree reaches outside itself")

    memcached = elsewhere / provides["memcached"]
    path = borrow.clean_path(memcached.parent)

    banner = borrow.run(memcached, "--version", path=path)
    if banner.split()[:2] != ["memcached", version]:
        raise SystemExit(f"memcached reports {banner!r}, expected 'memcached {version}'")
    print(f"memcached: {banner}")

    port = free_port()
    work = elsewhere.parent / "instance"
    work.mkdir(parents=True, exist_ok=True)
    log = work / "memcached.log"
    environment = {**os.environ, "PATH": path}
    with log.open("wb") as sink:
        process = subprocess.Popen(
            # 64 MB and loopback are MixEngine's own defaults for this service; `-U 0` turns the UDP
            # listener off, which upstream also defaults to and which a development machine has no
            # use for at all.
            [str(memcached), "-l", "127.0.0.1", "-p", str(port), "-U", "0", "-m", "64"],
            stdout=sink, stderr=subprocess.STDOUT, env=environment, cwd=str(work),
        )

    try:
        answered = await_version(port, process, log)
        if answered != f"VERSION {version}":
            raise SystemExit(f"the cache on {port} answered {answered!r}; this archive is {version}")
        print(f"memcached version: {answered} on {port}")

        expected = f"mixengine {version}"
        stored = talk(port, f"set mixengine:smoke 0 0 {len(expected)}\r\n{expected}\r\n")
        if stored.strip() != "STORED":
            raise SystemExit(f"SET answered {stored!r}, expected STORED")
        got = talk(port, "get mixengine:smoke\r\n")
        if expected not in got:
            raise SystemExit(f"GET answered {got!r}, expected a value of {expected!r}")
        print(f"memcached set/get: {expected}")

        # There is no `shutdown` to send — see the docstring — so this is the supervisor's own stop
        # and the check is that it is enough.
        process.terminate()
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            process.kill()
            raise SystemExit("memcached ignored a terminate and had to be killed") from None
        print("memcached stopped on terminate")
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=30)

    borrow.discard(elsewhere)
    return {
        "relocated": True,
        "ran": [
            "bin/memcached --version",
            "memcached -l 127.0.0.1 -p <port> -U 0 -m 64",
            "version over the text protocol, checked against this archive's version",
            "set/get over the text protocol",
            "terminate, which is how MixEngine stops this service",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--version", required=True,
        help="exact version (1.6.45), a line (1 or 1.6) for its newest release, or 'latest'",
    )
    parser.add_argument("--out", default=Path("dist"), type=Path)
    arguments = parser.parse_args()

    operating_system, arch = borrow.host("memcached")
    if operating_system == "windows":
        borrow.unavailable(
            "memcached has no Windows build: autotools and POSIX, no CMakeLists.txt, no win32 "
            "directory, and a privilege-dropping source file for every Unix and none for Windows. "
            "Its download page offers source and distribution packages and has never offered a "
            "Windows binary. This cell is empty and the index says so."
        )

    work = Path(tempfile.mkdtemp(prefix="mixengine-memcached-"))

    # The version is resolved and fetched *before* libevent is compiled, so that a spec naming a
    # release upstream does not have costs a request rather than a build.
    version, source_tree, digest, url = source(arguments.version, work)
    print(f"building memcached {version} for {operating_system}/{arch}")

    # One prefix for both installs. libevent is only ever an input — memcached links its static
    # archive — and `assemble` copies out the single file that ships rather than deleting the rest.
    prefix = work / "prefix"
    libevent_source = build_libevent(work, prefix)

    asked = build(source_tree, prefix, libevent_prefix=prefix)
    tree, provides = assemble(prefix, work)
    licences(tree, source_tree, libevent_source)

    manifest = {
        "schema": 1,
        "kind": "memcached",
        "version": version,
        "os": operating_system,
        "arch": arch,
        "source": "built",
        "recipe": (
            f"memcached-{version}.tar.gz from source (sha1 {digest[:12]}…, as published in "
            f"{url.rsplit('/', 1)[-1]}.sha1); {'; '.join(asked)}; no TLS, no SASL, no proxy, "
            f"no shutdown command"
        ),
        "provides": provides,
    }
    measured = relocate.floor(tree)
    if measured:
        manifest["requires"] = {measured[0]: measured[1]}
        print(f"needs {measured[0]} {measured[1]} or newer")

    manifest["smoke"] = smoke(tree, version, provides)
    print(f"built from {url}")

    borrow.publish(tree, manifest, arguments.out, "tar")
    shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
