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
import time
import urllib.error
import urllib.request
import zipfile
from collections.abc import Iterable, Mapping
from pathlib import Path

# What a recipe exits with when the answer is "upstream builds nothing here", as opposed to
# "something went wrong". A matrix leg asking for Node 18 on Windows-on-ARM, or Ruby 3.3 on the same,
# is not a failed run: it is an empty cell of the table, and upstream decided it years ago. Each
# workflow reads this code and skips the upload rather than failing the job — which matters because
# a failed leg would stop the release of the targets that did produce something.
UNAVAILABLE = 75

# bsdtar rather than whatever ``tar`` resolves to. It ships with Windows itself and is reached by
# absolute path because a runner with Git in its ``PATH`` may well answer `tar` with a GNU tar, which
# cannot read a 7z at all and says so only after the download.
WINDOWS_TAR = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "tar.exe"

# Where 7-Zip is when it is installed at all. GitHub's Windows runners carry it; a developer's
# machine may not, which is why it is tried rather than required — see `unpack`.
SEVEN_ZIP = Path(r"C:\Program Files\7-Zip\7z.exe")


def fetch(url: str, timeout: int = 120, attempts: int = 3) -> bytes:
    """Download *url*, trying again when the network rather than the server refuses.

    A retry here is not defensive programming for its own sake: the source builds fetch five
    archives before they compile anything, one of them a 53 MB OpenSSL, and a run that gets through
    that and dies twenty minutes later has thrown away a whole build over a dropped connection. It
    happened on the release build of Ruby 3.2 — `[Errno 60] Operation timed out` on one leg of four,
    on a version that had already gone green twice.

    **An HTTP status is an answer and is not retried.** A 404 means "upstream does not publish
    this", which is a fact three attempts will not change, and each recipe has something to say
    about it that this cannot.
    """
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError:
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as error:
            if attempt == attempts:
                raise
            print(f"{url}: {error} (attempt {attempt} of {attempts})", file=sys.stderr)
            time.sleep(5 * attempt)
    raise AssertionError("unreachable")


def _digest(path: Path, algorithm: str) -> str:
    digest = hashlib.new(algorithm)
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256(path: Path) -> str:
    return _digest(path, "sha256")


def sha512(path: Path) -> str:
    """What a publisher states, where the publisher states that one — Caddy's checksums file does.

    The manifest still carries a SHA-256 of the same bytes, because that is the field every artifact
    here has and the one the index is built on. Which algorithm a download was *checked* with is the
    publisher's choice and is recorded in ``upstream.verified_against`` rather than being smoothed
    over into a hash nobody published.
    """
    return _digest(path, "sha512")


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


def seven_zip(archive: Path, into: Path) -> None:
    """Extract a 7-Zip archive with whichever of two readers on this machine can actually do it.

    Reading the container is not the hard part; decoding it is. bsdtar has understood the 7-Zip
    format for years, but only decompresses LZMA when libarchive was built with liblzma — and the
    `tar.exe` in Windows Server 2022 was not, while the one in Windows 11 was. So a recipe that
    called bsdtar and nothing else worked on this machine, worked on the `windows-11-arm` runner,
    and failed on `windows-2022` with a bare exit code after a 20 MB download.

    Both are therefore tried, 7-Zip first because it is the format's own reader, and what is raised
    when neither works quotes *both* refusals. Neither is required to be present: GitHub's runners
    have 7-Zip and a developer's machine usually does not, and each falls back to the other.
    """
    attempts: list[tuple[list[str], str]] = []
    installed = str(SEVEN_ZIP) if SEVEN_ZIP.exists() else shutil.which("7z")
    if installed:
        attempts.append(([installed, "x", "-y", f"-o{into}", str(archive)], "7-Zip"))
    if WINDOWS_TAR.exists():
        attempts.append(([str(WINDOWS_TAR), "-xf", str(archive), "-C", str(into)], "bsdtar"))

    refusals = []
    for command, name in attempts:
        result = subprocess.run(command, capture_output=True, text=True, timeout=1800)
        if result.returncode == 0:
            return
        said = (result.stderr or result.stdout).strip().splitlines()
        refusals.append(f"  {name} exited {result.returncode}: {said[-1] if said else 'no output'}")
    raise SystemExit(
        "nothing on this machine could extract a 7-Zip archive.\n" + "\n".join(refusals)
    )


