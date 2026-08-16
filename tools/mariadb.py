#!/usr/bin/env python3
"""Borrow a MariaDB build from downloads.mariadb.org and repack it as a MixEngine artifact.

**The cell this repository expected to be cheap, and it is cheap in exactly two places.** MixEngine's
runtime table said "official zip / official tarball / official tarball" for all three systems. Asked
rather than assumed — which is what "borrow costs one evaluation" is for — the catalogue answers
something else, and the evaluation is written up in `MixEngine's runtime-packaging.md`_:

* **Windows x86_64** publishes ``mariadb-<version>-winx64.zip``. Borrowed here.
* **Linux x86_64** publishes ``mariadb-<version>-linux-systemd-x86_64.tar.gz``. Borrowed here.
* **Linux aarch64** publishes no tarball at all — only ``.deb`` packages, which ``mariadb_deb.py``
  takes apart.
* **macOS** publishes nothing, on either architecture, and never has: every release from 10.2 to 13.1
  offers Linux and Windows and nothing else. ``mariadb_build.py`` compiles those.
* **Windows aarch64** likewise publishes nothing. Same recipe, built natively on an ARM runner.

So this file is two of six cells, and the reason it is not simply :mod:`caddy` with a different table
is the second half of that sentence: **a borrowed MariaDB is not self-contained.** Caddy is one static
Go binary; a MariaDB bintar is a hundred programs and a plugin directory linked against whatever the
build machine had — OpenSSL, libaio, libnuma, libsystemd, PCRE2 — by soname, with no search path of
its own. Installed on a user's machine those are a different version or absent, and the failure is a
server that will not start with an error naming a file nobody installed. ``relocate.bundle`` therefore
runs over a *borrowed* tree here, which no other borrow recipe in this repository needs, and
``upstream.added`` records every library it put in.

Three further decisions:

*The REST API is the catalogue, and it is also the checksum.* ``downloads.mariadb.org/rest-api``
states, per release, every file with all four digests beside it. So what exists and what it should
hash to come out of one document from the publisher, which is the same trade the Node.js and Caddy
recipes make. Upstream also publishes a PGP signature per file, and it is deliberately not checked:
that would mean ``gpg`` and a key distribution on a runner with nothing installed, which is the same
dependency the Caddy recipe refused ``cosign`` for.

*Its download URLs are ``http://`` and are rewritten to ``https://``.* Not a preference. The digest
is fetched over the same channel as the file, so a plain-text download would let anything on the path
substitute both.

*And the file is fetched from the publisher's archive rather than from those URLs at all.* They are a
redirector to third-party mirrors, one of which served a 10.6.28 tarball 1,846 bytes short of the
published one — so the checksum comes from the REST API and the bytes come from
``archive.mariadb.org``, MariaDB's own host and the one ``mariadb_deb.py`` already uses. See
``ARCHIVE``.

*The test suite is not shipped.* ``mysql-test/`` and ``sql-bench/`` are more than half the unpacked
tree, are a developer's tool for testing the server rather than for running one, and nothing in
MixEngine reaches for them. They are named in ``upstream.removed`` rather than quietly dropped.

Python 3 stdlib only, by policy: this runs on a GitHub runner with nothing installed.

.. _MixEngine's runtime-packaging.md: https://github.com/haiquang9994/MixEngine/blob/master/.claude/operations/runtime-packaging.md
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import borrow  # noqa: E402  — siblings, and this directory is not importable as a package
import mariadb_smoke  # noqa: E402
import relocate  # noqa: E402
import strip  # noqa: E402

API = "https://downloads.mariadb.org/rest-api/mariadb"

# **Where the bytes come from, which is not where the REST API points.** `file_download_url` is a
# redirector: it answers 302 to whichever third-party mirror the service picks that minute, and the
# mirror is not guaranteed to hold the file MariaDB published. CI caught one — 10.6.28 came back from
# `linux.domainesia.com` 1,846 bytes short of upstream's own copy and hashing to something else
# entirely — and the failure is worse than it looks, because *which* mirror answers changes per run,
# so the same recipe passes and fails at random. `archive.mariadb.org` is MariaDB's own host, is what
# `mariadb_deb.py` already borrows its packages from, and holds every release in the catalogue under
# one path shape. The checksum still comes from the REST API: one host states what the file should
# be, another serves it, and they have to agree.
ARCHIVE = "https://archive.mariadb.org"

# What this recipe can borrow, and what upstream calls it. The value is `(os, cpu, package_type)` as
# the REST API spells each — its own vocabulary, not this repository's — then the substring that picks
# one file out of a release that offers several for the same target, and last the directory the
# archive files that target under.
#
# `linux-systemd` is the only Linux bintar still published from 10.6 onwards; the plain `linux-` and
# `linux-glibc_214-` variants stopped there. It is named explicitly rather than matched loosely so
# that a release which brings the others back does not change which artifact this publishes.
BORROWABLE = {
    ("windows", "x86_64"): (
        "Windows", "x86_64", "ZIP file", "-winx64.zip", "winx64-packages",
    ),
    ("linux", "x86_64"): (
        "Linux", "x86_64", "gzipped tar file", "-linux-systemd-x86_64.tar.gz",
        "bintar-linux-systemd-x86_64",
    ),
}

# Anything below this is not in the REST API at all: the catalogue starts at 10.6, older lines having
# been retired from it. archive.mariadb.org still has them, and MixEngine does not offer them — this
# is a project whose oldest supported line is younger than the PHP floor by a decade.
FLOOR = (10, 6)

# Half the unpacked tree, and none of it is a database server. See the module docstring.
#
# **`bin/garbd` is here for a harder reason than size, and it was found by CI rather than by
# reading.** The bintar ships Galera's arbitrator daemon, which links `libboost_program_options.so
# .1.52.0` — a Boost from 2013 that exists on MariaDB's build machine and on no runner, no user
# machine and no current distribution. Bundling stops on it, correctly: it cannot invent a library
# that is missing from the machine. And the right answer is not to find that Boost, because MixEngine
# supervises a single server and has no cluster for an arbitrator to arbitrate. So the whole of
# Galera goes, and `upstream.removed` says it went.
#
# `mariadb-test` rather than `mysql-test`: upstream renamed the directory along with the binaries,
# and the old spelling silently matched nothing — leaving the whole suite in the archive and, worse,
# leaving it for the Galera globs below to walk through and list file by file.
PRUNE = ("mariadb-test", "mysql-test", "sql-bench", "share/man", "share/doc", "man", "docs",
         # Headers and import libraries, for compiling a C client against this server. MixEngine
         # installs a database rather than an SDK, and the compiled cells do not ship them either —
         # six artifacts of one version differing in what they contain is the thing worth avoiding.
         # `lib/pkgconfig` and `share/aclocal` are the rest of that SDK: a `.pc` file and an autoconf
         # macro describe how to link against headers this archive no longer carries.
         "include", "lib/pkgconfig", "share/aclocal",
         # Init scripts, systemd units, apparmor and SELinux policy, and a logrotate rule. Every one
         # of them names an absolute path and registers a system service — which is the thing
         # MixEngine supervises *instead of*, not something it installs. `symbols` is where the
         # Windows install layout collects `.pdb` files, and is empty once `DEBRIS` has run.
         "support-files", "symbols")

# **What MixEngine does not ship, stated once so that six artifacts of one version contain the same
# MariaDB.** A bintar is built with everything its maintainers can compile; a source build here is
# configured with `mariadb_build.DISABLED_PLUGINS`; and the `.deb` route takes six packages and
# therefore never had these at all, because upstream ships each as its own `mariadb-plugin-*`. Left
# alone, the same version would mean three different feature sets depending on which cell a user
# installed from — which is exactly the difference nobody chose that this repository keeps trying to
# eliminate.
#
# Each entry is also a thing a *local development environment* does not do: cluster storage engines,
# a federated engine, an ODBC/JDBC bridge, an S3 archive engine, a full-text engine for Japanese, a
# graph engine. `mariabackup` goes with them — it is upstream's physical-backup tool, MixEngine
# takes logical dumps with `mariadb-dump`, and the bintar ships it twice under two names.
#
# Keep this in step with `mariadb_build.DISABLED_PLUGINS`; the two are the same decision expressed
# to a packer and to a compiler.
NOT_SHIPPED = (
    "*rocksdb*", "mariadb-ldb*", "mysql_ldb*", "sst_dump*",     # RocksDB, and its own tooling
    "myrocks*",                                                 # …and its backup script
    "*mroonga*", "*groonga*",                                   # Mroonga, and the normaliser under it
    "*spider*", "*oqgraph*", "*columnstore*",
    "ha_connect*", "*.jar",                                     # CONNECT, and the JDBC bridge it uses
    "ha_s3*", "aria_s3_copy*",                                  # the S3 engine, and its own tooling
    "*auth_pam*", "pam_user_map*", "user_map.conf",             # PAM: a system authentication stack
    # Kerberos and HashiCorp Vault. Both are the authentication and key custody of an organisation
    # with a directory server, which is not a machine running a local development environment — and
    # `.deb` ships each as its own `mariadb-plugin-*`, so four of the six cells never had them. What
    # made these worth naming rather than tolerating is what they drag in behind them: bundling
    # followed `auth_gssapi` into krb5, LDAP, SASL, GnuTLS and Nettle, and `hashicorp` into libcurl,
    # nghttp2, brotli, libssh, RTMP and PSL — eighteen libraries, ten megabytes, for two features.
    "*gssapi*", "*hashicorp*",
    # **The test suite's binaries, which are not in the test suite's directory.** `mariadb-test` and
    # `mysql-test` above take the suite itself; these four executables live in `bin/` beside the
    # server and are useless without it — 18 MB on a Linux bintar, 21 MB in a Windows build, and
    # nothing in MixEngine has ever run them. `echo.exe` is a helper the suite installs on Windows
    # because `cmd` has no `echo` that behaves; shipping a database server should not mean shipping
    # an `echo`.
    "mariadb-test*", "mysqltest*", "mariadb-client-test*", "mysql_client_test*", "echo.exe",
    # The demonstration and QA plugins upstream builds beside the real ones. Each is an example for
    # somebody writing a plugin — `ha_example` is a storage engine that stores nothing,
    # `example_key_management` encrypts with a key derived from the key id — and a server that loads
    # one is a server somebody is developing MariaDB on rather than running.
    "adt_null*", "auth_0x0100*", "auth_test_plugin*", "qa_auth_*", "mypluglib*",
    "*daemon_example*", "ha_example*", "dialog_examples*", "func_test*", "type_test*",
    "debug_key_management*", "example_key_management*", "ha_test_sql_discovery*",
    "test_sql_service*", "test_versioning*", "test_pam_modules",
    # The rest of the SDK `PRUNE` starts on: static libraries to link a client against, and the
    # scripts that print the flags for doing so. Without `include/` there is nothing to compile.
    "*.a", "mariadb_config*", "mysql_config*", "*.pl",
    # What a distribution adds and a tarball does not. `innotop` is a curses monitor, `mariadb-report`
    # a Perl status summary, `debian-start.inc.sh` a hook the init script sources, `mini-benchmark` a
    # benchmark; each arrives with the `.deb` packages and exists in no other cell. Small enough that
    # size is not the argument — the argument is that they exist on one operating system only.
    "innotop*", "mariadb-report*", "debian-start*", "echo_stderr", "mini-benchmark",
)

# **`mariabackup` is deliberately not in that list, and it was in it once.** Everything above is a
# storage engine or an authentication stack a local development environment does not use; a backup
# tool is not. It takes a physical copy of a running database — far faster than a logical dump once
# a database stops being small — needs nothing from the machine, and compiles on every target,
# including the two that upstream publishes no binary for. So the parity here is towards *having*
# it: the bintar keeps it and `mariadb_build.py` no longer turns it off.

# Debug information, by extension, wherever it sits. `bin/server.pdb` alone is 74 MB unpacked and
# 29 MB of the Windows zip — the same waste `strip_debug` takes out of a Linux bintar, in the form
# Windows uses. Upstream publishes the symbols as a separate `-debugsymbols.zip` for whoever wants
# them, which is exactly the arrangement Debian makes with its `-dbg` packages.
#
# `.lib` goes with `include` above: an import library is a linker input, not something a server
# loads.
DEBRIS = ("*.pdb", "*.lib")

# The same thing on the other side of the build: `bin/server.lib` is 14 MB of import library in a
# Windows ARM64 build and nothing links against it, which is how that cell came to be 12 MB larger
# than the x86_64 one it should have matched. It is `DEBRIS` that already said so — the compiled
# recipe simply was not running this list.

# Galera, wherever the bintar happens to put it, and under every name it uses. A path list was not
# enough — removing `bin/garbd` and `lib/galera` left `lib/libgalera_smm.so`, which needs the OpenSSL
# retired in 2019 — and a `*galera*` glob was not enough either, because the arbitrator is called
# `garbd` and matches neither. `wsrep` is the third spelling: the replication API Galera plugs into,
# which is what `wsrep_info.so`, `wsrep_sst_*` and `wsrep.cnf` are named after and which is inert
# without a provider to plug in. `garb*` rather than `*garbd*` because the systemd wrapper beside it
# is `garb-systemd`. The provider, the arbitrator, the API and the state-transfer scripts are one
# feature; MixEngine supervises a single server and has no cluster for any of it.
GALERA = ("*galera*", "garb*", "*wsrep*")


def get(url: str, timeout: int = 120) -> dict:
    return json.loads(borrow.fetch(url, timeout=timeout))


def secure(url: str) -> str:
    """The publisher's own URL over TLS. See the module docstring — this is not cosmetic."""
    return url.replace("http://", "https://", 1) if url.startswith("http://") else url


