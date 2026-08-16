#!/usr/bin/env python3
"""Borrow a Ruby from RubyInstaller and repack it as a MixEngine artifact. **Windows only.**

MixEngine's runtime table said *"Ruby: we build"* in all three columns. One of the three was wrong,
and it is the one nobody expected to be: the Windows cell is the *easiest* borrow in the whole table.

``rubyinstaller-<version>-<arch>.7z`` is an archive that unpacks into a directory of its own and runs
from wherever it is put, because RubyInstaller configures Ruby with ``--enable-load-relative`` and
every path Ruby needs — its standard library, its gem home, and *its CA bundle* — is computed from
the executable's own location at run time. That last one matters more than it sounds: it is the
difference between a Ruby that can `gem install` on somebody else's machine and one that fails every
HTTPS handshake with an error that names nothing.

It also publishes **arm64**, from Ruby 3.4 onwards, which no other runtime in this table does on
Windows — there is no ARM64 PHP in any branch, and Node.js starts at 20.

What is *not* here is macOS and Linux, and that is a task rather than an omission
(**T27b**). The three relocatable Ruby distributions that exist for those systems were each
checked and each fails for a different reason, written up in
``.claude/operations/runtime-packaging.md`` — briefly: Homebrew's ``portable-ruby`` is relocatable by
construction and publishes exactly one version, which is not something a version manager can be
built on; ``ruby/ruby-builder``'s artifacts are what ``ruby/setup-ruby`` installs and its own README
says they "embed the install path when built and cannot be moved around"; and RVM's binaries are
prefix-bound, per distribution release, and the newest of them are years old.

Everything mechanical is in :mod:`borrow`, shared with the Node.js and Python recipes, and
everything this recipe *claims* about a Ruby is in :mod:`ruby_smoke`, shared with the one that
compiles the other two systems — a Ruby packed here and a Ruby compiled there are the same runtime
to a MixEngine daemon, so they must be the same claim as well.

Python 3 stdlib only, by policy: this runs on a GitHub runner with nothing installed.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import borrow  # noqa: E402  — siblings, and this directory is not importable as a package
import ruby_parity  # noqa: E402
import ruby_smoke  # noqa: E402

API = "https://api.github.com/repos/oneclick/rubyinstaller2/releases"

# What upstream calls each Windows architecture. `x86` — the 32-bit build, which is still published
# for the 3.x lines — has no row: MixEngine offers no 32-bit target for any runtime.
ARCHES = {"x86_64": "x64", "aarch64": "arm"}

# Where each command lives inside the tree, as candidates in the order they are preferred. `ruby.exe`
# is a real executable and the rest are batch files, which is not a special case: `std::process::Command`
# starts a `.bat` and a `.cmd` through `cmd.exe`, returns the batch file's own exit code and escapes
# its arguments — measured for `npm.cmd` in T27's Node half and pinned by a test there.
#
# **The extension is not the same in every line, which is why these are lists.** RubyInstaller ships
# `bin/bundle.bat` in the lines MixEngine offers and shipped `bin/bundle.cmd` in the 2.x ones; `gem`
# has stayed `.cmd` throughout. Both run identically, so hard-coding either would be a recipe that
# fails on a line for a difference that makes no difference.
#
# `bundler` has no row although `bin/bundler.bat` exists, and `python3` had none in the Python
# recipe's map for the same reason: those are *command* names, which `core::shims::COMMANDS` maps
# onto these *executable* names. An artifact publishing both spellings would be inviting the two to
# disagree about which file `bundler` runs.
LAYOUT = {
    "ruby": ["bin/ruby.exe"],
    "gem": ["bin/gem.cmd", "bin/gem.bat"],
    "bundle": ["bin/bundle.bat", "bin/bundle.cmd"],
    "rake": ["bin/rake.bat", "bin/rake.cmd"],
    "irb": ["bin/irb.bat", "bin/irb.cmd"],
}

REQUIRED = ("ruby", "gem", "bundle")


def prune(tree: Path) -> list[str]:
    """Throw out the documentation, which is most of this archive and none of this runtime.

    **Measured before it was decided, and the measurement is why this is not a footnote.** RDoc's
    HTML rendering of Ruby's own manual, plus the `ri` database beside it, is 60.3 MB of a 108 MB
    tree on 3.4.10 and **224.9 MB of a 276 MB tree on 4.0.6** — four fifths of an artifact of a
    programming language being that language's manual, growing four and a half times in one line
    while the language itself grew by 8%.

    The four compiled cells have never carried any of it: `ruby_unix.py` passes
    `--disable-install-doc` so it is never generated. That is what decides the direction rather than
    the size — a positive choice with an argument behind it on one side, and on the other an archive
    that carries the docs because RubyInstaller is a general-purpose distribution of Ruby. The list
    itself is :data:`ruby_parity.SURPLUS`, read by both recipes, because "say so beside the other
    recipe" is a comment and comments do not fail a build.

    `share/ri` is the one worth arguing about and was checked rather than assumed: unlike the
    terminfo database CPython's Unix cells were carrying, this one **is** reachable —
    `RDoc::RI::Paths.path` names it and `RDoc::RI::Driver` answers `String#upcase` out of it. What it
    is not is *reachable through anything this artifact publishes*. Neither recipe puts `ri` or
    `rdoc` in `provides`, and IRB's own `help` in 3.4 routes to its command table and not to RDoc. So
    it is a working feature of a Ruby installation that no MixEngine command reaches, on two cells
    out of six.
    """
    removed = []
    for surplus in ruby_parity.SURPLUS:
        path = tree / surplus
        # `lexists`, for the reason `borrow.declare` gives at length: the check that follows this is
        # about paths being gone, and a link whose target went first is invisible to `exists`.
        if os.path.lexists(path):
            removed.append(surplus)
            shutil.rmtree(path, ignore_errors=True)
    if removed:
        print(f"dropped {', '.join(removed)} (documentation; the compiled cells never build it)")
    return removed


def releases() -> list[dict]:
    """Every RubyInstaller release, newest page first.

    The GitHub API rather than a file, because unlike nodejs.org and python-build-standalone this
    publisher states its catalogue nowhere else — the tags *are* the index. ``GITHUB_TOKEN`` is used
    when the environment has one, which on a runner it does: unauthenticated requests are limited to
    sixty an hour **per IP address**, and GitHub's runners share those.
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
            raise SystemExit(f"the RubyInstaller release listing answered {error.code}") from error
        found += page_of
        if len(page_of) < 100:
            break
    return found


