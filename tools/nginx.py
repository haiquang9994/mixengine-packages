#!/usr/bin/env python3
"""Borrow nginx on Windows, compile it on the four Unix cells, and make both mean the same thing.

**The shape is PHP's, and the reason is not the one the roadmap assumed.** P9 was written on the
understanding that nginx publishes a Windows zip and nothing relocatable elsewhere, which is true.
What it did not say is that upstream's Windows zip is the *whole answer to the hard question here*:
run ``nginx -V`` on it and it prints the configure line, and that line is a specification this
repository can compile against. The four Unix cells are built from it rather than from a taste in
modules — same twenty-two flags, same three libraries, same empty prefix — so a version of nginx
here means one set of directives on all six cells and it is upstream who decided which.

Four things came out of asking rather than assuming, and each one moved a decision.

*Upstream's Windows build is 32-bit x86, at every version it has ever published.* There is no
``-win64`` asset and never has been; ``nginx-1.31.3.zip`` is a ``PE32`` for ``i386``. So the
``windows``/``x86_64`` cell ships a 32-bit binary — which runs natively under WOW64, is what every
nginx-on-Windows user is already running, and is stated in this docstring and in the release notes
rather than left for somebody to discover in a PE header. The ``windows``/``aarch64`` cell is
**empty**: publishing an ``i386`` payload in an archive whose manifest says ``arch: aarch64`` would
be a lie in the index, and running it under emulation is not something to publish on the strength of
an assumption. Building nginx natively with MSVC for ARM64 is possible — unlike Redis, nginx has a
Windows build system, and it is the one upstream uses — but that is a compiler pipeline maintained
for every security release to fill one cell, which is exactly the trade *Borrow before you build*
tells this repository not to make.

*The floor is 1.26.0, and it was measured.* Every Windows build from 1.26.0 to 1.31.3 carries
**exactly the same twenty-two** ``--with-`` flags; 1.24.0 carries twenty, missing
``stream_realip`` and ``stream_ssl_preread``. A 1.24 row would therefore mean one thing on Windows
and another on the Unix cells this recipe configures, which is the first half of *One version means
one thing* broken by a version number. The second reason is worse: 1.24.0's zip is linked against
**OpenSSL 1.1.1t**, which stopped receiving public security fixes in September 2023, and that zip is
frozen — borrowing it means shipping that OpenSSL forever.

*nginx publishes no digest of anything.* Not a ``.sha256``, not a checksums file, not a line in an
API — 594 tarballs and 331 zips, each with a detached **PGP signature** and nothing else. Every
other recipe here checks a download against a digest its publisher states; keeping that property
means verifying the signature, so this one does. The trust anchor is
:data:`FINGERPRINTS`, five constants in this file: the keys are fetched from ``nginx.org/keys/``,
each is checked against its pinned fingerprint *before* it is imported, and a signature is accepted
only when gpg's machine-readable status output names one of them in a ``VALIDSIG``. Fetching a key
over the same HTTPS connection as the archive would prove nothing; a fingerprint somebody committed
is the thing an attacker on the wire cannot move.

*An empty prefix is upstream's own answer to relocation, and it does not mean the same thing on both
platforms.* ``--prefix=`` makes configure define ``NGX_PREFIX`` *not at all*, and nginx then takes
its prefix from ``-p`` or, failing that, from the working directory — which is what
`nginx.org/en/docs/windows.html <https://nginx.org/en/docs/windows.html>`_ describes and why nothing
has to be rewritten after the archive moves. The trap is in the sub-paths: with an empty prefix
``--conf-path=conf/nginx.conf`` compiles to ``/conf/nginx.conf``, and on Unix a leading slash *is* an
absolute path while on Windows ``ngx_test_full_name`` wants a drive letter and prepends the prefix
anyway. So the compiled-in default finds the config on Windows and looks in the filesystem root on
Unix. Nothing here relies on it: the contract, proven on every cell by :func:`smoke`, is

    ``nginx -p <instance> -c conf/nginx.conf -e stderr``

— a prefix, a *relative* config path that is therefore resolved against that prefix, and an error
log on stderr where a supervisor can capture what nginx says before it has read a config file. Those
three flags behave identically on all six cells, which is what makes the compiled-in defaults
irrelevant rather than merely unused.

Two things this deliberately does not do. It does not enable **HTTP/3**: QUIC is UDP, upstream's own
documentation says the Windows build has no UDP support at all, and a row where a ``listen ... quic``
works on four cells and is a parse error on the other two is the asymmetry this whole file is
arranged against. And it does not put an ``nginx.conf`` in the archive — configuration is generated
from state by ``core::generate`` and is disposable by design, which is the same argument
:mod:`caddy` makes and the reason the four ``conf/`` files that *do* ship are the ones a generated
config ``include``\\ s rather than the one it replaces.

Python 3 stdlib only, by policy: this runs on a GitHub runner with nothing installed.
"""

from __future__ import annotations

import argparse
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

DOWNLOAD = "https://nginx.org/download"
KEYS = "https://nginx.org/keys"

# The catalogue is the directory autoindex, because there is nothing else. nginx has no releases API,
# its GitHub mirror publishes no assets, and `nginx.org/en/download.html` lists only the newest few
# of each branch. The autoindex lists every file that exists, which is what a catalogue is.
INDEX = f"{DOWNLOAD}/"

