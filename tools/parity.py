#!/usr/bin/env python3
"""Compare the artifacts of one version to each other, and each of them to the rule.

Every other check in this repository reads one artifact. A smoke test proves an archive runs;
`borrow.declare` proves a recipe's claims against the tree that made them; `verify.py` proves the
index is well formed and points at our own mirror. Every one of them passes on an archive that is
perfectly self-consistent and does something none of its five siblings do — which is the whole of
what [*One version means one thing, and no more than is needed*][rule] forbids. A rule about a set
of artifacts cannot be enforced by anything that looks at one of them.

**Both halves of it were found by hand before they were written down here.** The first audit of a
green MariaDB run — six cells, every one proven against a running server — found four asymmetries no
recipe knew it had. P2 then packed five branches of PHP without GD, because a keep-list matched
`php_gd2.dll` against a name PHP changed in 8.0, and every check passed: what is missing cannot fail
a load test. Twice, and both times by comparing rather than by reading.

So there are two checks, and they read different things.

*Across the cells of one version*, the feature set has to match. What a feature set **is** differs
by kind, and only there: for PHP it is ``extensions.static ∪ extensions.enabled`` — what a cell
actually runs with, as against ``shared``, which on Windows says the same word about `curl` and
about a debugger — and for every kind it is also the commands in ``provides``. A cell short of
something its siblings have is a defect unless it says otherwise.

*Within one artifact*, nothing may match what the second half of the rule throws out — debug
symbols, import and static libraries, headers, manual pages, documentation, test suites — unless the
artifact declares it, with the reason.

**The two exemptions are deliberately different kinds of thing.** ``lacks`` and ``keeps`` are
written by the recipe into the artifact, so the reason travels with the archive and a reader holding
it can see what was decided and why; that is what makes "no more than is needed" a list somebody
wrote down rather than a habit somebody remembers. `php_parity` is written *here* instead, because
what Windows has never built is a fact about the whole PHP row and not about one cell — and a fact
stated eleven times is a fact that will be true ten times.

What this cannot do is notice a feature missing from all six cells at once. That is
`php_parity.check` and the smoke tests, which know what a version owes; this knows only what its
siblings have.

Run where every artifact of a version is visible at once, which is ``publish-index.yml`` and nowhere
else. An empty cell is not a failure — a target upstream never built exits 75 in its own workflow —
so nothing here asks how many cells a row should have.

Python 3 stdlib only, and it has to keep running on the 3.12 the index workflow installs: no
``tarfile`` zstd, hence ``tar``.

[rule]: ../README.md#one-version-means-one-thing-and-no-more-than-is-needed
"""

from __future__ import annotations

import argparse
import collections
import json
import subprocess
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import php_parity  # noqa: E402  — siblings, and this directory is not importable as a package

ARCHIVE_SUFFIXES = (".zip", ".tar.zst", ".tar.gz")

# What the second half of the rule throws out, by file extension, wherever in the tree it sits.
# Extensions rather than directories because none of these has a home: `bin/server.pdb` and
# `lib/libruby-static.a` are the same claim about two trees, and MariaDB's Windows zip filed 74 MB
# of the first under `bin/` while its own installer puts them elsewhere.
SURPLUS_SUFFIXES = {
    ".pdb": "debug symbols, which every publisher here offers as a separate download",
    ".lib": "an import library — a linker input, and this installs a runtime rather than an SDK",
    ".a": "a static library, likewise",
}

# The same, by directory, and anchored at the root of the archive on purpose. A `test` directory
# deeper in is a library's own code — Ruby ships `lib/ruby/3.4.0/test/unit.rb` and it is part of the
# standard library — while a `test` at the root is a suite for testing the thing rather than for
# running it. `upstream.removed` names a directory by its root for the same reason.
SURPLUS_DIRECTORIES = {
    "include": "headers",
    "share/man": "manual pages",
    "share/doc": "documentation",
    "share/ri": "documentation, in Ruby's spelling",
    "test": "a test suite",
    "tests": "a test suite",
}

# And one that is neither: a `.dSYM` is a *bundle*, so it is a directory whose name carries the
# extension and whose contents do not. Matched on any component, since nothing else on macOS is
# spelled that way and a nested one is still 30 MB of DWARF.
DSYM = ".dSYM"