def unpack(archive: Path, into: Path, suffix: str, wrapped: bool = True) -> Path:
    """Extract, and answer with the directory the payload is actually in.

    **Every publisher of a *runtime* here wraps its release in one directory** —
    ``node-v22.23.2-linux-x64``, ``python``, ``rubyinstaller-3.4.10-1-x64`` — and every one of them
    has to go. MixEngine unpacks an archive straight into ``runtimes/<kind>/<version>/`` and every
    path in ``provides`` is relative to that, so a preserved wrapper would install a runtime one
    directory below where the index says it is. It is stripped here rather than by the daemon, which
    stays ignorant of who packed what.

    *wrapped* is that assumption, made an argument rather than a fact, because the first service
    this repository packs does not hold it: Caddy's release is a single Go binary and its archive
    puts ``caddy``, ``LICENSE`` and ``README.md`` at the root. The default stays the strict check —
    an unwrapped payload arriving where a wrapper was expected means the publisher changed the shape
    of its release, and that is worth failing over rather than packing whatever was found.
    """
    into.mkdir(parents=True, exist_ok=True)
    if suffix == "zip":
        with zipfile.ZipFile(archive) as zipped:
            zipped.extractall(into)
    elif suffix == "7z":
        seven_zip(archive, into)
    else:
        with tarfile.open(archive) as tarred:
            # Symlinks are the point on Unix — `bin/python3` is one — so nothing here flattens them,
            # and `data` filters the members that are neither file, directory nor link.
            tarred.extractall(into, filter="data")

    if not wrapped:
        # Still a check rather than a shrug: an archive that turns out to wrap its payload after all
        # would otherwise be packed as a tree whose only entry is a directory, and every path in
        # `provides` would be wrong by one level in a way nothing before installation would notice.
        directories = [path.name for path in into.iterdir() if path.is_dir()]
        if len(list(into.iterdir())) == 1 and directories:
            raise SystemExit(
                f"this archive was expected to hold its payload at the root and instead wraps it "
                f"in {directories[0]}/"
            )
        return into

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