# **The trust anchor, and the only reason the PGP check is worth making.** Fetching a key from the
# same host as the archive and then verifying one against the other proves that whoever served the
# page served both — which is true of an attacker who has the page. These are the fingerprints
# `nginx.org/en/pgp_keys.html` listed when this was written, committed here so that the check is
# against something in this repository rather than against something on the wire.
#
# Five files and seven keys, because a release is signed by whichever maintainer cut it: three of
# them appear across the range this recipe offers — Roman Arutyunyan, Sergey Kandaurov and
# Konstantin Pavlov, sometimes alternating between consecutive patches of one line — and the others
# are kept because artifacts already published carry their signatures, and a key removed here is a
# version that stops resolving.
#
# `nginx_signing.key` is **three** keys in one file rather than one, which is worth pinning
# individually rather than accepting whatever the file holds: a keyring served under the name of a
# key is exactly where an extra public key would go unnoticed. :func:`keyring` requires the file to
# carry these and *nothing else*, so a fourth signing key stops the run and a human decides whether
# it belongs — the same posture `WINDOWS_DISCARD` and :mod:`redis`'s licence table take.
FINGERPRINTS = {
    "arut.key": (
        "43387825DDB1BB97EC36BA5D007C8D7C15D87369",       # Roman Arutyunyan
    ),
    "nginx_signing.key": (
        "8540A6F18833A80E9C1653A42FD21310B49F6B46",       # nginx signing key 2
        "573BFD6B3D8FBC641079A6ABABF5BD827BD9BF62",       # nginx signing key
        "9E9BE90EACBCDE69FE9B204CBCDCD8A38D88A2B3",       # nginx signing key 3
    ),
    "pluknet.key": (
        "D6786CE303D9A9022998DC6CC8464D549AF75C0A",       # Sergey Kandaurov
    ),
    "sb.key": (
        "7338973069ED3F443F4D37DFA64FD5B17ADB39A8",       # Sergey Budnevitch
    ),
    "thresh.key": (
        "13C82A63B603576156E30A4EA0EA981B66B0D967",       # Konstantin Pavlov
    ),
}

# Every fingerprint above, as the one set :func:`signed` will accept a signature from.
PINNED = frozenset(fingerprint for group in FINGERPRINTS.values() for fingerprint in group)

# Where gpg is, in the order a machine is likely to have one. Tried rather than required by a single
# name, for the reason `borrow.seven_zip` gives about 7-Zip: the three runner families keep it in
# three places, and on Windows the one that exists belongs to Git rather than to the system.
GPG = (
    "gpg", "gpg2",
    "/usr/bin/gpg", "/opt/homebrew/bin/gpg", "/usr/local/bin/gpg",
    r"C:\Program Files\Git\usr\bin\gpg.exe",
    r"C:\Program Files (x86)\GnuPG\bin\gpg.exe",
)

# See the docstring: the oldest line whose Windows build carries the same modules as every later
# one, and the oldest that is not frozen against an end-of-life OpenSSL.
FLOOR = (1, 26)

# **The specification, read off upstream's own Windows binary and then imposed on the Unix build.**
# Identical on every published Windows zip from 1.26.0 to 1.31.3 — checked, not sampled — and
# :func:`check_modules` compares what each cell actually reports against it, so an upstream that
# changes its build stops this recipe rather than quietly publishing a row that means two things.
#
# `--with-debug` is in it and is not a debug build: it compiles in the `error_log ... debug` level,
# which costs binary size and is the difference between diagnosing a rewrite rule and guessing at
# one. It is here because upstream ships it on Windows, and a debug directive that works on one cell
# and is rejected on another is precisely what this table exists to prevent.
#
# `--with-mail` is in it for the same reason and for no other: a local web development environment
# has no use for an IMAP proxy. It cannot be dropped from the Unix side, because it cannot be
# dropped from the borrowed side.
MODULES = frozenset({
    "--with-debug",
    "--with-http_addition_module",
    "--with-http_auth_request_module",
    "--with-http_dav_module",
    "--with-http_flv_module",
    "--with-http_gunzip_module",
    "--with-http_gzip_static_module",
    "--with-http_mp4_module",
    "--with-http_random_index_module",
    "--with-http_realip_module",
    "--with-http_secure_link_module",
    "--with-http_slice_module",
    "--with-http_ssl_module",
    "--with-http_stub_status_module",
    "--with-http_sub_module",
    "--with-http_v2_module",
    "--with-mail",
    "--with-mail_ssl_module",
    "--with-stream",
    "--with-stream_realip_module",
    "--with-stream_ssl_module",
    "--with-stream_ssl_preread_module",
})

# Where the compiled-in paths point, copied from upstream's Windows configure line so that the two
# halves of the row differ in one entry, and that one only in spelling: `nginx` where upstream says
# `nginx.exe`. See the docstring for why none of these is load-bearing — `-p` and a *relative* `-c`
# override the two that matter, and the rest are defaults a generated configuration replaces.
PATHS = (
    "--prefix=",
    "--sbin-path=nginx",
    "--conf-path=conf/nginx.conf",
    "--pid-path=logs/nginx.pid",
    "--error-log-path=logs/error.log",
    "--http-log-path=logs/access.log",
    "--http-client-body-temp-path=temp/client_body_temp",
    "--http-proxy-temp-path=temp/proxy_temp",
    "--http-fastcgi-temp-path=temp/fastcgi_temp",
    "--http-scgi-temp-path=temp/scgi_temp",
    "--http-uwsgi-temp-path=temp/uwsgi_temp",
)