def lines() -> dict[tuple[int, ...], dict]:
    """Every MariaDB release series the publisher currently lists, keyed for comparison.

    Preview and RC series are dropped rather than ranked, the same rule the Ruby recipe applies to
    previews: 13.1 is listed beside 11.8 and a channel nobody asked for should not be what ``latest``
    means.
    """
    found: dict[tuple[int, ...], dict] = {}
    for series in get(f"{API}/")["major_releases"]:
        if series.get("release_status") != "Stable":
            continue
        key = borrow.parts(series["release_id"])
        if key >= FLOOR:
            found[key] = series
    if not found:
        raise SystemExit(f"{API}/ listed no stable release series at all; its format has changed")
    return found


def resolve(spec: str, target: tuple[str, str]) -> tuple[str, str, str, str, str | None]:
    """Turn ``11.8``, ``11.8.8`` or ``latest`` into one published file.

    Answers ``(version, url, mirror, sha256, end of life)`` — the archive's own copy first and the
    REST API's redirector second, because the two are not interchangeable and only the first is
    reproducible. See ``ARCHIVE``. The end-of-life date comes back from the same
    document, which is why MariaDB has no hand-written entry in ``data/eol.json``: upstream states a
    dated schedule per series through the API, and copying it into a file here would be a second
    source that goes stale silently.

    A series with no build for this target is an **empty cell and not a failure**, as in the Caddy
    recipe — though for MariaDB the empty cells are whole architectures rather than early versions.
    """
    stated = lines()
    if spec == "latest":
        wanted = [max(stated)]
    else:
        prefix = borrow.parts(spec)
        if len(prefix) < 2:
            raise SystemExit(
                f"{spec} is not a MariaDB series: they are numbered major.minor (10.11, 11.4, 11.8) "
                f"and a bare {spec} would name several"
            )
        wanted = [key for key in stated if key[:2] == prefix[:2]]
        if not wanted:
            raise SystemExit(
                f"downloads.mariadb.org lists no stable {spec}. It offers "
                f"{', '.join(stated[key]['release_id'] for key in sorted(stated))}."
            )

    series = stated[wanted[0]]
    catalogue = get(f"{API}/{series['release_id']}/")["releases"]
    system, cpu, package, tail, directory = BORROWABLE[target]

    offered: dict[tuple[int, ...], tuple[str, str, str, str]] = {}
    unverifiable: list[str] = []
    for version, release in catalogue.items():
        if spec not in ("latest",) and len(borrow.parts(spec)) == 3 and version != spec:
            continue
        for entry in release.get("files", ()):
            name = entry.get("file_name", "")
            if entry.get("os") != system or entry.get("cpu") != cpu:
                continue
            if entry.get("package_type") != package or not name.endswith(tail):
                continue
            # Upstream publishes the symbols beside the build under the same package type, and an
            # artifact of those would install a gigabyte of nothing.
            if "debugsymbols" in name:
                continue
            # **A file with no stated checksum is one release out of a series, not a broken API.**
            # `mariadb-11.4.0-winx64.zip` — the first release of that line — is listed with an empty
            # checksum object while every 11.4.x after it states one, and treating that as a shape
            # change killed the whole Windows cell over a version this recipe would never choose:
            # `max(offered)` takes the newest. So it is skipped and named, not fatal.
            digest = (entry.get("checksum") or {}).get("sha256sum")
            if not digest:
                unverifiable.append(name)
                continue
            offered[borrow.parts(version)] = (
                version,
                f"{ARCHIVE}/mariadb-{version}/{directory}/{name}",
                secure(entry["file_download_url"]),
                digest,
            )

    if unverifiable:
        print(f"not borrowable, listed with no sha256sum: {', '.join(sorted(unverifiable))}")
    if not offered and unverifiable:
        # Every candidate unverifiable is the shape change the per-entry skip is not. It must not
        # become an empty cell: that would publish silence for a series upstream does build.
        raise SystemExit(
            f"every {package} for {system}/{cpu} in {series['release_id']} is listed with no "
            f"sha256sum; the API's shape has changed"
        )
    if not offered:
        borrow.unavailable(
            f"downloads.mariadb.org publishes no {package} for {system}/{cpu} in "
            f"{series['release_id']}"
        )
    chosen = offered[max(offered)]
    return (*chosen, series.get("release_eol_date"))


