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

Everything mechanical is in :mod:`borrow`, shared with the Node.js and Python recipes.

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

# Required from the relocated tree. Every one is a compiled extension or the thing that proves one
# works: no `openssl` is no `gem install` and no `bundle install`, no `psych` is no YAML and
# therefore no `config/database.yml`, no `fiddle` is no FFI, and `zlib` is how every gem arrives.
MODULES = (
    "openssl", "zlib", "psych", "fiddle", "digest", "socket", "json", "stringio",
    "bigdecimal", "io/console", "date", "etc",
)

PROBE = """
require "json"
require "openssl"
require "digest"
require "zlib"
require "rbconfig"
puts JSON.generate({
  version: RUBY_VERSION,
  platform: RUBY_PLATFORM,
  ruby: RbConfig.ruby,
  rubylibdir: RbConfig::CONFIG["rubylibdir"],
  archdir: RbConfig::CONFIG["archdir"],
  gem_dir: Gem.dir,
  openssl: OpenSSL::OPENSSL_VERSION,
  zlib: Zlib.zlib_version,
  sha256: Digest::SHA256.hexdigest("mixengine"),
  cert_file: OpenSSL::X509::DEFAULT_CERT_FILE,
  cert_file_exists: File.exist?(OpenSSL::X509::DEFAULT_CERT_FILE),
})
"""


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
             published: str | None) -> dict:
    """What is in the archive, as the daemon will read it."""
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

    return {
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


def gem_versions(tree: Path) -> dict[str, str]:
    """Which version of each bundled gem is *in this archive*, read off its gemspecs.

    The point is to have an expectation that did not come from running the thing being tested. When
    ``bundle --version`` says 2.6.9 and the archive contains ``bundler-2.6.9.gemspec``, the batch
    file ran this tree's Ruby; when it says something else, another Ruby on the machine answered and
    the artifact has proven nothing about itself.
    """
    found: dict[str, str] = {}
    for gems in sorted((tree / "lib" / "ruby" / "gems").glob("*/specifications")):
        for spec in list(gems.glob("*.gemspec")) + list((gems / "default").glob("*.gemspec")):
            name, _, version = spec.name[: -len(".gemspec")].rpartition("-")
            found.setdefault(name, version)
    # `bundle` is the command; `bundler` is the gem. The one alias worth spelling out, because it is
    # the only place in this table where a command and its gem are named differently.
    if "bundler" in found:
        found.setdefault("bundle", found["bundler"])
    return found


def smoke(tree: Path, version: str, manifest: dict) -> dict:
    """Run the artifact from somewhere it has never been, and make it be itself while doing it.

    ``ruby --version`` alone would pass on an archive that is unusable in three separate ways: the
    runner has a Ruby of its own that would answer identically, an interpreter that found the
    *runner's* standard library answers it too, and a Ruby that cannot verify a certificate answers
    it right up to the first ``gem install``. So five things are proven.

    *It is this Ruby*, and *it found its own library after moving*: ``RbConfig.ruby``,
    ``rubylibdir``, ``archdir`` and ``Gem.dir`` all have to be inside the relocated tree. Those four
    are the whole of what ``--enable-load-relative`` claims, and they are the reason this cell can be
    borrowed at all.

    *Its compiled extensions load, and two are called rather than reported*: OpenSSL hashes a string
    and zlib states its own version, where ``OpenSSL::OPENSSL_VERSION`` alone is a string constant a
    broken build recites just as confidently.

    *It carries its own trust store.* ``OpenSSL::X509::DEFAULT_CERT_FILE`` has to exist **and be
    inside the tree**. A Ruby whose CA bundle points at the packaging machine works perfectly on the
    packaging machine, and this is the check that would have caught it.

    *Its bundled commands are its own.* Each of ``gem``, ``bundle``, ``rake`` and ``irb`` reports the
    version whose gemspec is in this archive, and ``gem`` additionally has to name a gem home inside
    the tree.
    """
    elsewhere = borrow.moved(tree)
    ruby = elsewhere / manifest["provides"]["ruby"]
    path = borrow.clean_path(ruby.parent)
    # A runner with a Ruby set up has usually exported at least one of these, and every one of them
    # would make this test pass for a reason that will not exist on a user's machine.
    drop = ("RUBY", "GEM_", "BUNDLE_")

    # `ruby 3.4.10 (2026-07-01 …)`, but `ruby 2.7.8p225 (…)` in the 2.x lines — the patchlevel used
    # to be part of the version word and is not any more, so the number is matched rather than the
    # word taken.
    banner = borrow.run(ruby, "--version", path=path, drop=drop)
    stated = re.match(r"ruby (\d+\.\d+\.\d+)", banner)
    if not stated or stated.group(1) != version:
        raise SystemExit(f"ruby reports {banner!r}, expected {version}")

    for module in MODULES:
        borrow.run(ruby, "-e", f"require {module!r}", path=path, drop=drop)

    report = json.loads(borrow.run(ruby, "-e", PROBE, path=path, drop=drop))
    if report["version"] != version:
        raise SystemExit(f"RUBY_VERSION is {report['version']}, expected {version}")
    for field in ("ruby", "rubylibdir", "archdir", "gem_dir"):
        where = Path(report[field])
        if not where.resolve().is_relative_to(elsewhere.resolve()):
            raise SystemExit(
                f"{field} is {where}, which is not inside the tree this Ruby was copied to — "
                f"--enable-load-relative did not survive the move, or another Ruby answered"
            )
    if len(report["sha256"]) != 64:
        raise SystemExit(f"the bundled OpenSSL answered {report['sha256']!r}")
    if not report["cert_file_exists"]:
        raise SystemExit(
            f"OpenSSL::X509::DEFAULT_CERT_FILE is {report['cert_file']}, which does not exist — "
            "every HTTPS request this Ruby makes would fail verification, `gem install` included"
        )
    if not Path(report["cert_file"]).resolve().is_relative_to(elsewhere.resolve()):
        raise SystemExit(
            f"this Ruby's CA bundle is {report['cert_file']}, outside its own tree: it would stop "
            "verifying certificates the moment the archive is installed somewhere else"
        )

    print(f"ruby {report['version']} {report['platform']}, {report['openssl']}, "
          f"zlib {report['zlib']}, CA bundle inside the tree")
    ran = ["ruby --version", "require " + ", ".join(MODULES), "ruby -e (RbConfig, Gem, OpenSSL)"]

    packaged = gem_versions(elsewhere)
    for name in sorted(set(manifest["provides"]) - {"ruby"}):
        reported = borrow.run(
            elsewhere / manifest["provides"][name], "--version", path=path, drop=drop
        )
        expected = packaged.get(name)
        if expected and expected not in reported.replace(",", " ").split():
            raise SystemExit(
                f"{name} reports {reported!r}, but the {name} inside this archive is {expected} — "
                f"something else on this machine answered"
            )
        print(f"{manifest['provides'][name]}: {reported}")
        ran.append(f"{manifest['provides'][name]} --version")

    # `gem` is the one command with no gemspec of its own — RubyGems is part of Ruby — so the check
    # above has nothing to compare it against. This is what replaces it, and it is stronger.
    home = borrow.run(elsewhere / manifest["provides"]["gem"], "env", "gemdir",
                      path=path, drop=drop)
    if not Path(home).resolve().is_relative_to(elsewhere.resolve()):
        raise SystemExit(f"gem would install into {home}, which is outside this artifact")
    ran.append(f"{manifest['provides']['gem']} env gemdir")

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

    manifest = describe(tree, version, arch, tag, url, actual, published)
    manifest["smoke"] = smoke(tree, version, manifest)

    borrow.publish(tree, manifest, args.out, "zip")
    shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
