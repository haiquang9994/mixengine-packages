#!/usr/bin/env python3
"""What every borrowed runtime does identically, in one place so three recipes cannot drift.

A borrowed runtime is a download, a hash check, a repack and a proof — and the first three of those
are the same work whether the publisher is nodejs.org, python-build-standalone or RubyInstaller. The
fourth is not, and deliberately stays in each recipe: what it means for a Node to be usable and what
it means for a Ruby to be usable are different claims, and the whole of T27a's sharpest finding is
that *a check two producers implement separately will drift, and the drift is invisible exactly
because they agree on the field name*. So the mechanics are shared and the claims are not.

Nothing here knows what a runtime is. It knows about archives, hashes, targets and a ``PATH``.

Python 3 stdlib only, by policy: this runs on a GitHub runner with nothing installed.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

# What a recipe exits with when the answer is "upstream builds nothing here", as opposed to
# "something went wrong". A matrix leg asking for Node 18 on Windows-on-ARM, or Ruby 3.3 on the same,
# is not a failed run: it is an empty cell of the table, and upstream decided it years ago. Each
# workflow reads this code and skips the upload rather than failing the job — which matters because
# a failed leg would stop the release of the targets that did produce something.
UNAVAILABLE = 75

# bsdtar rather than whatever ``tar`` resolves to. It ships with Windows itself, reads 7-Zip archives
# — which is how the Ruby recipe unpacks RubyInstaller without depending on 7-Zip being installed —
# and it is reached by absolute path because a runner with Git in its ``PATH`` may well answer `tar`
# with a GNU tar, which cannot read a 7z at all and says so only after the download.
WINDOWS_TAR = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "tar.exe"


def fetch(url: str, timeout: int = 120) -> bytes:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return response.read()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def parts(version: str) -> tuple[int, ...]:
    """``"3.12.14"`` as something that compares numerically rather than lexically."""
    return tuple(int(piece) for piece in version.split("."))


def host(runtime: str) -> tuple[str, str]:
    """Which cell of the table this machine is, refusing anything it cannot smoke-test.

    Cross-packing would be easy for a borrowed runtime — the payload is a download and a repack —
    and it is refused for the same reason the PHP recipes refuse it: an artifact nobody ran is an
    artifact nobody knows about. The runner *is* the proof.
    """
    system = {"win32": "windows", "darwin": "macos", "linux": "linux"}.get(sys.platform)
    if system is None:
        raise SystemExit(f"no {runtime} target for {sys.platform}")

    machine = platform.machine().lower()
    arch = {
        "amd64": "x86_64", "x86_64": "x86_64",
        "arm64": "aarch64", "aarch64": "aarch64",
    }.get(machine)
    if arch is None:
        raise SystemExit(f"no {runtime} target for {machine}")
    return system, arch


def unavailable(reason: str) -> None:
    """End the run as an empty cell rather than as a failure, saying which cell and why."""
    print(reason, file=sys.stderr)
    raise SystemExit(UNAVAILABLE)


def unpack(archive: Path, into: Path, suffix: str) -> Path:
    """Extract, and answer with the directory the payload is actually in.

    **Every publisher in this table wraps its release in one directory** — ``node-v22.23.2-linux-x64``,
    ``python``, ``rubyinstaller-3.4.10-1-x64`` — and every one of them has to go. MixEngine unpacks an
    archive straight into ``runtimes/<kind>/<version>/`` and every path in ``provides`` is relative to
    that, so a preserved wrapper would install a runtime one directory below where the index says it
    is. It is stripped here rather than by the daemon, which stays ignorant of who packed what.
    """
    into.mkdir(parents=True, exist_ok=True)
    if suffix == "zip":
        with zipfile.ZipFile(archive) as zipped:
            zipped.extractall(into)
    elif suffix == "7z":
        # Only bsdtar can read this, and only on Windows is it the tar that answers — which is fine,
        # because the only 7z in this table is a Windows-only publisher's.
        subprocess.run(
            [str(WINDOWS_TAR), "-xf", str(archive), "-C", str(into)],
            check=True, capture_output=True, text=True, timeout=1800,
        )
    else:
        with tarfile.open(archive) as tarred:
            # Symlinks are the point on Unix — `bin/python3` is one — so nothing here flattens them,
            # and `data` filters the members that are neither file, directory nor link.
            tarred.extractall(into, filter="data")

    entries = [path for path in into.iterdir() if path.is_dir()]
    if len(entries) != 1:
        raise SystemExit(
            f"expected one directory inside the archive, found "
            f"{[path.name for path in into.iterdir()]}"
        )
    return entries[0]


def moved(tree: Path) -> Path:
    """A copy of *tree* in a directory it has never seen, whose name contains a space.

    The space is not decoration. Windows generates an 8.3 short name behind every long one, a
    published PHP artifact once loaded no extensions at all because of a `~` in one, and a recipe
    that only ever ran from paths without spaces would find that out on a user's machine.
    """
    destination = Path(tempfile.mkdtemp(prefix="mixengine-smoke-")) / "moved here" / tree.name
    destination.parent.mkdir(parents=True)
    shutil.copytree(tree, destination, symlinks=True)
    return destination


def discard(copy: Path) -> None:
    """Remove what :func:`moved` made, from the temporary root it made it in."""
    shutil.rmtree(copy.parent.parent, ignore_errors=True)


def clean_path(*directories: Path) -> str:
    """Exactly what the shim composes, cut down to the system directories.

    The environment is the argument that matters in every one of these smoke tests. ``bin/pip`` is a
    script that finds its interpreter relative to itself, ``bin/gem`` is another, ``npm`` is a third
    — and every runner in this matrix has a Node.js, a Python and a Ruby of its own installed. A
    check that inherited the runner's ``PATH`` could pass by running the runner's interpreter against
    the archive's script, which is the one result that means nothing at all.

    ``System32`` is on it because every ``.cmd`` and ``.bat`` in a ``provides`` map is started
    through the ``cmd.exe`` that lives there.
    """
    system = (
        [os.environ.get("SystemRoot", r"C:\Windows") + suffix for suffix in (r"\system32", "")]
        if sys.platform == "win32"
        else ["/usr/bin", "/bin"]
    )
    return os.pathsep.join([*(str(directory) for directory in directories), *system])


def run(program: Path, *args: str, path: str, drop: tuple[str, ...] = (), timeout: int = 300) -> str:
    """Run *program* with a ``PATH`` of exactly what the shim would give it, and return its output.

    *drop* names environment prefixes to remove — ``PYTHON``, ``RUBY``, ``GEM`` — because a runner
    that has set up its own interpreter has usually pointed one of those at its own library
    directory, and a borrowed runtime that only works because ``PYTHONHOME`` happens to be unset is
    a runtime that breaks on the first machine where it is not.
    """
    environment = {
        key: value for key, value in os.environ.items()
        if not any(key.startswith(prefix) for prefix in drop)
    }
    environment["PATH"] = path
    result = subprocess.run(
        [str(program), *args], capture_output=True, text=True, timeout=timeout, env=environment
    )
    if result.returncode != 0:
        raise SystemExit(
            f"{program.name} {' '.join(args)} exited {result.returncode}\n"
            f"{result.stdout.strip()}\n{result.stderr.strip()}"
        )
    return result.stdout.strip()


def pack(tree: Path, out: Path, name: str, suffix: str) -> Path:
    """Write the archive, in the format that can carry what this tree contains.

    Windows gets a zip because that is what everything on Windows can open and there are no symlinks
    or permission bits to lose. Unix gets a tar, through the system ``tar`` rather than ``tarfile``,
    because ``bin/python3`` is a symlink and ``bin/python3.12`` has to stay executable — and zstd
    where it exists, falling back to gzip on a machine whose tar was built without it.
    """
    out.mkdir(parents=True, exist_ok=True)
    if suffix == "zip":
        packed = out / f"{name}.zip"
        with zipfile.ZipFile(packed, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for path in sorted(tree.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(tree))
        return packed

    packed = out / f"{name}.tar.zst"
    try:
        subprocess.run(
            ["tar", "--zstd", "-cf", str(packed), "-C", str(tree), "."],
            check=True, capture_output=True, text=True, timeout=1800,
        )
    except (subprocess.CalledProcessError, OSError):
        # A tar that died half way leaves a truncated archive behind, and `dist/` is uploaded
        # wholesale — so it is removed rather than left for something downstream to find.
        packed.unlink(missing_ok=True)
        packed = out / f"{name}.tar.gz"
        subprocess.run(
            ["tar", "-czf", str(packed), "-C", str(tree), "."],
            check=True, capture_output=True, text=True, timeout=1800,
        )
    return packed


def publish(tree: Path, manifest: dict, out: Path, suffix: str) -> Path:
    """Write the manifest into the tree, pack it, and write the manifest beside the archive too.

    Twice on purpose. The copy *inside* travels with the runtime, so a machine that has an artifact
    installed can still say what was proven about it; the copy *beside* is what ``mkindex.py`` reads,
    and what the release has to still hold in a year for the index to be rebuildable.
    """
    text = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    (tree / "mixengine-artifact.json").write_text(text, encoding="utf-8")

    name = f"{manifest['kind']}-{manifest['version']}-{manifest['os']}-{manifest['arch']}"
    packed = pack(tree, out, name, suffix)
    (out / f"{packed.name}.json").write_text(text, encoding="utf-8")

    print(f"packed {packed} ({packed.stat().st_size:,} bytes)")
    print(f"sha256 {sha256(packed)}")
    print(
        f"{manifest['kind']} {manifest['version']} on {manifest['os']}/{manifest['arch']}: "
        f"{', '.join(sorted(manifest['provides']))}"
    )
    return packed