# Where a licence text sits once it is installed, in the two shapes this repository meets: at the root
# of a Homebrew keg, and under `share/doc/<anything>/` — Debian's spelling and also where several
# formulas put theirs. Globbed rather than named because projects disagree about `LICENSE`, `LICENCE`,
# `COPYING` and every suffix of each.
LICENCE_GLOBS = ("LICENSE*", "LICENCE*", "COPYING*", "COPYRIGHT*", "copyright")


def dpkg_owner(path: Path) -> str | None:
    """Which Debian package installed this file, if the machine can say."""
    try:
        result = subprocess.run(["dpkg", "-S", str(path)], capture_output=True, text=True,
                                timeout=120)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0 or ":" not in result.stdout:
        return None
    # `libssl3:arm64: /usr/lib/...` on a multi-arch system; the qualifier is not part of the name,
    # and `/usr/share/doc` is filed under the bare one.
    return result.stdout.split(":", 1)[0].strip() or None


def licence_texts(origin: Path) -> list[tuple[str, Path]]:
    """The licence files belonging to a library at ``origin``, as ``(who it belongs to, file)``."""
    real = origin.resolve()
    places: list[Path] = []
    owner: str | None = None

    if "/Cellar/" in str(real):
        # **The keg, not the formula directory.** Walking up until the parent is `Cellar` stops at
        # `/opt/homebrew/Cellar/snappy`, which holds version directories and no files — which is how
        # six Homebrew libraries came to be bundled into the macOS artifacts with `licenses/` holding
        # nothing but MariaDB's own. One level lower is `.../snappy/1.2.2`, where the files are.
        keg = real
        while keg.parent.parent.name != "Cellar" and keg.parent != keg:
            keg = keg.parent
        owner = keg.parent.name
        places = [keg] + sorted(p for p in keg.glob("share/doc/*") if p.is_dir())
    else:
        owner = dpkg_owner(real) or dpkg_owner(origin)
        if owner:
            places = [Path("/usr/share/doc") / owner]

    found: list[tuple[str, Path]] = []
    for place in places:
        for pattern in LICENCE_GLOBS:
            for text in sorted(place.glob(pattern)):
                if text.is_file():
                    found.append((owner or "unknown", text))
    return found


