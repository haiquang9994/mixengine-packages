#!/usr/bin/env python3
"""Compile memcached from upstream source for the five cells that can run it, and say so about one.

**The reason this recipe used to give for the Windows cells was wrong, and it was measured wrong
rather than argued wrong.** What it said was that memcached has "a privilege-dropping source file
for every Unix and none for Windows" — which describes the source tree accurately and then draws a
conclusion the source tree does not support. Every ``*_priv.c`` sits behind an ``AM_CONDITIONAL``
(``Makefile.am:39-57``, ``configure.ac:841-845``) and is **off by default**: optional hardening
reached through ``--enable-seccomp`` or a platform probe, not a component the program needs. Built
under Cygwin the generated ``config.h`` carries ``/* #undef HAVE_DROP_PRIVILEGES */`` and the build
completes anyway. ``eventfd``, ``accept4`` and ``mlockall`` are each an ``AC_CHECK_FUNCS`` with a
fallback and are undefined there too, with the same result.

So the true statement is **no native Win32 build system** — no ``CMakeLists.txt``, no ``win32/``,
no project file — which is a statement about what upstream ships, not about what the program can
be. Under Cygwin it builds on this recipe's own ``configure`` line with no flag added and no file
in either tarball patched, and the result runs as an ordinary Windows process: started from a
directory the build never named, on a trimmed ``PATH``, answering the text protocol and stopping on
a terminate.

*What that cell costs, said here rather than found in a tree.* A Cygwin binary links
``cygwin1.dll``, so the Windows archive is **two files rather than one** and the second is
**LGPLv3** — a licence text to ship and a source route to offer, both of which :func:`licences`
does, and a "this imports nothing outside the C runtime" claim that holds for the four Unix cells
and not for this one.

*And ``windows``/``aarch64`` stays empty*, for the reason ``nginx.py`` states about its own: Cygwin
has no aarch64 port — the toolchain and runtime are not upstream and package porting has not begun
— so the only thing that could fill that cell is this x86_64 image under emulation, and publishing
it in an archive whose manifest says ``arch: aarch64`` would be a lie in the index.
``docs/building-from-source.md`` already refuses that for the whole repository: *nothing here
cross-compiles and nothing runs under emulation*. Whether MixEngine should install the x86_64
archive on a Windows ARM machine and say so is a question for the daemon, where the user can see
the answer.

What the five cells get:

*A static libevent, pinned here rather than taken from the machine.* memcached is a thin layer over
an event loop and links nothing else. Taking the runner's libevent would make each artifact carry
whatever that image happened to have — the thing this repository levels out everywhere else — and
would leave a shared object to bundle and re-point afterwards. Compiled static into the binary
instead, the Unix artifact is one file that imports nothing outside the C runtime, and
``relocate.verify`` is what says so rather than this paragraph. The version is written down with its
SHA-256 and checked, which is one better than :mod:`ruby_unix` does for the three libraries it pins,
and it costs three lines.

*On Windows nothing in :mod:`relocate` says anything, and that is worth knowing rather than
assuming.* ``relocate.kind`` decides what a file is from its first four bytes and answers ``None``
for a PE, so ``machine_files`` finds nothing in a Windows tree and ``verify`` inspects nothing —
the call does not fail, it *passes without looking*. What replaces it here is two checks that are
not a model of the loader: :func:`imports` reads the import table with ``cygcheck`` at pack time and
refuses anything that is neither in the Cygwin tree nor under ``%SystemRoot%``, and :func:`smoke`
then starts the binary with :func:`borrow.clean_path`, which is the loader's own verdict. Teaching
:mod:`relocate` to read PE would turn a no-op into a real check inside seven other recipes at once —
Caddy, Node, Python, nginx, MariaDB and PostgreSQL all call ``verify`` unconditionally on Windows —
so it is left alone here deliberately, and that is the whole reason ``cygcheck`` lives in this file.

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
import shlex
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

WINDOWS = sys.platform == "win32"

# Where the toolchain is on the one cell that needs one that is not the machine's own. The workflow
# installs Cygwin with `add-to-path: false` and every program here is reached by absolute path, for a
# reason worth stating: putting Cygwin on PATH makes `shell: bash` in a step resolve to *Cygwin's*
# bash, which hands the trailing CR of a CRLF step script to the command — answered with
# `SHELLOPTS: igncr`, which is an exported, readonly, self-updating variable, so `set -euo pipefail`
# in that step then writes `errexit:nounset` into the environment of everything it starts. An
# autoconf `configure` is safe under neither, and the spike that measured this cell died at
# `_as_can_reexec: unbound variable` before testing one thing. Kept off PATH, none of that starts.
#
# `or` rather than a default argument: the workflow sets this from the install action's own `root`
# output rather than pinning a directory, and a step that skipped the install passes an *empty*
# string rather than nothing at all — which `os.environ.get` would hand back as a valid path.
CYGWIN_ROOT = Path(os.environ.get("CYGWIN_ROOT") or r"C:\cygwin64")

# One binary, which is the whole of what memcached installs into `bin/`: `bin_PROGRAMS = memcached`.
LAYOUT = {"memcached": "bin/memcached.exe" if WINDOWS else "bin/memcached"}

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

# Where Cygwin publishes the source of what it ships. Named in the tree rather than only here,
# because LGPLv3 asks the recipient to be able to get it and a reader holding the archive has this
# file and not this recipe. Only the Windows cell ever reaches it.
CYGWIN_SOURCE = "https://cygwin.com/pub/cygwin/x86_64/release"


def cygwin(script: str, capture: bool = False, stdin: str | None = None,
           timeout: int = 3600) -> str:
    """Run *script* in Cygwin's own bash, reached by absolute path rather than through ``PATH``.

    A **login** shell, so that ``/etc/profile`` composes the POSIX ``PATH`` the toolchain expects —
    which is what makes ``configure``, ``make`` and ``cygcheck`` resolvable without Cygwin appearing
    on the ``PATH`` of whatever started this recipe. See :data:`CYGWIN_ROOT` for why that matters.

    ``SHELLOPTS`` is removed from the environment rather than merely left unset by the workflow. A
    recipe run by hand out of a Cygwin shell inherits it the same way a step would, and what it
    causes is an autoconf ``configure`` dying on ``_as_can_reexec: unbound variable`` — a message
    that names a line in a generated script and says nothing about the variable that caused it.
    """
    bash = CYGWIN_ROOT / "bin" / "bash.exe"
    if not bash.is_file():
        raise SystemExit(
            f"there is no Cygwin at {CYGWIN_ROOT}: {bash} does not exist. The Windows cell is built "
            f"with Cygwin's toolchain — set CYGWIN_ROOT if it is installed somewhere else."
        )
    environment = dict(os.environ)
    environment.pop("SHELLOPTS", None)
    result = subprocess.run(
        [str(bash), "-lc", script], timeout=timeout, env=environment, text=True,
        input=stdin, capture_output=capture or stdin is not None,
    )
    if result.returncode != 0:
        raise SystemExit(
            f"cygwin bash exited {result.returncode} running: {script}\n"
            f"{(result.stdout or '').strip()}\n{(result.stderr or '').strip()}"
        )
    return result.stdout or ""


def posix(path: Path) -> str:
    r"""*path* as Cygwin sees it: ``C:\x\y`` becomes ``/cygdrive/c/x/y``.

    Through ``cygpath`` rather than through string surgery, because the answer is the *mount table*
    and not a rule: ``/usr/bin`` is not ``<root>/usr/bin``, and a drive letter is not always
    ``/cygdrive``.
    """
    return cygwin(f"cygpath -u {shlex.quote(str(path))}", capture=True).strip()


def install(prefix: Path) -> str:
    """A prefix spelled the way the build system has to be told it, on this cell."""
    return posix(prefix) if WINDOWS else str(prefix)


def run(*command: str, cwd: Path | None = None, env: dict | None = None,
        timeout: int = 3600) -> None:
    print("$ " + " ".join(str(part) for part in command), flush=True)
    if WINDOWS:
        # Through Cygwin's bash rather than through `subprocess` directly, because `./configure` is
        # a shell script and `make` is Cygwin's own: both want the POSIX view of the directory they
        # are run in, which is what `cwd` becomes here rather than a `subprocess` argument.
        script = " ".join(shlex.quote(str(part)) for part in command)
        if cwd is not None:
            script = f"cd {shlex.quote(posix(cwd))} && {script}"
        cygwin(script, timeout=timeout)
        return
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
        "./configure", f"--prefix={install(prefix)}", "--disable-shared", "--enable-static",
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
    # Not one flag added for Cygwin, and not one file patched in either tarball: the spike that
    # measured this cell ran exactly this line and the build completed. If that ever stops being
    # true, the failure belongs in the log rather than pre-empted by a `#ifdef` of flags here.
    asked = [
        "./configure", f"--prefix={install(prefix)}",
        f"--with-libevent={install(libevent_prefix)}",
    ]
    run(*asked, cwd=source_tree, env=os.environ.copy())
    run("make", f"-j{jobs()}", cwd=source_tree)
    run("make", "install", cwd=source_tree)
    return [f"./configure --prefix=… --with-libevent=… (libevent {LIBEVENT['version']}, static)"]


# ---------------------------------------------------------------------------- the Windows cell ---
#
# What follows does for one cell what :mod:`relocate` does for the other four, and lives here rather
# than there for the reason the module docstring gives: `relocate.verify` is a no-op on Windows
# inside seven other recipes, and making it stop being one is a change to all seven.


def imports(binary: Path) -> list[tuple[Path, str]]:
    r"""Everything *binary* loads, transitively, as ``(the Windows path, the same path POSIX)``.

    ``cygcheck`` is Cygwin's ``ldd`` and walks the PE import table. It prints **Windows** paths —
    ``C:\cygwin64\bin\cygwin1.dll``, never ``/usr/bin/cygwin1.dll`` — which is the detail that cost
    the spike a run: a filter written as ``grep '^/'`` matched nothing, the tree was packed with no
    DLL in it at all, and it still passed on the runner that had Cygwin on ``PATH``.

    Both spellings are answered because both are needed: the Windows one is what :func:`shutil.copy2`
    takes, and the POSIX one is what decides whether a file is *inside the Cygwin tree* — which is
    the mount table's answer (``/usr/bin/…`` for inside, ``/cygdrive/…`` for out) rather than a
    string comparison's. An earlier attempt compared against ``cygpath -w /`` through ``awk -v``,
    which expands escape sequences in the value it is handed: ``C:\cygwin64`` arrived as
    ``C:cygwin64`` and matched nothing.
    """
    # `|| true` because `cygcheck` reports a DLL it cannot resolve by exiting non-zero as well as by
    # printing it, and an unresolvable import is a finding this should read rather than a reason to
    # stop reading. Nothing is swallowed: the two checks below are on the *content* of the answer.
    printed = cygwin(f"cygcheck {shlex.quote(posix(binary))} || true", capture=True)
    windows: list[str] = []
    for line in printed.splitlines():
        entry = line.strip()
        if re.match(r"^[A-Za-z]:\\", entry) and entry not in windows:
            windows.append(entry)
    if not windows:
        raise SystemExit(
            f"cygcheck named no import at all for {binary.name}, which cannot be true of a PE. Its "
            f"output was:\n{printed}"
        )

    converted = cygwin("cygpath -u -f -", capture=True, stdin="\n".join(windows) + "\n")
    posix_paths = [line.strip() for line in converted.splitlines() if line.strip()]
    if len(posix_paths) != len(windows):
        raise SystemExit(
            f"cygpath answered for {len(posix_paths)} path(s) and cygcheck named {len(windows)}; "
            f"the two lists cannot be paired up"
        )
    return list(zip((Path(entry) for entry in windows), posix_paths))


def bundle(binary: Path) -> list[tuple[Path, str]]:
    """Copy every DLL that came out of the Cygwin tree in beside *binary*.

    **Beside the ``.exe`` is the whole of the rewrite on Windows.** The PE loader searches the
    directory of the image being loaded first, so a DLL copied there needs no rpath, no install name
    and no re-signing — what ``$ORIGIN`` emulates on ELF is already the default, and there is nothing
    for :func:`relocate.rewrite` to have an opinion about.
    """
    carried = [(path, where) for path, where in imports(binary) if not where.startswith("/cygdrive/")]
    if not carried:
        # The failure the spike hid for a whole run, made loud here instead of three steps later: a
        # Cygwin binary links `cygwin1.dll` by definition, so an empty list is a parse that went
        # wrong and never a tree that needed nothing.
        raise SystemExit(
            f"cygcheck found nothing from the Cygwin tree in {binary.name}. A Cygwin binary links "
            f"cygwin1.dll by definition, so this is an import list that was not parsed."
        )
    for source_path, where in carried:
        destination = binary.parent / source_path.name
        shutil.copy2(source_path, destination)
        print(f"bundling {where} ({source_path.stat().st_size:,} bytes)")
    return carried


def strays(binary: Path) -> list[str]:
    """Anything *binary* loads that is neither this tree's nor the operating system's.

    Not a proof that the tree is closed — :func:`smoke` gets that from the loader itself, by starting
    the binary on a :func:`borrow.clean_path` that has no build environment on it, which is the only
    thing that would have caught the spike's first false pass. This catches the different hazard:
    a DLL picked up from *somewhere else on the build machine*, which would be copied nowhere and
    found nowhere.

    A match on the file name rather than on the path, because ``cygcheck`` resolves a name the way
    its own environment does and this tree is not on it — so the copy it names may be Cygwin's while
    the copy the loader will use is the one sitting beside the ``.exe``.
    """
    system = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    beside = {path.name.lower() for path in binary.parent.iterdir() if path.is_file()}
    problems = []
    for path, where in imports(binary):
        if path.name.lower() in beside:
            continue
        try:
            path.resolve().relative_to(system.resolve())
        except (ValueError, OSError):
            problems.append(f"{binary.name}: {where} is neither in the tree nor under {system}")
    return problems


def cygwin_licences(carried: list[tuple[Path, str]], into: Path) -> list[str]:
    """Ship the licence of every DLL bundled out of the Cygwin tree, and a route to its source.

    **``cygwin1.dll`` is LGPLv3, which is not the shape of any other licence in this archive.** It
    asks for the text, and it asks that whoever receives the binary can obtain the library's source
    and relink against a modified one. The second half answers itself here — the DLL is a separate
    file beside the ``.exe`` and can be replaced with another — so what is left is the text and a
    place to get the source, and both are written into the tree rather than promised in a README.

    A bundled file whose licence cannot be found is a failure and not a warning, which is the rule
    :func:`relocate.bundled_licences` already sets for every library bundled on the Unix cells.
    """
    doc, licences_dir = (
        Path(line.strip()) for line in
        cygwin("cygpath -w /usr/share/doc; cygpath -w /usr/share/licenses", capture=True).splitlines()
    )

    shipped, unlicensed, rows = [], [], []
    for source_path, where in carried:
        # `cygcheck -f` answers with the package a file was installed by, which is the only thing
        # that can be asked — a DLL carries no licence of its own and Cygwin files them per package.
        package = cygwin(f"cygcheck -f {shlex.quote(where)} || true", capture=True).strip().splitlines()
        package = package[0].strip() if package else ""
        if not package:
            raise SystemExit(f"cygcheck cannot say which package installed {where}")
        bare = re.sub(r"-\d[\w.]*-\d+$", "", package)
        rows.append(f"{source_path.name}\t{where}\t{package}")

        found = []
        for place in (doc / bare, doc / bare.capitalize(), doc / "Cygwin", licences_dir / bare):
            if not place.is_dir():
                continue
            for pattern in ("LICENSE*", "LICENCE*", "COPYING*", "COPYRIGHT*", "CYGWIN_LICENSE*"):
                found += [text for text in sorted(place.glob(pattern)) if text.is_file()]
        if not found:
            unlicensed.append(f"{source_path.name} (from {package}; looked in {doc / bare} and beside it)")
            continue
        for text in found:
            shutil.copy2(text, into / f"{bare}-{text.name}")
            shipped.append(f"{bare} {text.name}")

    if unlicensed:
        raise SystemExit(
            "no licence text was found for a bundled DLL, and this archive may not be redistributed "
            "without one: " + "; ".join(unlicensed)
        )

    (into / "CYGWIN-SOURCE.txt").write_text(
        "The Windows archive links the Cygwin runtime, which is LGPLv3. Nothing in it is patched: "
        "each DLL below is the file the named Cygwin package installs, copied beside the binary "
        "unmodified.\n\n"
        "file\tinstalled from\tpackage\n" + "\n".join(rows) + "\n\n"
        "The corresponding source for each package is published by Cygwin at\n"
        f"  {CYGWIN_SOURCE}/<package>/\n"
        "as a <package>-<version>-src.tar.xz beside the binary package, and can also be fetched "
        "with Cygwin's own setup program using --download --include-source. The runtime's history "
        "is at\n"
        "  https://sourceware.org/git/?p=newlib-cygwin.git\n",
        encoding="utf-8",
    )
    return shipped


def licences(tree: Path, source_tree: Path, libevent_source: Path,
             carried: list[tuple[Path, str]] = ()) -> None:
    """Ship the licence of memcached, of the library compiled into it, and of anything bundled.

    Each requires its text to travel with the binary, so this is a condition of redistributing the
    archive rather than tidiness — and libevent is the half a walk over the *tree* would miss
    entirely, because after a static link there is no file in the archive that came from it.

    *carried* is what :func:`bundle` copied in on the Windows cell and is empty everywhere else. It
    is the opposite half of the same problem: a file that **is** in the tree, under its own licence
    rather than memcached's, and the one licence here that asks for more than its text.
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

    if carried:
        shipped += cygwin_licences(list(carried), into)

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
    memcached = elsewhere / provides["memcached"]
    # Exactly what the shim composes: the tree's own directory, then the system's. Nothing of the
    # build environment is on it, which on Windows is not hygiene but *the* check — the spike passed
    # once with a tree holding no DLL at all, because Cygwin's own bin was on PATH and the loader
    # found cygwin1.dll there. `--version` below is where that comes out, as 0xC0000135.
    path = borrow.clean_path(memcached.parent)

    problems = strays(memcached) if WINDOWS else relocate.verify(elsewhere)
    for problem in problems:
        print(f"error: {problem}", file=sys.stderr)
    if problems:
        raise SystemExit("the relocated tree reaches outside itself")

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
    if operating_system == "windows" and arch != "x86_64":
        borrow.unavailable(
            "Cygwin has no aarch64 port, and that is the whole of the reason this cell is empty. "
            "The toolchain and the runtime are not upstream — aarch64-pc-cygwin is waiting on GCC "
            "— and porting the packages has not begun, so nothing can build memcached natively for "
            "Windows on ARM today. The x86_64 archive does run here under emulation, and was "
            "measured doing it, but publishing that payload in an archive whose manifest says "
            "arch: aarch64 would be a lie in the index — the same refusal nginx.py makes about its "
            "own 32-bit payload, and the rule docs/building-from-source.md sets for the whole "
            "repository: nothing here cross-compiles and nothing runs under emulation. Whether to "
            "install the x86_64 archive on a Windows ARM machine is the daemon's decision to make "
            "in front of the user. This cell is empty and the index says so."
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

    # The Windows cell is the only one where anything outside the payload has to travel with it, and
    # the difference from the Unix cells is not the bundling but *what is left over*: after a static
    # libevent those archives are one file, and this one is two.
    carried: list[tuple[Path, str]] = []
    if WINDOWS:
        carried = bundle(tree / provides["memcached"])
        asked.append(
            "built with Cygwin's toolchain; "
            + ", ".join(sorted(path.name for path, _ in carried))
            + " copied in beside the binary (LGPLv3 — see licenses/CYGWIN-SOURCE.txt)"
        )

    licences(tree, source_tree, libevent_source, carried)

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