# **The same three versions upstream compiled into the Windows binary of the newest line**, which is
# the whole argument for pinning them here rather than linking whatever the runner has. nginx does
# not bundle these; it takes a *source* directory and compiles them into the binary itself, which is
# what makes a built cell as self-contained as the borrowed one and why `relocate.verify` has nothing
# to find. macOS ships no OpenSSL headers at all, so on two of the four cells there is no system
# alternative to compare against in any case.
#
# The digests were computed from the downloads and, where the publisher states one, checked against
# it: OpenSSL publishes a `.sha256` beside the release and it matches. PCRE2 and zlib publish
# signatures rather than digests, so theirs are this repository's own measurement of the bytes it
# intends to keep compiling — pinned so that a different tarball at the same URL stops the build.
LIBRARIES = {
    "pcre2": {
        "version": "10.47",
        "url": "https://github.com/PCRE2Project/pcre2/releases/download/pcre2-10.47/pcre2-10.47.tar.gz",
        "sha256": "c08ae2388ef333e8403e670ad70c0a11f1eed021fd88308d7e02f596fcd9dc16",
        # nginx compiles the PCRE2 sources directly rather than running its configure, and it looks
        # for `src/pcre2.h.generic` to decide that a directory is PCRE2 at all. That file is in the
        # release tarball and not in a git checkout, which is why this is a release URL.
        #
        # `LICENCE.md` and not `LICENCE`: PCRE2 renamed it, and the only reason that is written down
        # rather than discovered on a runner is that a licence is collected *after* OpenSSL has been
        # compiled. The two pieces of a licence row are the publisher's spelling and this
        # repository's, and they are different strings on three of these four.
        "licence": ("LICENCE.md", "pcre2-LICENCE"),
    },
    "zlib": {
        "version": "1.3.2",
        "url": "https://github.com/madler/zlib/releases/download/v1.3.2/zlib-1.3.2.tar.gz",
        "sha256": "bb329a0a2cd0274d05519d61c667c062e06990d72e125ee2dfa8de64f0119d16",
        "licence": ("LICENSE", "zlib-LICENSE"),
    },
    "openssl": {
        "version": "3.5.7",
        "url": "https://github.com/openssl/openssl/releases/download/openssl-3.5.7/openssl-3.5.7.tar.gz",
        "sha256": "a8c0d28a529ca480f9f36cf5792e2cd21984552a3c8e4aa11a24aa31aeac98e8",
        "licence": ("LICENSE.txt", "openssl-LICENSE"),
    },
}

# What nginx's own build passes OpenSSL, on top of the `no-shared no-threads` its makefile already
# supplies. `no-tests` is the one that matters: it is the second half of *no more than is needed*
# applied to a dependency, and it is most of the build time.
OPENSSL_OPT = "no-tests no-makedepend"

# The data files a generated nginx.conf `include`s, which is the whole of what `conf/` is for once
# the default configuration has been thrown out. `mime.types` is the one nothing works without;
# the other four are what a `fastcgi_pass` to PHP-FPM needs, which is the reason MixEngine renders
# an nginx config in the first place.
CONF_FILES = ("fastcgi.conf", "fastcgi_params", "mime.types", "scgi_params", "uwsgi_params")

# One layout on every cell, and it is upstream's rather than nginx's own `sbin/` convention —
# *Repack, do not rearrange*, which this row could have broken without noticing. The binary sits at
# the root of upstream's Windows zip; putting it in `sbin/` would have made the two halves of the
# row match each other by moving the borrowed one, and the whole reason that rule exists is that a
# borrowed archive is worth more when a reader can diff it against the publisher's. So the compiled
# cells come to *it*: `objs/nginx` is copied to the root, which is where `--sbin-path` would have
# put it under an empty prefix anyway. Caddy is laid out the same way for the same reason.
LAYOUT = {
    "windows": {"nginx": "nginx.exe"},
    "unix": {"nginx": "nginx"},
}

# The payload is at the root, so `relocate` has to be told to look there: its default is a list of
# subdirectories and a tree shaped like this one has none. See `relocate.machine_files`, where the
# argument exists precisely so a check cannot pass by having looked at nothing.
BINARIES = ("",)

# Everything statically compiled into the binary is redistributed by this archive, so its licence
# travels with it — the same rule :mod:`redis` applies to `deps/`, and upstream applies it too: the
# four files in the Windows zip's `docs/` are exactly these four.
#
# **The names are this repository's, not the publishers', and that is the point.** Upstream spells
# them `PCRE.LICENCE` on Windows and `LICENCE.md` in the PCRE2 tarball; a `licenses/` directory that
# inherited whichever spelling each half of the row happened to find would be a difference between
# cells that means nothing and that nobody could tell from one that means something. Both halves
# assemble :data:`LICENCES` and :func:`check_licences` refuses a tree that does not hold exactly it.
LICENCES = ("nginx-LICENSE", "openssl-LICENSE", "pcre2-LICENCE", "zlib-LICENSE")

WINDOWS_LICENCES = {
    "docs/LICENSE": "nginx-LICENSE",
    "docs/OpenSSL.LICENSE": "openssl-LICENSE",
    "docs/PCRE.LICENCE": "pcre2-LICENCE",
    "docs/zlib.LICENSE": "zlib-LICENSE",
}

# What the Windows zip holds that this archive does not, named so that :func:`borrowed` can insist
# every entry it found is either kept or on this list. A directory is named by its root, which is
# the spelling `upstream.removed` asks for.
#
# `conf/nginx.conf` is a default configuration and generated config is disposable — :mod:`caddy`
# refuses to ship one for the same reason. `koi-utf`, `koi-win` and `win-utf` are Cyrillic charset
# maps that only the default configuration references. `contrib` is vim syntax files and two Perl
# scripts; `docs` is 880 kB of changelog once its four licences have been lifted out; `html` is the
# welcome page and the 502 page a generated config replaces; `logs` and `temp` are empty directories
# nginx creates for itself under whatever prefix it is given.
WINDOWS_DISCARD = (
    "conf/koi-utf", "conf/koi-win", "conf/nginx.conf", "conf/win-utf",
    "contrib", "docs", "html", "logs", "temp",
)


def check_licences(tree: Path) -> None:
    """Refuse a tree whose ``licenses/`` is not exactly :data:`LICENCES`.

    Run on both halves of the row. nginx's binary statically links OpenSSL, PCRE2 and zlib on every
    cell, so all four texts are a condition of redistributing any of them — and a missing licence is
    the one defect in this repository that no smoke test could ever show, which is the whole of what
    the MariaDB row cost to learn.
    """
    shipped = sorted(path.name for path in (tree / "licenses").iterdir() if path.is_file())
    if shipped != sorted(LICENCES):
        raise SystemExit(
            f"licenses/ holds {', '.join(shipped) or 'nothing'} and this row ships "
            f"{', '.join(sorted(LICENCES))}"
        )
    print(f"shipping {len(shipped)} licence files: {', '.join(shipped)}")