def bundled_licences(tree: Path, bundled: dict[str, Path]) -> None:
    """Ship the licence of every library ``relocate.bundle`` put beside the server.

    **Decided once here for the same reason :func:`prune` is.** ``relocate.bundle`` already answers
    with where each library came from precisely so that its caller can do this, and only the macOS
    recipe tried — with a walk that stopped one directory too high, so it collected nothing and said
    nothing. The two Linux recipes bundle between eighteen and twenty-two system libraries apiece and
    never looked at all: OpenSSL, PCRE2, lz4, lzo, snappy and zstd travel inside these artifacts, and
    several of those licences require their text to travel with them. That is a condition of
    redistributing the archive, not tidiness, so a library whose licence cannot be found is a failure
    and not a warning — a warning is what the last one was.
    """
    if not bundled:
        return
    licences = tree / "licenses"
    licences.mkdir(exist_ok=True)

    rows, unlicensed = [], []
    for name, origin in sorted(bundled.items()):
        rows.append(f"{name}\t{origin}")
        texts = licence_texts(origin)
        if not texts:
            unlicensed.append(f"{name} (from {origin})")
            continue
        for owner, text in texts:
            shutil.copy2(text, licences / f"{owner}-{text.name}")

    (licences / "BUNDLED.tsv").write_text(
        "library\tbuilt from\n" + "\n".join(rows) + "\n", encoding="utf-8"
    )
    if unlicensed:
        raise SystemExit(
            "no licence text found for a bundled library, and the archive may not be redistributed "
            "without one: " + "; ".join(unlicensed)
        )


