# PostgreSQL, where most of the download is not a database

*Part of [mixengine-packages](../../README.md), which holds the table of what is packaged.*

[MariaDB's evaluation](mariadb.md) found the table wrong about *which* platforms upstream builds
for. PostgreSQL's finds something else: the project itself publishes **no binaries at all**, only
source. What the download page points at on Windows and macOS is EnterpriseDB's, and what EDB
publishes is an installer's payload rather than a server.

| OS / arch | Range | How |
| --- | --- | --- |
| Windows x86_64 | **14 – newest** | **borrowed** — EDB's `postgresql-<version>-<n>-windows-x64-binaries.zip` |
| macOS aarch64, x86_64 | **14 – newest** | **borrowed, thinned** — one **universal** `osx-binaries.zip` serving both cells, each keeping its own slice |
| Linux x86_64, aarch64 | **14 – newest** | **borrowed, rearranged** — the project's own `.deb` packages from `apt-archive.postgresql.org` |
| Windows aarch64 | — | **upstream does not compile there** before PostgreSQL 19 |

**The floor is where the archive changes shape.** EDB's macOS zip for 13 is a thin x86_64 Mach-O and
from 14 on it is universal, so a 13 packed here would mean *Intel* on a row where every other version
means both architectures — one version meaning two things, decided by which cell a user installed
from. PostgreSQL 13 also went out of support in November 2025, so upstream's answer and the
catalogue's shape agree on where to stop.

**Most of what is downloaded is never written to disk.** The Windows zip unpacks to 914 MB, of which
717 MB is pgAdmin 4 — an Electron application with its own Python — beside StackBuilder, a downloader
for more EDB software. The macOS zip is 1,215 MB with the same two inside. Neither is a thing
MixEngine installs, so they are skipped *during* unpacking rather than deleted afterwards, and the
second reason is better than the first: `pgsql/pgAdmin 4/python/Lib/site-packages/azure/mgmt/rdbms/…`
is past `MAX_PATH`, and extracting the archive whole dies half way through with a `FileNotFoundError`
naming a file whose only problem is the length of its name. Every skipped root is still listed in
`upstream.removed` — "never unpacked" and "deleted" are the same difference to a reader holding both
archives.

What else does not ship is decided by the rule rather than by size: the headers and the static and
import libraries, PGXS and its `pkgconfig` file, `pg_config` and `ecpg` — without `include/` there is
nothing for any of them to compile — the test modules that live beside the real ones, EDB's own
`plugin_debugger` and `system_stats`, and 14 MB of wxWidgets DLLs sitting in `bin/` for
StackBuilder's window. **And the procedural languages that are not PostgreSQL's own**: `plperl`,
`plpython3u` and `pltcl` each need an interpreter installed on the user's machine, so `CREATE
EXTENSION plperl` on a clean one fails with a message about a missing library — and Debian packages
each separately, so the Linux cells could not have had them either. `plpgsql` is compiled into the
server and stays. 344 MB of download becomes a 38 MB artifact with 46 extensions in it.

**The version catalogue and the end-of-life dates are one document.** `postgresql.org/versions.json`
states every major, its newest minor, whether it is supported and the day support ends — so
`data/eol.json`'s `postgres` block is transcribed from a publisher rather than from a schedule page,
as MariaDB's is, and `tools/postgres.py` prints the date it saw on every run.

**EDB publishes no checksum, and the manifest says so in those words.** Every other borrow here is
checked against a digest its publisher states; `get.enterprisedb.com` answers 403 to `.sha256` and
`.md5` beside every archive it serves. What is left is TLS to the publisher's own host with no mirror
redirector in between — which is exactly what `tools/ruby.py` records when RubyInstaller publishes no
checksum file. `upstream.verified_against` reads *"HTTPS to get.enterprisedb.com; EDB publishes no
checksum for these archives"*, because an artifact that implied otherwise would be making the one
claim a reader cannot check.

The proof asks a database's questions, as MariaDB's does: `initdb` bootstraps a cluster with a
password-authenticated superuser, the server starts against a rendered `postgresql.conf` — never one
written into the data directory, so generated configuration stays disposable — `pg_isready` answers,
a row is written and read back, and **`hstore` and `pgcrypto` are created and used**. That last one
is the check on the pruning: an extension needs a module in the library directory *and* a control
file in the share tree, the two live in different halves of the archive, and `pgcrypto`'s `digest()`
is computed by the OpenSSL travelling inside it. Then `pg_ctl stop -m fast`, with the
clean-shutdown line looked for in the log.

One finding worth stating on its own, because it is invisible from a build log. Run without a stated
locale on a machine whose system locale is Vietnamese, `initdb` reports *could not find suitable text
search configuration* and quietly sets the default to `simple` — a cluster where full-text search
does not stem — **and exits zero**. The same artifact on two developers' machines then produces two
databases that answer differently. The check states `--locale=C -E UTF8` so it is reproducible; what
that teaches the daemon is that it has to choose, because the default is whatever the machine is.

## The macOS cells, where one archive is written down three times

A universal build saves a download and spends it on the disk. After the roots above are skipped and
the pruning has run, the macOS tree is **362 MB** — nine times the Windows artifact of the same
version — and 199 MB of that is the same bytes written more than once.

Two different duplications, and only one of them is inherent. 161 MB is machine code compiled twice,
once per architecture, of which each cell can execute one copy; that is what a universal binary is.
The other 38 MB is a packaging accident: EDB ships each dylib's version chain as whole copies, so
`libicudata.dylib`, `libicudata.77.dylib` and `libicudata.77.1.dylib` are three identical 64 MB files
where an ordinary ICU install is one file and two links — and the archive does know how to store a
link, it holds 78 of them, all inside pgAdmin. So the chain is put back and the slice this cell cannot
run is dropped: **362 MB becomes 82 MB**, with nothing taken out that a database opens.

The slice is lifted out as a byte range read from the file's own fat header, and **the proof that the
copy was exact is the signature EDB already put on it**. Every one of the 173 binaries carries an
ad-hoc code signature whose CodeDirectory holds a SHA-256 of each 4 KB page, `tools/strip.py`
recomputes all of them against the bytes now on disk, and an extraction off by one byte fails there —
on arm64 it would otherwise fail at run time as a `SIGKILL` with nothing printed. That is why no
re-signing is needed and none is done: the shipped file is bytes EDB compiled and signed, and
`upstream.changed` names all 173 paths and which of the two things happened to each.

The quieter half is a correctness one. `otool` reports a universal binary's load commands **once per
architecture**, so `relocate.verify` and `relocate.floor` had been reading two machines at once and
answering with the stricter of them: the macOS floor a user saw was the higher of the two builds'
minimums, whichever cell they installed. Thinning first means each cell measures the binaries it
actually ships.

## The Linux cells, where the publisher is the project and the layout may not be touched

EDB never built the Linux tarball the runtime table promised, so both Linux cells come from
`apt.postgresql.org` instead — run by the same people who tag the releases, built for `amd64` and
`arm64` alike, and **better checked than the archives above**: a `Release` file states the digest of
the package index and the index states the digest of every package, so both links are followed where
EDB offers none at all. The packages are taken from `apt-archive.postgresql.org`, because the live
repository keeps roughly the last three minors of a major and drops the rest, and this index promises
that a blueprint pinning 18.4 keeps working. The same trade `mariadb_deb.py` makes between
`deb.mariadb.org` and `archive.mariadb.org`.

**Where this reverses MariaDB's answer is the layout.** MariaDB's `.deb` route rearranges upstream's
packaging into upstream's own bintar shape, because a MariaDB is *told* where it lives —
`--basedir`. PostgreSQL is told nothing and works it out, in `make_relative_path` in
`src/port/path.c`: it strips the shared part of the `bindir` and `sharedir` compiled into the binary
and then requires the directory it is actually running from to **end in what is left**. Debian
configures `--bindir=/usr/lib/postgresql/18/bin`, so what is left is `lib/postgresql/18/bin`, and a
`postgres` moved to a plain `bin/` does not end in that: the match fails, the binary falls back to
the absolute `/usr/share/postgresql/18` no artifact has, and `initdb` then fails on every machine
except the packager's own. So the tree keeps Debian's `/usr` shape exactly and lays `bin` over it as
a symlink — which `find_my_exec` resolves *before* it measures anything, so all five cells report
`bin/postgres` and upstream's binaries still find their own share directory.

Two more decisions that are the rule rather than the packaging. `postgresql-<major>-jit` is left out:
Debian is the only publisher here that offers an LLVM JIT, and taking it would make `jit = on` —
PostgreSQL's own default — mean *compile the query* on one cell of a version and not on the other
four, with no error and no log line either way. `sepgsql` goes for the same reason one step smaller.
Neither is a command or an extension, so `tools/parity.py` could never have caught either: this is
the rule applied by hand where the check cannot reach. What the check *can* see, it saw — Debian's
`postgresql-18` offers exactly the 46 extensions the two EDB archives were cut down to, with nothing
on either side of the difference, which is the closest this repository gets to a second opinion on
what a version means.

And one thing this cell needs that its siblings carry: Debian builds `--with-system-tzdata`, so the
646 files of timezone data are not in the archive and cannot be put there — the compiled-in
`/usr/share/zoneinfo` is the one path PostgreSQL never relocates. It is named in `requires` beside
the glibc floor, because a dependency a user already has is a fact to state rather than a reason to
refuse.

## The empty cell, which is upstream's answer and not this repository's

Nobody publishes a Windows-on-ARM PostgreSQL, and the plan was to do what `mariadb_build.py` already
does for three cells nobody publishes: compile it natively. Asked instead of assumed, **PostgreSQL
does not build there** on any version MixEngine offers.

The evidence is upstream's own. The buildfarm has two Windows/ARM64 MSVC machines — `unicorn`,
approved December 2025, and `hoatzin`, March 2026; the newer reports on `master` only, and the older
tried the stable branches once. On 18 and 17 the build stops at target **1206 of 2047**, with 1205 objects already
compiled for `/MACHINE:ARM64` — not at a compiler, an intrinsic or an atomic, but at
`src/tools/msvc_gendef.pl`, the Perl script that generates the export file the server's extensions
link against, whose usage line reads `arch: x86 | x86_64` and which exits rather than accept
`aarch64`. That list gained `aarch64` after 18 branched; `master` and `REL_19_STABLE` have it.

Backporting two lines of Perl would be the wrong move, and for a reason the failure itself gives:
nobody knows what target 1207 does, because upstream has never got past 1206 on those branches. A
patch carried here would be this repository claiming a platform its publisher does not test, on
evidence that stops exactly where the evidence stops. So the cell stays empty, the index says so,
and it opens when PostgreSQL 19 ships.

**That it will open is an observation rather than a hope.** `hoatzin` is green on `master` in 44 of
its 53 runs, ~45 minutes each — so once `msvc_gendef.pl` accepts `aarch64`, everything behind it
compiles on Windows/ARM64 and passes the whole test suite. What is missing is the branch, not the
platform: 19 was at Beta 3 when this was last checked, and no healthy animal reports on
`REL_19_STABLE` yet. See P7c in [docs/roadmap.md](../roadmap.md) for the conditions and the date
they were last asked.
