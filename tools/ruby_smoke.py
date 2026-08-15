#!/usr/bin/env python3
"""What both Ruby recipes claim about a Ruby, in one place so the two cannot drift.

There are two of them — ``ruby.py`` borrows RubyInstaller's archive on Windows, ``ruby_unix.py``
compiles macOS and Linux from source — and they produce artifacts a MixEngine daemon treats
identically. The *mechanics* differ completely and belong apart; what must not differ is the claim
each one makes before publishing, because the manifest field it is written into is the same word.
``borrow.py``'s docstring states the general form of this: a check two producers implement
separately will drift, and the drift is invisible exactly because they agree on the field name.

So this holds the questions that are about **Ruby** rather than about downloading or compiling:

* did the interpreter find its own standard library, its own gem home and its own CA bundle after
  the tree was moved — the whole of what ``--enable-load-relative`` claims, and the reason a Ruby
  can be packaged at all;
* do its compiled extensions load, and do two of them *work* rather than merely report a version;
* can it verify a real certificate chain;
* are ``gem``, ``bundle``, ``rake`` and ``irb`` the ones packed beside it rather than the runner's.

Python 3 stdlib only, by policy: this runs on a GitHub runner with nothing installed.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import borrow  # noqa: E402  — siblings, and this directory is not importable as a package

# Required from the relocated tree. Every one is a compiled extension or the thing that proves one
# works: no `openssl` is no `gem install` and no `bundle install`, no `psych` is no YAML and
# therefore no `config/database.yml`, no `fiddle` is no FFI, and `zlib` is how every gem arrives.
MODULES = (
    "openssl", "zlib", "psych", "fiddle", "digest", "socket", "json", "stringio",
    "bigdecimal", "io/console", "date", "etc",
)

# Environment prefixes removed before running anything out of the archive. A runner with a Ruby set
# up has usually exported at least one of these, and every one of them would make these checks pass
# for a reason that will not exist on a user's machine.
DROP = ("RUBY", "GEM_", "BUNDLE_")

# The host the handshake below is made against. rubygems.org rather than whichever host this
# particular recipe downloaded from, because it is the one the artifact itself will talk to: a Ruby
# that starts, requires `openssl` and then fails every `gem install` with a verification error is
# the failure furthest from its cause in this whole table.
HANDSHAKE = "rubygems.org"

PROBE = """
require "json"
require "openssl"
require "digest"
require "zlib"
require "psych"
require "rbconfig"
require "net/http"

# The handshake is the claim; `cert_file` below is only the diagnosis when it fails.
begin
  handshake = Net::HTTP.start(%(host)s, 443, use_ssl: true,
                              open_timeout: 30, read_timeout: 30) { |http| http.get("/").code.to_i }
rescue => error
  handshake = "#{error.class}: #{error.message}"
end

puts JSON.generate({
  version: RUBY_VERSION,
  platform: RUBY_PLATFORM,
  ruby: RbConfig.ruby,
  rubylibdir: RbConfig::CONFIG["rubylibdir"],
  archdir: RbConfig::CONFIG["archdir"],
  gem_dir: Gem.dir,
  openssl: OpenSSL::OPENSSL_VERSION,
  zlib: Zlib.zlib_version,
  libyaml: Psych::LIBYAML_VERSION,
  sha256: Digest::SHA256.hexdigest("mixengine"),
  cert_file: OpenSSL::X509::DEFAULT_CERT_FILE,
  cert_file_exists: File.exist?(OpenSSL::X509::DEFAULT_CERT_FILE),
  handshake: handshake,
})
""".replace("%(host)s", json.dumps(HANDSHAKE))


def gem_versions(tree: Path) -> dict[str, str]:
    """Which version of each bundled gem is *in this archive*, read off its gemspecs.

    The point is to have an expectation that did not come from running the thing being tested. When
    ``bundle --version`` says 2.6.9 and the archive contains ``bundler-2.6.9.gemspec``, the wrapper
    ran this tree's Ruby; when it says something else, another Ruby on the machine answered and the
    artifact has proven nothing about itself.
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