def strip_debug(tree: Path) -> dict[str, str]:
    """Take the debug symbols out of a borrowed bintar, and answer with which files that changed.

    **Measured because the artifacts did not agree with each other.** MariaDB 11.8.8 packs to 27 MB
    from upstream's own ``arm64`` ``.deb`` packages and to 371 MB from its ``x86_64`` bintar — the
    same server, the same compression. Debian strips its binaries and ships the symbols in a
    separate ``-dbg`` package; the bintar carries them inside every executable and every plugin.
    Nothing in MixEngine reads them, and a user would download them once per version per machine.

    ``--strip-debug`` rather than ``--strip-all``: the dynamic symbol table is what makes a shared
    object loadable and a stack trace nameable, and removing it saves nothing here because it is
    small. What goes is ``.debug_*``, which is nearly all of the difference above.

    **What this used to return was a sentence, and P6 is where it stopped being one.** It said
    "debug symbols stripped from 412 files (371 MB -> 27 MB)" into an ``upstream.stripped`` field of
    its own, which was this repository's first attempt at recording that a shipped file is not
    upstream's file — written before there was a rule and before ``upstream.changed`` existed. Two
    fields saying one thing is the shape of drift the rule is against, so the sentence goes and the
    mapping stays. The fold-in is worth more to this row than to the one it folds into: 344 MB came
    out of a bintar here and *nothing checked what came out*, while `strip.symbols` refuses to
    return unless the loader's and the linker's whole view of every file survived the operation.

    So `strip` failing is now a failed pack rather than a file quietly left alone. That was the
    right call when this ran `strip` over whatever `machine_files` handed it and hoped; it is the
    wrong one now that the same list is proved file by file, because a `strip` that exits non-zero
    on a MariaDB plugin is a fact about the archive and not about the tool.
    """
    if sys.platform != "linux" or not shutil.which("strip"):
        return {}

    files = relocate.machine_files(tree)
    before = sum(path.stat().st_size for path in files)
    # Spelled here rather than taken from one of `strip`'s two tables, and those tables are why:
    # `IMAGES` is `--strip-all` because a CPython tree's binaries are loaded and never linked
    # against, `ARCHIVES` is `--strip-debug` because an `ar` archive is only ever linked against.
    # This is an image that *is* linked against — `lib/libmariadb.so` is what a client extension
    # resolves — so it asks for the third thing, and asking for it in the recipe is what keeps the
    # two tables meaning what they say.
    changed = strip.symbols(tree, files, ["--strip-debug"], "linux")
    after = sum(path.stat().st_size for path in files)
    if changed:
        print(f"stripped debug symbols from {len(changed)} of {len(files)} files: "
              f"{before / 1e6:,.0f} MB of machine code became {after / 1e6:,.0f} MB")
    return changed