def run(*command: str, cwd: Path | None = None, timeout: int = 5400) -> None:
    print("$ " + " ".join(str(part) for part in command), flush=True)
    result = subprocess.run([str(part) for part in command], cwd=cwd, timeout=timeout)
    if result.returncode != 0:
        raise SystemExit(f"{command[0]} exited {result.returncode}")


def jobs() -> str:
    return str(os.cpu_count() or 2)


def tell(program: Path, *args: str, path: str, timeout: int = 120) -> str:
    """Run *program* and return everything it said, both streams.

    :func:`borrow.run` returns stdout, and nginx writes ``-V``, ``-t`` and every diagnostic to
    **stderr** — so a check reading stdout would compare an empty string against a version number
    and be satisfied by it. Nothing about that would look wrong in a log.
    """
    environment = dict(os.environ) | {"PATH": path}
    result = subprocess.run(
        [str(program), *args], capture_output=True, text=True, timeout=timeout, env=environment,
    )
    said = f"{result.stdout}{result.stderr}".strip()
    if result.returncode != 0:
        raise SystemExit(f"{program.name} {' '.join(args)} exited {result.returncode}\n{said}")
    return said


# ----------------------------------------------------------------------------- the catalogue


def catalogue() -> dict[str, dict[tuple[int, ...], str]]:
    """Every release nginx has published at or above the floor, per archive format.

    Two maps rather than one, because the source tarball and the Windows zip are separate assets and
    a version that has only the first is an **empty cell on Windows and not a failure**. Every
    version from 1.26 up currently has both; that is checked here on every run rather than written
    down and left to go stale.
    """
    listing = borrow.fetch(INDEX).decode("utf-8", "replace")
    offered: dict[str, dict[tuple[int, ...], str]] = {"tar.gz": {}, "zip": {}}
    for name in re.findall(r'href="(nginx-[\d.]+\.(?:tar\.gz|zip))"', listing):
        match = re.fullmatch(r"nginx-(\d+\.\d+\.\d+)\.(tar\.gz|zip)", name)
        if not match:
            continue
        version, suffix = match.group(1), match.group(2)
        key = borrow.parts(version)
        if key[:2] < FLOOR:
            continue
        offered[suffix][key] = version

    if not offered["tar.gz"]:
        raise SystemExit(
            f"{INDEX} listed no nginx-<x.y.z>.tar.gz at or above "
            f"{'.'.join(str(part) for part in FLOOR)}; the autoindex changed shape"
        )
    return offered


def resolve(spec: str, suffix: str) -> str:
    """Turn ``1``, ``1.30``, ``1.30.4`` or ``latest`` into one published archive of *suffix*.

    ``latest`` is the newest release of any line, mainline included, which is upstream's own advice
    for anybody not running a distribution package: nginx backports nothing, so the newest mainline
    is the only build with every known fix. A user who wants the stable branch asks for it by
    number, and that is what the version argument is for.
    """
    offered = catalogue()
    published = offered[suffix]
    if spec == "latest":
        candidates = sorted(published)
    else:
        prefix = borrow.parts(spec)
        candidates = sorted(key for key in published if key[: len(prefix)] == prefix)

    if not candidates:
        # Sorted on the tuple: `1.30` is a later line than `1.9` and sorts before it as text, which
        # would print a range nobody could read.
        lines = [".".join(str(part) for part in line)
                 for line in sorted({key[:2] for key in offered["tar.gz"]})]
        raise SystemExit(
            f"nginx.org publishes no {spec} .{suffix} at or above "
            f"{'.'.join(str(part) for part in FLOOR)}. It offers {', '.join(lines)}."
        )
    return published[candidates[-1]]


# ----------------------------------------------------------------------------- the signature


def gpg() -> str:
    """The first gpg on this machine, or a refusal naming everywhere that was looked.

    **Never a shrug.** nginx states no digest anywhere, so a run without gpg is a run that would
    publish an archive nothing checked — and an unverified artifact that looks exactly like a
    verified one is worse than a failed job.
    """
    for candidate in GPG:
        found = shutil.which(candidate) or (candidate if Path(candidate).exists() else None)
        if found:
            return found
    raise SystemExit(
        "no gpg on this machine, and nginx publishes no digest of anything — only detached PGP "
        f"signatures. Looked for: {', '.join(GPG)}."
    )


def _gpg(program: str, home: Path, *args: str, stdin: bytes | None = None) -> str:
    """gpg against a keyring of our own, in the one spelling both gpg builds understand.

    ``--homedir .`` with the working directory set, rather than ``--homedir <absolute path>``,
    because the gpg that exists on a Windows runner is Git's — an MSYS program, which reads
    ``C:\\Users\\...`` as a *relative* path and prepends its own cwd to it. What comes out is
    ``/tmp/.../C:\\Users\\...`` and a fatal error about a keyblock resource, on a machine where
    everything else about the recipe worked. A relative homedir is correct for both.
    """
    result = subprocess.run(
        [program, "--batch", "--no-tty", "--homedir", ".", *args],
        cwd=str(home), input=stdin, capture_output=True, timeout=300,
    )
    return result.stdout.decode("utf-8", "replace")


def primaries(colons: str) -> list[str]:
    """The fingerprints of the *primary* keys in gpg's colon output, in order.

    Subkeys have ``fpr`` rows too, so reading every one of them would count an encryption subkey as
    a key this repository pinned. Only the first ``fpr`` after each ``pub`` is a primary.
    """
    found: list[str] = []
    expecting = False
    for row in colons.splitlines():
        fields = row.split(":")
        if fields[0] == "pub":
            expecting = True
        elif fields[0] == "fpr" and expecting:
            found.append(fields[9])
            expecting = False
    return found