def resolve(spec: str, arch: str) -> tuple[str, str, str, str | None]:
    """Turn ``3.4``, ``3.4.10`` or ``latest`` into one published archive.

    Answers ``(ruby version, tag, download url, digest)``. Two version numbers are in play and only
    one of them is MixEngine's: the tag ``RubyInstaller-3.4.10-1`` is Ruby 3.4.10 in RubyInstaller's
    *first* packaging of it, and a re-packaging bumps the second number without changing which Ruby
    this is. The index is keyed on the Ruby version, so a later packaging replaces the artifact in
    place — and the tag is kept in the manifest, which is where the difference belongs.

    A line with no build for this architecture is an **empty cell and not a failure**: upstream's
    first ARM64 archive is in the 3.4 line, so asking for Ruby 3.3 on Windows-on-ARM is a question
    that was answered years ago and should not take the other legs' release down with it.
    """
    offered: dict[tuple[int, ...], tuple[str, str, str, str | None]] = {}
    lines: set[str] = set()

    for release in releases():
        tag = release.get("tag_name", "")
        match = re.fullmatch(r"RubyInstaller-(\d+\.\d+\.\d+)-(\d+)", tag)
        if not match:
            continue
        version, packaging = match.group(1), int(match.group(2))

        for asset in release.get("assets", ()):
            if asset["name"] != f"rubyinstaller-{version}-{packaging}-{arch}.7z":
                continue
            lines.add(".".join(version.split(".")[:2]))
            key = borrow.parts(version) + (packaging,)
            offered[key] = (version, tag, asset["browser_download_url"], asset.get("digest"))

    if not offered:
        borrow.unavailable(f"RubyInstaller publishes no {arch} archive in any release")

    if spec == "latest":
        candidates = sorted(offered)
    else:
        prefix = borrow.parts(spec)
        candidates = sorted(key for key in offered if key[: len(prefix)] == prefix)

    if not candidates:
        borrow.unavailable(
            f"RubyInstaller has no {spec} archive for {arch}. It offers "
            f"{', '.join(sorted(lines, key=borrow.parts))}."
        )
    return offered[candidates[-1]]


