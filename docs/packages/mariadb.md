# MariaDB, where the table was wrong

*Part of [mixengine-packages](../../README.md), which holds the table of what is packaged.*

[Caddy](caddy.md) is what a borrow looks like when it works. MariaDB is what the rule is *for*:
MixEngine's runtime table said "official zip / official tarball / official tarball" for all three
systems, and asking the catalogue rather than assuming it — the whole point of "borrow costs one
evaluation" — answered something else. Every release from 10.2 to 13.1 offers Linux and Windows on
x86_64 and nothing else. There has never been a macOS build, and there is no ARM64 tarball of any kind.

| OS / arch | Range | How |
| --- | --- | --- |
| Windows x86_64 | **10.6 – newest** | **borrowed** — official `mariadb-<version>-winx64.zip`, repacked |
| Linux x86_64 | **10.6 – newest** | **borrowed** — the official `linux-systemd-x86_64` bintar |
| Linux aarch64 | **10.6 – newest** | **borrowed, rearranged** — upstream's own `arm64` `.deb` packages, laid out as its bintar is |
| macOS aarch64, x86_64 | **10.6 – newest** | **built** — upstream publishes no macOS binary and never has |
| Windows aarch64 | **10.6 – newest** | **built** — natively, on an ARM64 runner |

Three things follow from that shape.

**A borrowed MariaDB is not self-contained**, which no other borrow here has to deal with. Caddy is
one static Go binary; a bintar is a hundred programs and a plugin directory naming `libssl.so.3`,
`libaio`, `libnuma` and `libsystemd` by soname with no search path of its own. On a machine whose
OpenSSL is a different version the server does not start, and the error names a file nobody
installed. So `relocate.bundle` — written for the *built* cells — runs over a borrowed tree here, and
`upstream.added` records every library it put in.

**The ARM64 Linux cell is a rearrangement rather than a build.** The payload is upstream's, compiled
by upstream, taken from upstream's own repository and checked against the SHA256 in its `Packages`
index; only the shape is this repository's, and the shape it is given is upstream's own bintar
layout, so one tree comes out of all three routes. The packages are taken from the `jammy` suite on a
22.04 runner because the glibc floor of the artifact is the highest floor of anything in it — noble
packages would publish something that refuses to start on Debian 12.

**The end-of-life dates come from the publisher.** MariaDB states `release_eol_date` per series
through its REST API, so `data/eol.json`'s MariaDB entry is the one in that file transcribed from an
API rather than from a schedule page, and `tools/mariadb.py` prints what it saw on every run.

The proof is the same shape as Caddy's and asks the questions a *database* raises rather than a web
server: the artifact is moved somewhere it has never been, `mariadb-install-db` bootstraps a data
directory from scratch, the server starts against a rendered `my.cnf`, `mariadb-admin ping` answers,
a row is written and read back **through InnoDB** — checked in `information_schema`, because a server
whose storage engine failed to initialise falls back without failing — and it is stopped through
`mariadb-admin shutdown`, with the clean-shutdown line looked for in the log afterwards. A supervisor
that kills a database instead leaves crash recovery for the user's first start.

Two platform differences this turned up, neither of them guessable from documentation:
`mariadb-install-db` is a **different program** on Windows — a C++ one, not the Unix shell script,
sharing almost none of its options — and Windows mariadbd writes its error log to a file in the data
directory and sends nothing to stdout, so a check reading the process's own output concludes the
server said nothing.