def keyring(work: Path) -> tuple[str, Path]:
    """Fetch nginx's signing keys, refuse any file that is not exactly what is pinned, and import.

    The order is the point. A file is read and checked *before* it is imported, so a substituted one
    never reaches the keyring at all and a later ``--verify`` cannot be satisfied by it. The
    comparison is an equality rather than a membership test, because ``nginx_signing.key`` is a
    keyring of three and the interesting failure is a key **added** to it, not one missing.
    """
    program = gpg()
    home = work / "keys"
    home.mkdir(parents=True)
    os.chmod(home, 0o700)

    for name, pinned in sorted(FINGERPRINTS.items()):
        material = borrow.fetch(f"{KEYS}/{name}")
        offered = primaries(
            _gpg(program, home, "--show-keys", "--with-colons", "--with-fingerprint",
                 stdin=material)
        )
        if set(offered) != set(pinned):
            raise SystemExit(
                f"{KEYS}/{name} is not the key file this recipe pins.\n"
                f"  it carries: {', '.join(offered) or 'nothing gpg could read'}\n"
                f"  pinned:     {', '.join(pinned)}\n"
                f"Either nginx rotated a signing key or this is not nginx's key file, and telling "
                f"those apart is a person's job rather than this one's."
            )
        _gpg(program, home, "--import", stdin=material)

    imported = primaries(_gpg(program, home, "--list-keys", "--with-colons"))
    if set(imported) != PINNED:
        raise SystemExit(
            f"the keyring holds {len(imported)} keys and {len(PINNED)} were pinned: "
            f"{', '.join(sorted(set(imported) ^ PINNED))} is the difference"
        )
    print(f"gpg {program}: {len(imported)} pinned nginx keys imported")
    return program, home


def signed(program: str, home: Path, archive: Path, signature: Path) -> str:
    """Verify *archive* against *signature*, and answer with the fingerprint that signed it.

    Read from ``--status-fd`` rather than from the exit code alone. gpg exits 0 for a good signature
    from a key it does not *trust* — which is every key here, since the trust in this recipe comes
    from :data:`FINGERPRINTS` and not from a web of trust — and the machine-readable ``VALIDSIG``
    line is the only place the fingerprint that actually signed the bytes is stated. Requiring it to
    be one that was pinned closes the gap between "gpg was happy" and "the person who signs nginx
    signed this".
    """
    status = _gpg(program, home, "--status-fd", "1", "--verify", str(signature), str(archive))
    valid = [line.split()[2] for line in status.splitlines()
             if line.startswith("[GNUPG:] VALIDSIG ")]
    if len(valid) != 1 or valid[0] not in PINNED:
        raise SystemExit(
            f"{archive.name} is not signed by a key this recipe pins. gpg said:\n"
            + "\n".join(line for line in status.splitlines() if line.startswith("[GNUPG:]"))
        )
    return valid[0]


def download(program: str, home: Path, name: str, work: Path) -> tuple[Path, str, str]:
    """Fetch an nginx archive and its detached signature, and refuse to return an unverified one."""
    archive, signature = work / name, work / f"{name}.asc"
    url = f"{DOWNLOAD}/{name}"
    print(f"fetching {url}")
    try:
        archive.write_bytes(borrow.fetch(url, timeout=600))
        signature.write_bytes(borrow.fetch(f"{url}.asc", timeout=120))
    except urllib.error.HTTPError as error:
        raise SystemExit(f"{url} answered {error.code}") from error

    fingerprint = signed(program, home, archive, signature)
    digest = borrow.sha256(archive)
    who = next(file for file, group in FINGERPRINTS.items() if fingerprint in group)
    print(f"{name}: good signature from {who} ({fingerprint})")
    print(f"sha256 {digest} (computed here; nginx publishes no digest of anything)")
    return archive, digest, url


# ----------------------------------------------------------------------------- the two payloads