def interpreter(tree: Path, version: str, provides: dict[str, str]) -> dict:
    """Prove that the Ruby in *tree* is this Ruby, is self-contained, and can verify a certificate.

    *It is this Ruby*, and *it found its own library after moving*: ``RbConfig.ruby``,
    ``rubylibdir``, ``archdir`` and ``Gem.dir`` all have to be inside *tree*. Those four are the
    whole of what ``--enable-load-relative`` claims, and they are what makes a Ruby packageable.

    *Its compiled extensions load, and two are called rather than reported*: OpenSSL hashes a string
    and zlib states its own version, where ``OpenSSL::OPENSSL_VERSION`` alone is a string constant a
    broken build recites just as confidently.

    *It carries its own trust store, and the store works.* ``DEFAULT_CERT_FILE`` has to exist and be
    inside the tree — a Ruby whose CA bundle points at the packaging machine works perfectly on the
    packaging machine — and then a real chain is verified over a real connection. Both halves are
    needed and neither implies the other: the path check catches a bundle that will be missing on
    the user's machine, the handshake catches one that is present and useless.
    """
    ruby = tree / provides["ruby"]
    path = borrow.clean_path(ruby.parent)

    # `ruby 3.4.10 (2026-07-01 …)`, but `ruby 2.7.8p225 (…)` in the 2.x lines — the patchlevel used
    # to be part of the version word and is not any more, so the number is matched rather than the
    # word taken.
    banner = borrow.run(ruby, "--version", path=path, drop=DROP)
    stated = re.match(r"ruby (\d+\.\d+\.\d+)", banner)
    if not stated or stated.group(1) != version:
        raise SystemExit(f"ruby reports {banner!r}, expected {version}")

    for module in MODULES:
        borrow.run(ruby, "-e", f"require {module!r}", path=path, drop=DROP)

    report = json.loads(borrow.run(ruby, "-e", PROBE, path=path, drop=DROP, timeout=600))
    if report["version"] != version:
        raise SystemExit(f"RUBY_VERSION is {report['version']}, expected {version}")
    for field in ("ruby", "rubylibdir", "archdir", "gem_dir"):
        where = Path(report[field])
        if not where.resolve().is_relative_to(tree.resolve()):
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
    if not Path(report["cert_file"]).resolve().is_relative_to(tree.resolve()):
        raise SystemExit(
            f"this Ruby's CA bundle is {report['cert_file']}, outside its own tree: it would stop "
            "verifying certificates the moment the archive is installed somewhere else"
        )
    if not (isinstance(report["handshake"], int) and 200 <= report["handshake"] < 400):
        raise SystemExit(
            f"this Ruby could not verify a real certificate chain against its own CA bundle "
            f"({report['cert_file']}): https://{HANDSHAKE}/ answered {report['handshake']!r}"
        )

    print(f"ruby {report['version']} {report['platform']}, {report['openssl']}, "
          f"zlib {report['zlib']}, libyaml {report['libyaml']}, CA bundle inside the tree, "
          f"https://{HANDSHAKE}/ verified")
    return report


def commands(tree: Path, provides: dict[str, str]) -> list[str]:
    """Run every bundled command from the moved tree and make each one prove it is ours.

    Returns what was run, for ``smoke.ran`` in the manifest.
    """
    ruby = tree / provides["ruby"]
    path = borrow.clean_path(ruby.parent)
    ran = ["ruby --version", "require " + ", ".join(MODULES), "ruby -e (RbConfig, Gem, OpenSSL)"]

    packaged = gem_versions(tree)
    for name in sorted(set(provides) - {"ruby"}):
        reported = borrow.run(tree / provides[name], "--version", path=path, drop=DROP)
        expected = packaged.get(name)
        if expected and expected not in reported.replace(",", " ").split():
            raise SystemExit(
                f"{name} reports {reported!r}, but the {name} inside this archive is {expected} — "
                f"something else on this machine answered"
            )
        print(f"{provides[name]}: {reported}")
        ran.append(f"{provides[name]} --version")

    # `gem` is the one command with no gemspec of its own — RubyGems is part of Ruby — so the check
    # above has nothing to compare it against. This is what replaces it, and it is stronger.
    home = borrow.run(tree / provides["gem"], "env", "gemdir", path=path, drop=DROP)
    if not Path(home).resolve().is_relative_to(tree.resolve()):
        raise SystemExit(f"gem would install into {home}, which is outside this artifact")
    ran.append(f"{provides['gem']} env gemdir")
    return ran