def unshippable_plugins(tree: Path) -> list[str]:
    """Drop the plugins that need a library this machine does not have, and name each one.

    **Written after the third plugin in a row stopped a build, one CI round each.** A MariaDB bintar
    is built on a machine with everything installed, so its plugin directory contains optional
    features linked against libraries a runner has never heard of: `cracklib_password_check.so`
    wants `libcrack.so.2`, and it is not the last of them. `relocate.bundle` refuses to continue —
    correctly, it cannot invent a library — so each one costs a round of CI to discover and a line
    to exclude.

    Asking every plugin what it needs, once, turns that loop into a single pass. A plugin whose
    dependency cannot be resolved *here* could not be loaded on a user's machine either: the archive
    would carry a file that `INSTALL SONAME` fails on with a message about a library nobody has. So
    it is not shipped, and `upstream.removed` says which.

    Deliberately only ``lib/plugin``. The same missing library under ``bin/`` is a server that cannot
    start, and that must remain a failure rather than becoming a deletion.
    """
    plugins = tree / "lib" / "plugin"
    if not plugins.is_dir():
        return []

    dropped = []
    for path in sorted(plugins.iterdir()):
        if path.is_symlink() or not path.is_file() or not relocate.kind(path):
            continue
        missing = [
            spelling
            for spelling, resolved in relocate.dependencies(path, tree / "bin", [tree / "lib"])
            if resolved is None and not relocate.is_system(spelling, resolved)
        ]
        if missing:
            print(f"not shipping {path.name}: it needs {', '.join(missing)}, which this machine "
                  f"does not have and a user's would not either")
            path.unlink()
            dropped.append(f"lib/plugin/{path.name}")
    return dropped