def borrowed(program: str, home: Path, version: str, work: Path) -> tuple[Path, dict, dict]:
    """Repack upstream's Windows zip into this repository's layout.

    Answers ``(tree, upstream block, lacks block)``. Everything upstream shipped is either copied
    into the new tree or named in :data:`WINDOWS_DISCARD`, and an entry in neither **stops the
    pack** — an archive that grew a directory since this was written is a change worth reading
    before it is republished, not one to pass through.
    """
    name = f"nginx-{version}.zip"
    archive, digest, url = download(program, home, name, work)
    unpacked = borrow.unpack(archive, work / "unpacked", "zip")

    tree = work / "tree"
    tree.mkdir()
    shutil.copy2(unpacked / "nginx.exe", tree / "nginx.exe")

    (tree / "conf").mkdir()
    for conf in CONF_FILES:
        source = unpacked / "conf" / conf
        if not source.is_file():
            raise SystemExit(f"upstream's zip has no conf/{conf}; a generated config includes it")
        shutil.copy2(source, tree / "conf" / conf)

    (tree / "licenses").mkdir()
    for relative, shipped in WINDOWS_LICENCES.items():
        source = unpacked / relative
        if not source.is_file():
            raise SystemExit(
                f"upstream's zip has no {relative}. Its binary statically links OpenSSL, PCRE2 and "
                f"zlib, so all four licences have to travel with it — upstream moved one."
            )
        shutil.copy2(source, tree / "licenses" / shipped)
    check_licences(tree)

    # Nothing unaccounted for. `kept` is spelled against the *upstream* tree rather than the new one
    # so that a file this recipe stopped copying shows up as unaccounted rather than as absent.
    kept = {"nginx.exe", *(f"conf/{conf}" for conf in CONF_FILES), *WINDOWS_LICENCES}
    accounted = kept | set(WINDOWS_DISCARD)
    unexpected = []
    # Files only. A directory is accounted for by its contents, and the two that have none —
    # `logs/` and `temp/`, which nginx creates for itself under whatever prefix it is given — are
    # covered by the staleness check below instead.
    for path in sorted(unpacked.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(unpacked).as_posix()
        if relative in accounted or any(relative.startswith(f"{root}/") for root in accounted):
            continue
        unexpected.append(relative)
    if unexpected:
        raise SystemExit(
            f"upstream's zip carries {', '.join(unexpected[:12])} — neither kept nor discarded by "
            f"this recipe. Decide which, rather than letting the archive decide."
        )

    # Checked the other way too: a discard naming something upstream stopped shipping is a claim
    # that has outlived its subject, which is the shape of declaration `borrow.declare` refuses.
    absent = [relative for relative in WINDOWS_DISCARD if not (unpacked / relative).exists()]
    if absent:
        raise SystemExit(
            f"WINDOWS_DISCARD names {', '.join(absent)}, which upstream's zip does not contain — "
            f"the removal is being declared for something nobody removed"
        )

    upstream = {
        "url": url,
        "sha256": digest,
        "verified_against": (
            "the detached PGP signature nginx publishes beside it, against a key fingerprint "
            "pinned in tools/nginx.py — nginx states no digest of any of its archives"
        ),
        # `nginx.exe` and the five `conf/` files are not in either list: they ship at the path
        # upstream published them, with the bytes upstream published, which is what makes the word
        # *borrowed* mean something. The four licences are `added` only because they moved out of
        # `docs/` — the same texts, collected where every other archive here keeps them.
        "added": [f"licenses/{shipped}" for shipped in WINDOWS_LICENCES.values()],
        "removed": list(WINDOWS_DISCARD),
    }

    # **The one field in this repository that is an admission**, and upstream writes it rather than
    # this recipe: nginx.org calls its own Windows build a beta and says why. A daemon reading this
    # can decline to render `worker_processes auto;` on a cell where the extra workers do nothing,
    # instead of rendering it everywhere and being slower on one platform for no visible reason.
    lacks = {
        "workers": (
            "Upstream's Windows build starts as many workers as it is asked for and only one of "
            "them does any work; the compiled cells use one per core."
        ),
        "udp": (
            "No UDP, so no `listen ... udp` in a stream block. QUIC is out of reach on every cell "
            "of this row, but on Windows it is out of reach of the platform rather than of a flag."
        ),
        "connection_processing": (
            "select() and poll() only — no epoll, kqueue or I/O completion ports — which is why "
            "upstream describes this build as a beta rather than as slower."
        ),
        "bitness": (
            "A 32-bit x86 binary, running under WOW64. Upstream has never published a 64-bit "
            "Windows build of nginx; there is no --win64 asset in any of its 331 zips."
        ),
    }
    return tree, upstream, lacks


def build(program: str, home: Path, version: str, work: Path) -> tuple[Path, str]:
    """Compile nginx against upstream's own configure line, and answer with what was asked for.

    The three libraries are handed to nginx as *source directories* rather than built first and
    linked: ``--with-pcre``, ``--with-zlib`` and ``--with-openssl`` make nginx's own build compile
    them in, which is what upstream's Windows build does and therefore the one path that produces
    the same binary shape on both halves of the row.
    """
    name = f"nginx-{version}.tar.gz"
    archive, digest, url = download(program, home, name, work)
    with tarfile.open(archive) as tarred:
        tarred.extractall(work, filter="data")
    source_tree = work / f"nginx-{version}"
    if not (source_tree / "configure").is_file():
        raise SystemExit(f"{source_tree} has no configure; this is not an nginx release tarball")

    unpacked: dict[str, Path] = {}
    for library, described in LIBRARIES.items():
        tarball = work / f"{library}.tar.gz"
        print(f"fetching {described['url']}")
        tarball.write_bytes(borrow.fetch(described["url"], timeout=1800))
        actual = borrow.sha256(tarball)
        if actual != described["sha256"]:
            raise SystemExit(
                f"{library} {described['version']} hashes to {actual}, this recipe pins "
                f"{described['sha256']} — the bytes at that URL are not the bytes it was written "
                f"against"
            )
        into = work / "libraries" / library
        with tarfile.open(tarball) as tarred:
            tarred.extractall(into, filter="data")
        entries = [path for path in into.iterdir() if path.is_dir()]
        if len(entries) != 1:
            raise SystemExit(f"{library}'s tarball holds {[p.name for p in into.iterdir()]}")
        unpacked[library] = entries[0]
        print(f"  {library} {described['version']} verified against the pinned digest")

    configure = [
        "./configure",
        *PATHS,
        f"--with-pcre={unpacked['pcre2']}",
        f"--with-zlib={unpacked['zlib']}",
        f"--with-openssl={unpacked['openssl']}",
        f"--with-openssl-opt={OPENSSL_OPT}",
        *sorted(MODULES),
    ]
    run(*configure, cwd=source_tree)
    run("make", f"-j{jobs()}", cwd=source_tree)

    produced = source_tree / "objs" / "nginx"
    if not produced.is_file():
        raise SystemExit("make finished and there is no objs/nginx")

    # **Assembled by hand, and that is forced rather than chosen.** `--prefix=` is what makes the
    # binary carry no path, and `make install` with an empty prefix would install into the root of
    # the filesystem — `auto/install` joins every relative sub-path onto the prefix, so an empty one
    # turns `sbin/nginx` into `/sbin/nginx`. Copying out what ships is the only install this
    # configuration has, and it is also how the tree comes to match the borrowed cell's.
    tree = work / "tree"
    tree.mkdir()
    shutil.copy2(produced, tree / "nginx")

    (tree / "conf").mkdir()
    for conf in CONF_FILES:
        shutil.copy2(source_tree / "conf" / conf, tree / "conf" / conf)

    (tree / "licenses").mkdir()
    shutil.copy2(source_tree / "LICENSE", tree / "licenses" / "nginx-LICENSE")
    for library, described in LIBRARIES.items():
        published, ships_as = described["licence"]
        text = unpacked[library] / published
        if not text.is_file():
            raise SystemExit(
                f"{library} {described['version']} has no {published}, and its code is compiled "
                f"into sbin/nginx — upstream moved the file and the row has to move with it"
            )
        shutil.copy2(text, tree / "licenses" / ships_as)
    check_licences(tree)

    libraries = ", ".join(
        f"{library} {described['version']}" for library, described in sorted(LIBRARIES.items())
    )
    return tree, (
        f"nginx-{version}.tar.gz from source (sha256 {digest[:12]}…, signature verified against a "
        f"pinned nginx key); upstream's own Windows configure line transposed — {libraries} "
        f"compiled in statically, empty prefix, no HTTP/3; {url}"
    )


# ----------------------------------------------------------------------------- the proof


def check_modules(said: str, where: str) -> None:
    """Compare what a binary reports against :data:`MODULES`, and stop the pack on any difference.

    Run against **both** halves of the row, which is what makes it worth having. On the borrowed
    cell it catches upstream changing its build; on a compiled cell it catches this recipe's
    configure line drifting from the specification it was copied out of. A check that only looked at
    one of them could not tell a row that means one thing from a row that means two.
    """
    reported = {token for token in said.split() if token.startswith("--with-") and "=" not in token}
    if reported != set(MODULES):
        gained = sorted(reported - MODULES)
        lost = sorted(MODULES - reported)
        raise SystemExit(
            f"{where} reports a different module set from every other cell of this row.\n"
            f"  it has and they do not: {', '.join(gained) or 'nothing'}\n"
            f"  they have and it does not: {', '.join(lost) or 'nothing'}\n"
            f"Decide which set the row means and change MODULES, rather than publishing both."
        )
    print(f"{where}: the same {len(MODULES)} modules as every other cell")


def free_port() -> int:
    """A port nothing is listening on, as the kernel's answer rather than as a guess.

    Racy in principle — it is closed before nginx binds it — and the alternative is a hard-coded 80,
    which needs privileges on some of these machines and is taken on the rest.
    """
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def served(port: int, timeout: float = 5) -> str:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=timeout) as response:
        return response.read().decode("utf-8", "replace")