def describe(tree: Path, version: str, arch: str, tag: str, url: str, digest: str,
             published: str | None,
             added: list[str] | tuple[()] = (), removed: list[str] | tuple[()] = ()) -> dict:
    """What is in the archive, as the daemon will read it.

    *removed* is what :func:`prune` threw out, which until P5 was nothing at all while
    ``ruby_unix.py`` was throwing out the same three directories from the four cells it compiles —
    the packer/compiler pair that has to answer the same question or say why it does not. The list
    lives in :mod:`ruby_parity` now so neither side can move without the other. *added* is still
    empty: nothing here writes into the archive, and it stays an argument because the field it
    fills is the one that would have to say so. See :func:`borrow.declare` for what the fields
    promise and what is checked before they are written.
    """
    provides = {}
    for name, candidates in LAYOUT.items():
        found = next((path for path in candidates if (tree / path).exists()), None)
        if found:
            provides[name] = found

    missing = [name for name in REQUIRED if name not in provides]
    if missing:
        raise SystemExit(
            f"the archive provides no {', '.join(missing)} — expected at "
            f"{', '.join(' or '.join(LAYOUT[name]) for name in missing)}. Contents of bin/: "
            f"{sorted(path.name for path in (tree / 'bin').iterdir())[:25]}"
        )

    manifest = {
        "schema": 1,
        "kind": "ruby",
        "version": version,
        "os": "windows",
        "arch": arch,
        "source": "borrowed",
        "upstream": {
            "project": "oneclick/rubyinstaller2",
            "release": tag,
            "url": url,
            "sha256": digest,
            # Precise about what this is worth. RubyInstaller publishes no checksum file; what it
            # publishes beside each archive is a detached OpenPGP signature, which would have to be
            # checked against a key fetched from a keyserver — a moving dependency on a machine with
            # nothing installed, and the same trade the Node.js recipe records. `digest` is GitHub's
            # own hash of the asset it is serving, so it catches a truncated or corrupted download
            # and states nothing about the publisher that HTTPS did not already state.
            "verified_against": (
                "GitHub's published asset digest over HTTPS to the publisher"
                if published else "HTTPS to the publisher; upstream publishes no checksum file"
            ),
        },
        "provides": provides,
    }
    # Stated rather than left to be noticed. The other five cells of this version run YJIT and
    # compile a native gem; these two can do neither and no pruning or downloading fixes it, so the
    # artifact says which and why. See `ruby_parity.LACKS` — it is the only field here that is an
    # admission, and writing it is the alternative to an artifact that is quietly smaller than the
    # word `ruby` promises.
    absent = ruby_parity.lacks("windows")
    if absent:
        manifest["lacks"] = absent
    # And what it keeps that the rule would otherwise throw out — the headers and the import
    # library, on a cell whose `lacks` above has just said it ships no compiler. The two are not in
    # tension: see :func:`ruby_parity.keeps`, which reads both off the tree and states why.
    return borrow.declare(tree, manifest, added, removed, ruby_parity.keeps(tree, "windows"))


def smoke(tree: Path, version: str, manifest: dict) -> dict:
    """Run the artifact from somewhere it has never been, and make it be itself while doing it.

    ``ruby --version`` alone would pass on an archive that is unusable in three separate ways: the
    runner has a Ruby of its own that would answer identically, an interpreter that found the
    *runner's* standard library answers it too, and a Ruby that cannot verify a certificate answers
    it right up to the first ``gem install``. What is proven instead is in :mod:`ruby_smoke`, and it
    is proven there rather than here because the recipe that compiles macOS and Linux has to make
    exactly the same claim about exactly the same runtime.
    """
    elsewhere = borrow.moved(tree)
    report = ruby_smoke.interpreter(elsewhere, version, manifest["provides"])
    ran = ruby_smoke.commands(elsewhere, manifest["provides"])
    borrow.discard(elsewhere)
    return {"relocated": True, "ran": ran, "openssl": report["openssl"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--version", required=True,
        help="exact version (3.4.10), a line (3.4) for its newest release, or 'latest'",
    )
    parser.add_argument("--out", default="dist", type=Path)
    args = parser.parse_args()

    operating_system, arch = borrow.host("Ruby")
    if operating_system != "windows":
        raise SystemExit(
            f"this recipe borrows RubyInstaller, which publishes for Windows only, and it is "
            f"running on {operating_system}. Ruby for macOS and Linux is compiled rather than "
            f"borrowed — see T27b in .claude/roadmap/phase-2-runtimes.md."
        )

    version, tag, url, published = resolve(args.version, ARCHES[arch])
    print(f"{tag}: {args.version} resolves to Ruby {version} ({ARCHES[arch]})")

    work = Path(tempfile.mkdtemp(prefix="mixengine-ruby-"))
    downloaded = work / url.rsplit("/", 1)[-1]
    print(f"borrowing {url}")
    try:
        urllib.request.urlretrieve(url, downloaded)
    except urllib.error.HTTPError as error:
        raise SystemExit(f"{url} answered {error.code}") from error

    actual = borrow.sha256(downloaded)
    if published and published != f"sha256:{actual}":
        raise SystemExit(f"sha256 mismatch: got {actual}, GitHub states {published}")
    print(f"sha256 {actual}" + (" (matches GitHub's asset digest)" if published else ""))

    tree = borrow.unpack(downloaded, work / "unpacked", "7z")

    # Before the tree is described and before it is proven, in that order and for the reason the
    # Python recipe gives: the manifest has to describe the tree that ships, and the smoke test is
    # what stands between a prune and a runtime that was quietly cut in half.
    removed = prune(tree)
    manifest = describe(tree, version, arch, tag, url, actual, published, removed=removed)
    manifest["smoke"] = smoke(tree, version, manifest)

    borrow.publish(tree, manifest, args.out, "zip")
    shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