def declare(
    tree: Path, manifest: dict, added: Iterable[str] = (), removed: Iterable[str] = (),
    keeps: Mapping[str, str] | None = None, changed: Mapping[str, str] | None = None,
) -> dict:
    """Write what this repository put into a borrowed archive and took out of it, having checked it.

    ``upstream.added`` and ``upstream.removed`` are what keep the word *borrowed* checkable: a reader
    holding this artifact and the publisher's own archive should find every difference between them
    named here, rather than deducing it from two hashes that do not match. A recipe that quietly
    deletes something is a recipe whose artifact cannot be told apart from a corrupted download.

    **Checked rather than trusted, because the fields are a claim and a claim can be stale.** Every
    path in *added* has to exist in the tree, every path in *removed* has to be gone from it, and a
    recipe that says otherwise fails the pack. That is not hypothetical carefulness: MariaDB shipped
    a pattern excluding ``mysql_ldb`` for four rounds while the file stayed in every artifact,
    because deleting its target first had made the symlink invisible to ``Path.exists``. A
    declaration nothing verifies decays into a comment.

    Paths are POSIX-relative to the root of the tree, sorted and de-duplicated here so that six cells
    of one version can be compared field to field rather than read side by side.

    *keeps* is the third claim and the only one that is **not** about the publisher, which is why it
    is written at the top level of the manifest rather than under ``upstream``: nothing was borrowed,
    added or removed, and the difference being declared is from *this repository's own rule*. The
    second half of that rule throws out headers, import libraries, manual pages and test suites, and
    a recipe that keeps one of them anyway has to say which and why — a mapping rather than a list,
    because a check that reads a bare path can only report "declared", and the whole point is that
    the reason travels with the artifact instead of living in a commit message. CPython is the first
    row that needs it: ``pip install`` of a source distribution compiles a C extension on the user's
    machine, and it compiles it against ``include/`` and links it against ``libs/python3XX.lib``.

    *changed* is the fourth kind of difference and the one this function was written without, because
    for three tasks there was no such thing: a recipe added files, deleted files, or left them alone.
    CPython's symbol tables are the first case of a file that ships **and is not the file upstream
    published** — same path, same purpose, different bytes — and that is exactly the difference a
    reader comparing the two archives is least able to explain, since it looks like corruption and
    nothing else here would name it. The value is the command that made it, not an argument for it:
    somebody holding both archives wants to know what was done to the file, and the reasoning lives
    where all the other reasoning lives.
    """
    declared = manifest.setdefault("upstream", {})

    if added:
        added = sorted(dict.fromkeys(added))
        absent = [path for path in added if not (tree / path).exists()]
        if absent:
            raise SystemExit(
                f"upstream.added names {', '.join(absent)}, which this tree does not contain — "
                f"either the recipe stopped writing them or the declaration was never true"
            )
        declared["added"] = added

    if removed:
        removed = sorted(dict.fromkeys(removed))
        # `lexists` rather than `exists`: a dangling symlink is still a file in the archive, and
        # `exists` follows the link and answers no. That is the mysql_ldb bug, in one call.
        survivors = [path for path in removed if os.path.lexists(tree / path)]
        if survivors:
            raise SystemExit(
                f"upstream.removed names {', '.join(survivors)}, which are still in the tree — "
                f"the removal did not happen, or it happened somewhere this does not see"
            )
        declared["removed"] = removed

    if changed:
        # Checked like `added`, and the check is weaker than the other three by nature: this can
        # prove the path is still there, not that the modification named is the one that happened.
        # What proves that is the caller — `python.strip_symbols` compares the loader's and the
        # linker's whole view of the file across the operation and refuses to return if any of it
        # moved — and saying so here is better than implying a check that is not being made.
        absent = [path for path in sorted(changed) if not (tree / path).exists()]
        if absent:
            raise SystemExit(
                f"upstream.changed names {', '.join(absent)}, which this tree does not contain — a "
                f"modification is being claimed for a file that is not shipping"
            )
        declared["changed"] = dict(sorted(changed.items()))

    if keeps:
        # Checked the same way `added` is, and for the same reason: a path kept on purpose that is
        # no longer there is an exemption still being claimed by a recipe that stopped needing it,
        # which is precisely the shape of declaration this function exists to refuse.
        absent = [path for path in sorted(keeps) if not (tree / path).exists()]
        if absent:
            raise SystemExit(
                f"keeps names {', '.join(absent)}, which this tree does not contain — the "
                f"exemption outlived whatever it was written for"
            )
        manifest["keeps"] = dict(sorted(keeps.items()))

    return manifest


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
    except (subprocess.CalledProcessError, OSError) as refusal:
        # **A fallback that succeeds is the hardest kind of difference to notice.** This one was
        # noticed by reading four release logs side by side and finding one version published as
        # `.tar.zst` on macOS and `.tar.gz` on Linux, for a reason nothing had printed — so it
        # prints it now. Both suffixes are named in the index and either installs; what is not
        # acceptable is a build machine quietly deciding which.
        said = getattr(refusal, "stderr", "") or str(refusal)
        print(f"tar --zstd refused ({said.strip().splitlines()[-1] if said.strip() else refusal}); "
              f"packing {name} with gzip instead", file=sys.stderr)
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