def await_answer(port: int, expected: str, process: subprocess.Popen | None, log: Path,
                 seconds: float = 30) -> None:
    """Wait until the server answers *expected*, or say what it said instead.

    Used for the start **and for the reload**, and the reload is why it waits for a body rather than
    for a connection: ``nginx -s reload`` returns as soon as the master has read the new
    configuration, while the old workers finish what they were doing and the new ones come up behind
    them. A check that read one response immediately afterwards would be reading whichever worker
    answered, and would pass either way about half the time.
    """
    deadline = time.monotonic() + seconds
    last = ""
    while time.monotonic() < deadline:
        if process is not None and process.poll() is not None:
            raise SystemExit(
                f"nginx exited {process.returncode} before it answered\n"
                f"{log.read_text(encoding='utf-8', errors='replace')}"
            )
        try:
            last = served(port, timeout=2)
            if last == expected:
                return
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as refusal:
            last = str(refusal)
        time.sleep(0.2)
    if process is not None and process.poll() is None:
        process.kill()
    raise SystemExit(
        f"127.0.0.1:{port} answered {last!r}, expected {expected!r}\n"
        f"{log.read_text(encoding='utf-8', errors='replace')}"
    )


def configuration(port: int, body: str, artifact: Path) -> str:
    """A configuration in the shape ``core::generate`` will render one.

    ``include`` of the archive's own ``mime.types`` by absolute path is the part worth exercising:
    it is how a generated configuration reaches a data file that lives in the artifact rather than
    beside the config, and a `provides` entry pointing at the wrong place would fail here instead of
    on a user's first site.

    The path is **quoted**, and it has to be: :func:`borrow.moved` puts the tree under a directory
    whose name contains a space on purpose, and an unquoted nginx directive stops at the first one.
    Forward slashes for the same reason on both platforms — nginx on Windows wants a configuration
    written in UNIX style, which is upstream's own instruction.
    """
    return (
        f"daemon off;\n"
        f"worker_processes 1;\n"
        f"pid logs/nginx.pid;\n"
        f"events {{ worker_connections 64; }}\n"
        f"http {{\n"
        f"    include \"{(artifact / 'conf' / 'mime.types').as_posix()}\";\n"
        f"    default_type application/octet-stream;\n"
        f"    access_log off;\n"
        f"    server {{\n"
        f"        listen 127.0.0.1:{port};\n"
        f"        location / {{ return 200 \"{body}\"; }}\n"
        f"    }}\n"
        f"}}\n"
    )