def plan(spec: str) -> list[str]:
    """Expand what a workflow was asked to build into the list of series to run.

    ``all`` is the reason this exists. MariaDB maintains four supported series at once, each with its
    own end-of-life years apart, and a user pinning 10.11 in a blueprint is as ordinary as one
    pinning 11.8 — so the workflow that publishes them has to be able to cover the whole catalogue in
    a run rather than being invoked four times and missing one.

    Only *series* are resolved here, never exact versions: each leg asks upstream for the newest
    patch of its series independently, which is the same rule the Caddy workflow follows and the
    reason a leg whose target has no build can end as an empty cell rather than as a failure.
    """
    stated = lines()
    if spec.strip() == "all":
        return [stated[key]["release_id"] for key in sorted(stated)]
    if spec.strip() == "latest":
        return [stated[max(stated)]["release_id"]]

    wanted = []
    for piece in spec.split(","):
        piece = piece.strip()
        if not piece:
            continue
        prefix = borrow.parts(piece)[:2]
        if not [key for key in stated if key[:2] == prefix]:
            raise SystemExit(
                f"downloads.mariadb.org lists no stable {piece}. It offers "
                f"{', '.join(stated[key]['release_id'] for key in sorted(stated))}."
            )
        # The piece as written, so an exact version stays exact and a series stays a series.
        wanted.append(piece)
    if not wanted:
        raise SystemExit("nothing to build: the version list is empty")
    return wanted