def contents(archive: Path) -> list[str]:
    """Every path inside *archive*, POSIX-relative to its root, sorted and de-duplicated.

    Read from the archive rather than from an unpacked copy because that is what a user downloads,
    and because unpacking the whole catalogue to answer a question about file names would cost more
    than the release it is checking.
    """
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as opened:
            names = opened.namelist()
    else:
        # `tar` rather than `tarfile`: the 3.12 this runs on cannot read zstd, and the archives this
        # repository writes are zstd wherever the packing machine's tar could manage it.
        flags = ["--zstd"] if archive.name.endswith(".tar.zst") else []
        done = subprocess.run(["tar", *flags, "-tf", str(archive)],
                              capture_output=True, text=True, timeout=900)
        if done.returncode != 0:
            raise SystemExit(f"cannot list {archive.name}: {done.stderr.strip()}")
        names = done.stdout.splitlines()

    found = set()
    for name in names:
        # A tar written with `-C tree .` names everything `./`, and a directory entry keeps its
        # trailing slash in both formats. Neither is part of the path being checked.
        stripped = name.strip().removeprefix("./").rstrip("/")
        if stripped:
            found.add(stripped)
    return sorted(found)


def surplus(path: str) -> tuple[str, str] | None:
    """``(what matched, why it is surplus)`` for a path the rule throws out, else ``None``."""
    for suffix, reason in SURPLUS_SUFFIXES.items():
        if path.endswith(suffix):
            return f"*{suffix}", reason
    if any(part.endswith(DSYM) for part in path.split("/")):
        return f"*{DSYM}", "a debug bundle, which is the same thing a .pdb is"
    for directory, reason in SURPLUS_DIRECTORIES.items():
        if path == directory or path.startswith(f"{directory}/"):
            return f"{directory}/", reason
    return None


def declared(path: str, keeps: dict) -> str | None:
    """Which ``keeps`` entry covers *path*, if any.

    A directory kept whole is named by its root — ``include`` rather than the 211 files under it —
    so an entry covers itself and everything below it. That is the same spelling `upstream.removed`
    uses, and it is what lets six cells be compared field to field.
    """
    for kept in keeps:
        if path == kept or path.startswith(f"{kept.rstrip('/')}/"):
            return kept
    return None


def within(name: str, manifest: dict, paths: list[str]) -> tuple[list[str], list[str]]:
    """Check one artifact against the second half of the rule. ``(problems, what was checked)``.

    Three things, and the second and third are not repetitions of `borrow.declare`. That runs
    against the tree a recipe is about to pack; this runs against the archive that came out, which
    is the only place a path lost between the two — a symlink `zipfile` skipped, a file a later step
    recreated — can be caught.
    """
    problems, checked = [], []
    keeps = manifest.get("keeps", {})
    removed = manifest.get("upstream", {}).get("removed", [])

    found = collections.defaultdict(list)
    for path in paths:
        matched = surplus(path)
        if matched:
            found[matched].append(path)

    for (matched, reason), carried in sorted(found.items()):
        undeclared = [path for path in carried if declared(path, keeps) is None]
        if undeclared:
            problems.append(
                f"{name} carries {len(carried)} path(s) matching {matched} — {reason} — of which "
                f"{len(undeclared)} {'is' if len(undeclared) == 1 else 'are'} declared nowhere: "
                f"{', '.join(undeclared[:4])}. Delete them, or name them in `keeps` with the "
                f"reason they are needed here."
            )
        else:
            checked.append(f"{name}: {len(carried)} path(s) matching {matched}, every one of them "
                           f"declared in `keeps`")
    if not found:
        # Said out loud, because an artifact that matched none of the patterns and an artifact
        # nobody compared against them produce the same silence otherwise.
        checked.append(f"{name}: nothing matching the rule's own list of surplus")

    inside = set(paths)
    absent = [path for path in sorted(keeps) if path not in inside
              and not any(other.startswith(f"{path.rstrip('/')}/") for other in inside)]
    if absent:
        problems.append(
            f"{name} declares {', '.join(absent)} in `keeps` and the archive does not contain "
            f"{'them' if len(absent) > 1 else 'it'} — an exemption outliving what it was for"
        )

    survivors = [path for path in sorted(removed) if path in inside
                 or any(other.startswith(f"{path.rstrip('/')}/") for other in inside)]
    if survivors:
        problems.append(
            f"{name} says it removed {', '.join(survivors)} and the archive still contains "
            f"{'them' if len(survivors) > 1 else 'it'} — the removal did not survive packing"
        )
    if removed and not survivors:
        checked.append(f"{name}: {len(removed)} path(s) in `upstream.removed`, none of them in the "
                       f"archive")
    return problems, checked