def smoke(tree: Path, version: str, provides: dict[str, str], operating_system: str) -> dict:
    """Run the artifact from somewhere it has never been, and make it be a *web server* while there.

    The five things MixEngine will do to an nginx, in the order it will do them, and one of them is
    not available to :mod:`caddy`'s equivalent: **nginx has no admin endpoint**, so a reload is
    ``-s reload`` against a running master and there is no configuration to read back afterwards to
    prove it took. What proves it is the request. The configuration is rewritten between the two
    reloads so that the body changes, and the check waits for the *new* body — a reload that did
    nothing leaves the old one being served indefinitely, which is a pass for any check that only
    asks whether the process survived.
    """
    elsewhere = borrow.moved(tree)

    if operating_system != "windows":
        problems = relocate.verify(elsewhere, directories=BINARIES)
        for problem in problems:
            print(f"error: {problem}", file=sys.stderr)
        if problems:
            raise SystemExit("the relocated tree reaches outside itself")

    nginx = elsewhere / provides["nginx"]
    path = borrow.clean_path(nginx.parent)

    banner = tell(nginx, "-V", path=path)
    if f"nginx/{version}" not in banner:
        raise SystemExit(f"nginx reports {banner.splitlines()[0]!r}, expected nginx/{version}")
    check_modules(banner, f"{operating_system} nginx {version}")

    openssl = re.search(r"built with (\S+ [\w.]+)", banner)
    if not openssl:
        raise SystemExit(
            f"nginx -V does not say what TLS library it was built with, and --with-http_ssl_module "
            f"is in the module set. It said:\n{banner}"
        )
    print(f"nginx -V: {banner.splitlines()[0]}, {openssl.group(1)}")

    port = free_port()
    instance = elsewhere.parent / "instance"
    # **Both of these have to exist before nginx starts, and finding that out is what this test is
    # for.** `logs/` is where the pid file goes and nginx never creates it. `temp/` is subtler: nginx
    # does create its own `temp/client_body_temp` and the four beside it — but with a single
    # `CreateDirectory`/`mkdir`, not a chain, so a missing parent is `[emerg] ... failed (3: The
    # system cannot find the path specified)` and a configuration that tested fine a line earlier.
    # Staging an instance directory therefore means making both, and the daemon will have to.
    for directory in ("conf", "logs", "temp"):
        (instance / directory).mkdir(parents=True, exist_ok=True)
    config = instance / "conf" / "nginx.conf"

    first = f"mixengine {version} on {operating_system} before reload"
    second = f"mixengine {version} on {operating_system} after reload"
    config.write_text(configuration(port, first, elsewhere), encoding="utf-8")

    # The contract, in full, on every cell: a prefix, a config path *relative to it*, and an error
    # log on stderr. See the module docstring for why the compiled-in defaults cannot be used.
    invocation = ["-p", str(instance), "-c", "conf/nginx.conf", "-e", "stderr"]

    print(tell(nginx, *invocation, "-t", path=path))
    print("nginx -t: accepted the rendered configuration")

    log = instance / "nginx.log"
    environment = dict(os.environ) | {"PATH": path}
    with log.open("wb") as sink:
        process = subprocess.Popen(
            [str(nginx), *invocation], stdout=sink, stderr=subprocess.STDOUT,
            env=environment, cwd=str(instance),
        )

    try:
        await_answer(port, first, process, log)
        print(f"nginx served: {first}")

        config.write_text(configuration(port, second, elsewhere), encoding="utf-8")
        tell(nginx, *invocation, "-s", "reload", path=path)
        await_answer(port, second, process, log)
        print(f"nginx -s reload: {second}")

        tell(nginx, *invocation, "-s", "quit", path=path)
        try:
            process.wait(timeout=60)
        except subprocess.TimeoutExpired:
            process.kill()
            raise SystemExit("nginx -s quit returned and the master was still running") from None
        print("nginx -s quit: the master exited")
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=30)

    borrow.discard(elsewhere)
    return {
        "relocated": True,
        "openssl": openssl.group(1),
        "ran": [
            f"{provides['nginx']} -V, checked against the {len(MODULES)} modules this row carries",
            "nginx -t against a rendered configuration including the archive's own mime.types",
            "nginx -p <prefix> -c conf/nginx.conf -e stderr, a request served",
            "the configuration rewritten, nginx -s reload, the new body served",
            "nginx -s quit",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--version", required=True,
        help="exact version (1.30.4), a line (1 or 1.30) for its newest release, or 'latest'",
    )
    parser.add_argument("--out", default=Path("dist"), type=Path)
    arguments = parser.parse_args()

    operating_system, arch = borrow.host("nginx")
    if operating_system == "windows" and arch != "x86_64":
        borrow.unavailable(
            "nginx publishes one Windows build and it is 32-bit x86 — there is no --win64 asset in "
            "any of its 331 zips, at any version. Publishing that payload in an archive whose "
            "manifest says arch: aarch64 would be a lie in the index, and running it under "
            "emulation is not something to claim without a machine to prove it on. nginx does have "
            "an MSVC build system, unlike Redis, so this cell could be compiled — that is a "
            "toolchain maintained for every security release to fill one cell, which is the trade "
            "'borrow before you build' exists to refuse. This cell is empty and the index says so."
        )

    work = Path(tempfile.mkdtemp(prefix="mixengine-nginx-"))
    program, home = keyring(work)

    suffix = "zip" if operating_system == "windows" else "tar.gz"
    version = resolve(arguments.version, suffix)
    if version != arguments.version:
        print(f"{arguments.version} resolves to nginx {version}")
    print(f"packing nginx {version} for {operating_system}/{arch}")

    manifest: dict = {
        "schema": 1,
        "kind": "nginx",
        "version": version,
        "os": operating_system,
        "arch": arch,
    }

    if operating_system == "windows":
        tree, upstream, lacks = borrowed(program, home, version, work)
        manifest["source"] = "borrowed"
        manifest["upstream"] = {
            key: value for key, value in upstream.items() if key not in ("added", "removed")
        }
        manifest["lacks"] = lacks
    else:
        tree, recipe = build(program, home, version, work)
        manifest["source"] = "built"
        manifest["recipe"] = recipe
        upstream = {}

    provides = dict(LAYOUT["windows" if operating_system == "windows" else "unix"])
    provides |= {conf: f"conf/{conf}" for conf in CONF_FILES}
    missing = sorted(name for name, relative in provides.items() if not (tree / relative).is_file())
    if missing:
        raise SystemExit(f"the tree provides no {', '.join(missing)}")
    manifest["provides"] = provides

    if upstream:
        borrow.declare(tree, manifest, added=upstream["added"], removed=upstream["removed"])

    measured = relocate.floor(tree, directories=BINARIES) if operating_system != "windows" else None
    if measured:
        manifest["requires"] = {measured[0]: measured[1]}
        print(f"needs {measured[0]} {measured[1]} or newer")

    manifest["smoke"] = smoke(tree, version, provides, operating_system)

    borrow.publish(tree, manifest, arguments.out, "zip" if operating_system == "windows" else "tar")
    shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