def prune(tree: Path) -> list[str]:
    """Take out what a database server does not need, and say what went.

    **Called by all three recipes, which it was not at first, and the artifacts showed it.** A
    borrowed bintar went through here; a `.deb` rearrangement and a source build each had a prune of
    their own that knew about layout and not about features. So Linux ARM64 kept the PAM plugins and
    the Galera scripts this list has excluded since the first round, and Windows ARM64 kept 21 MB of
    test binaries, 18 demonstration plugins and a 14 MB import library — one version meaning three
    different things depending on which cell a user installed from, which is precisely what the
    parity rule in the README exists to prevent. The layout-specific pruning stays with each recipe;
    what is *not shipped* is decided once, here.
    """
    removed = []
    for relative in PRUNE:
        path = tree / relative
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
            removed.append(relative)
        elif path.is_file():
            path.unlink()
            removed.append(relative)

    for pattern in GALERA + DEBRIS + NOT_SHIPPED:
        for path in sorted(tree.rglob(pattern)):
            # Only what is still there: a directory removed a moment ago takes its contents with it,
            # and listing each of those would turn `upstream.removed` into a file manifest.
            #
            # **`is_symlink` first, and it is not a refinement.** `exists()` follows the link, so a
            # symlink whose target an earlier pattern removed answers False and was skipped — which
            # is how `bin/mysql_ldb` shipped in every Linux artifact so far despite `mysql_ldb*`
            # having been on this list since the first round: `mariadb-ldb*` deleted its target one
            # pattern earlier and turned it invisible to its own rule.
            if not path.is_symlink() and not path.exists():
                continue
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink()
            removed.append(str(path.relative_to(tree)).replace("\\", "/"))

    # **What is left pointing at what just went.** A Linux bintar offers every tool under its old
    # `mysql*` name as a symlink beside the new one, so removing `bin/mariadb-test` leaves
    # `bin/mysqltest` aimed at nothing. Both spellings are named above, but a dangling link is the
    # kind of thing a pattern added later will produce again, and a file that resolves to nowhere is
    # a worse artifact than a missing one — it is a command that exists until a user runs it.
    for path in sorted(tree.rglob("*")):
        if path.is_symlink() and not path.exists():
            path.unlink()
            removed.append(str(path.relative_to(tree)).replace("\\", "/"))
    return removed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--version", required=True,
        help="a series (11.8), an exact version (11.8.8), or 'latest'",
    )
    parser.add_argument("--out", default=Path("dist"), type=Path)
    parser.add_argument(
        "--plan", action="store_true",
        help="print the series --version expands to, as JSON, and pack nothing. Used by the "
             "workflow to fan one run out over every supported series; accepts 'all'.",
    )
    arguments = parser.parse_args()

    if arguments.plan:
        print(json.dumps(plan(arguments.version)))
        return

    target = borrow.host("MariaDB")
    if target not in BORROWABLE:
        borrow.unavailable(
            f"downloads.mariadb.org publishes nothing for {target[0]}/{target[1]}: the catalogue "
            f"has only ever offered Linux and Windows on x86_64. macOS and both ARM64 cells are "
            f"built by mariadb_build.py, and Linux ARM64 is unpacked from .deb by mariadb_deb.py."
        )

    version, url, mirror, expected, eol = resolve(arguments.version, target)
    if version != arguments.version:
        print(f"{arguments.version} resolved to {version}")
    if eol:
        print(f"upstream supports this series until {eol}")

    work = Path(tempfile.mkdtemp(prefix="mixengine-mariadb-"))
    name = url.rsplit("/", 1)[-1]
    downloaded = work / name
    print(f"borrowing {url}")
    try:
        downloaded.write_bytes(borrow.fetch(url, timeout=900))
    except urllib.error.HTTPError as error:
        # The archive files a release the moment it is published, but a release published *this
        # minute* is the one case where it might not have it yet. Falling back to the redirector
        # keeps that from being a red cell; the checksum below is unchanged either way, so the worst
        # this can do is fail for the reason it already would have.
        if error.code != 404:
            raise SystemExit(f"{url} answered {error.code}") from error
        print(f"{url} answered 404; falling back to {mirror}")
        url = mirror
        try:
            downloaded.write_bytes(borrow.fetch(url, timeout=900))
        except urllib.error.HTTPError as second:
            raise SystemExit(f"{url} answered {second.code}") from second

    actual = borrow.sha256(downloaded)
    if actual != expected:
        raise SystemExit(
            f"{name} hashes to {actual}, and the REST API states {expected}. Either the download is "
            "damaged or it is not the file MariaDB published."
        )
    print(f"sha256 {actual} (verified against downloads.mariadb.org's REST API)")

    windows = target[0] == "windows"
    suffix = "zip" if windows else "tar.gz"
    tree = borrow.unpack(downloaded, work / "unpacked", suffix)

    removed = prune(tree)
    if removed:
        print(f"not shipping {len(removed)} paths: {', '.join(removed)}")
    if not windows:
        removed += unshippable_plugins(tree)
    changed = strip_debug(tree)

    provides = mariadb_smoke.describe(tree, windows)

    added: dict[str, Path] = {}
    if not windows:
        # The half of this recipe Caddy does not have. A borrowed bintar names its libraries by
        # soname with no search path of its own, so on a machine whose OpenSSL is a different
        # version — or which has no libaio at all — mariadbd does not start. Bundling makes the
        # archive answer for itself, and `smoke` proves it from a directory the tree has never seen.
        added = relocate.bundle(tree, search=[tree / "lib"])
        if added:
            print(f"bundled {len(added)} librar{'y' if len(added) == 1 else 'ies'}: "
                  f"{', '.join(sorted(added))}")
        # A bintar carries its own COPYING and THIRDPARTY at the root; what it does not carry is a
        # licence for the twenty-odd system libraries this recipe just put inside it.
        bundled_licences(tree, added)

    manifest = {
        "schema": 1,
        "kind": "mariadb",
        "version": version,
        "os": target[0],
        "arch": target[1],
        "source": "borrowed",
        "upstream": {
            "project": "MariaDB/server",
            "release": version,
            "url": url,
            "sha256": actual,
            "verified_against": "downloads.mariadb.org/rest-api (sha256) over HTTPS to the publisher",
        },
        "provides": provides,
    }
    if added:
        manifest["upstream"]["added"] = sorted(f"lib/{library}" for library in added)
    if removed:
        manifest["upstream"]["removed"] = sorted(removed)
    if changed:
        manifest["upstream"]["changed"] = dict(sorted(changed.items()))

    measured = relocate.floor(tree) if not windows else None
    if measured:
        manifest["requires"] = {measured[0]: measured[1]}
        print(f"needs {measured[0]} {measured[1]} or newer")

    elsewhere = borrow.moved(tree)
    if not windows:
        problems = relocate.verify(elsewhere)
        for problem in problems:
            print(f"error: {problem}", file=sys.stderr)
        if problems:
            raise SystemExit("the relocated tree reaches outside itself")
    manifest["smoke"] = {
        "relocated": True,
        "ran": mariadb_smoke.server(elsewhere, version, provides, windows),
    }
    borrow.discard(elsewhere)

    borrow.publish(tree, manifest, arguments.out, suffix)
    shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