def offered(manifest: dict) -> dict[str, set[str]]:
    """The comparable sets this artifact offers, by what each one is called.

    ``provides`` for every kind, because a command that is on five cells and not the sixth is the
    cheapest asymmetry there is and nothing was looking for it. ``extensions`` on top of that
    wherever a manifest carries them, in the spelling `php_parity` uses, since PHP answers `PDO` to
    `get_loaded_extensions()` and `pdo` to everything else.
    """
    commands = set(manifest["provides"])
    if manifest["kind"] == "php" and commands & php_parity.SERVES:
        commands = (commands - php_parity.SERVES) | {" or ".join(sorted(php_parity.SERVES))}
    sets = {"provides": commands}

    extensions = manifest.get("extensions")
    if extensions is not None:
        sets["extensions"] = (php_parity.reported(extensions.get("static", []))
                              | php_parity.reported(extensions.get("enabled", [])))
    return sets


def across(kind: str, version: str, manifests: list[dict]) -> tuple[list[str], list[str]]:
    """Check the cells of one version against each other. ``(problems, what was checked)``.

    The comparison is a cell against the union of its siblings rather than against a list of what
    the version owes, which is the half `php_parity.check` cannot do: a recipe sees one cell and
    cannot know that the other five have something. It is also why an empty cell costs nothing here
    — a row of three is compared as a row of three.
    """
    problems, checked = [], []
    branch = None
    if kind == "php":
        numbers = version.split(".")
        branch = (int(numbers[0]), int(numbers[1]))

    cells = {(m["os"], m["arch"]): offered(m) for m in manifests}
    lacks = {(m["os"], m["arch"]): m.get("lacks", {}) for m in manifests}

    for what, noun in (("provides", "command"), ("extensions", "extension")):
        present = {where: sets[what] for where, sets in cells.items() if what in sets}
        if len(present) < 2:
            if present:
                only = next(iter(present.values()))
                checked.append(f"only one cell of this version is here, so its {len(only)} "
                               f"{what} were compared against nothing")
            continue
        union = set().union(*present.values())
        disagreed = 0
        for name in sorted(union):
            for where in sorted(where for where, have in present.items() if name not in have):
                operating_system = where[0]
                if name in lacks[where]:
                    continue
                if kind == "php" and php_parity.exempt(branch, name, operating_system):
                    continue
                disagreed += 1
                has = sorted(f"{o}/{a}" for (o, a) in present if name in present[(o, a)])
                problems.append(
                    f"{kind} {version}: {operating_system}/{where[1]} has no {noun} `{name}` and "
                    f"{', '.join(has)} do{'es' if len(has) == 1 else ''} — one version meaning two "
                    f"things. Close it in the recipe, or, if no recipe can, say so in `lacks`."
                )
        if not disagreed:
            checked.append(f"{len(present)} cell(s) agree on {len(union)} {what}")
    return problems, checked


def collect(directory: Path) -> list[tuple[dict, Path | None, str]]:
    """Every ``<archive>.json`` in *directory*, with the archive beside it where there is one."""
    found = []
    for manifest_path in sorted(directory.glob("*.json")):
        archive = manifest_path.with_suffix("")
        if not archive.name.endswith(ARCHIVE_SUFFIXES):
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        found.append((manifest, archive if archive.exists() else None, archive.name))
    return found


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", type=Path, default=Path("dist"),
                        help="directory of <archive> and <archive>.json pairs")
    parser.add_argument("--quiet", action="store_true",
                        help="print the problems and not what passed")
    args = parser.parse_args()

    found = collect(args.artifacts)
    if not found:
        raise SystemExit(f"no manifests in {args.artifacts} — there is nothing to compare, which "
                         f"is not the same thing as everything agreeing")

    rows = collections.defaultdict(list)
    for manifest, archive, name in found:
        rows[(manifest["kind"], manifest["version"])].append((manifest, archive, name))

    problems, unopened = [], []
    for (kind, version), cells in sorted(rows.items()):
        said, checked = across(kind, version, [manifest for manifest, _, _ in cells])
        problems += said
        for manifest, archive, name in cells:
            if archive is None:
                unopened.append(name)
                continue
            said, inside = within(name, manifest, contents(archive))
            problems += said
            checked += inside
        if not args.quiet:
            print(f"{kind} {version} ({len(cells)} cell(s))")
            for line in checked:
                print(f"  ok  {line}")

    for problem in problems:
        print(f"FAIL {problem}")
    # Said whether or not anything failed: an artifact nobody looked inside is a artifact whose
    # second half was not checked, and a report that does not say so reads as a clean one.
    if unopened:
        print(f"not looked inside: {len(unopened)} archive(s) absent from {args.artifacts} — "
              f"{', '.join(unopened[:4])}", file=sys.stderr)
    if problems:
        raise SystemExit(f"{len(problems)} problem(s)")
    print(f"ok: {len(rows)} version(s), {len(found)} artifact(s) compared")


if __name__ == "__main__":
    main()
