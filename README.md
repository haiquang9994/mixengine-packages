# mixengine-packages

Runtime and service artifacts for [MixEngine](https://github.com/haiquang9994/MixEngine), and the
signed index that tells a MixEngine daemon what exists and where to get it.

This repository holds **no MixEngine source code**. It exists because the two things release on
different clocks: a PHP security release has to reach users the day it lands, and waiting for a
MixEngine release to carry it would make MixEngine's release cadence a function of every upstream
project it packages. Its release assets are also a permanent archive — the index promises that a
blueprint pinning PHP 8.1.29 keeps working forever, and upstreams prune, so the index must never
point at an upstream URL.

## What is here

```
schema/       the index and artifact formats, as JSON Schema, versioned
data/         upstream end-of-life dates, so the index can carry them
tools/        the recipes themselves, plus index generation and verification — Python 3, stdlib
              only for anything that runs on a build machine; `verify.py` alone pulls in
              `jsonschema`
.github/      the workflows that run the recipes on GitHub runners
docs/         what building the "built" rows taught us, for whoever adds the next one
```

Nothing here is built on a developer's machine on purpose. There is no macOS or Linux in this
project's hands, and an artifact built on a machine nobody else can reproduce is an artifact nobody
can audit. The runners are the build machines.

## Borrow before you build

Every artifact is either **borrowed** — repacked from a publisher who already produces something
relocatable — or **built** here. Borrowing costs one evaluation; building costs a pipeline kept
current for every security release, for as long as MixEngine offers the version. The evaluations,
and what each one settled, live in
[MixEngine's `runtime-packaging.md`](https://github.com/haiquang9994/MixEngine/blob/master/.claude/operations/runtime-packaging.md).

## One version means one thing, and no more than is needed

Two rules that go together, because each without the other produces an artifact somebody regrets.

**The artifacts of a version must be as alike as the platforms allow.** A user pins `mariadb 11.8`
in a blueprint and hands it to a colleague on another operating system; what they get has to be the
same database, not whatever that platform's publisher happened to compile. Upstreams do not help
here — MariaDB's Linux bintar carries every storage engine its maintainers can build, its own `.deb`
packages carry none of them because each is a separate package, and a source build carries whatever
was configured. Left alone, one version means three different feature sets. So the set of features
is **chosen here, once, for all cells of a kind**, and where two recipes express that choice
separately — a packer deleting, a compiler configuring — each says so beside the other.

Parity may be resolved in either direction, and which one is a judgement about the feature rather
than about convenience. Something a local development environment does not do — a cluster engine, an
ODBC bridge, PAM authentication — is dropped everywhere. Something it would reach for is *enabled*
everywhere, even where that means compiling it on the targets nobody publishes a binary for; that is
how `mariabackup` ended up in all six MariaDB cells after first being dropped from all six.

**And an artifact contains what it takes to run, nothing else.** Not debug symbols — upstream's
Linux bintar hides 700 MB of them inside its binaries and Windows keeps 74 MB in a single `.pdb`,
while Debian and Windows both publish theirs separately for whoever wants them. Not test suites, not
benchmarks, not headers or import libraries: MixEngine installs a database or a runtime, not an SDK.
Every user downloads this once per version per machine, and none of that is ever read.

Whatever is taken out or put in is **recorded in `mixengine-artifact.json`** — `upstream.removed`,
`upstream.added`, `upstream.stripped` — so that "borrowed" keeps meaning something a reader can
check against what the publisher shipped.

For PHP:

| OS / arch | Range | How |
| --- | --- | --- |
| Windows x86_64 | 7.0 – newest | **borrowed** — official windows.php.net builds, repacked |
| macOS aarch64, x86_64 | 8.1 – newest | **built** — [`static-php-cli`](https://github.com/crazywhalecc/static-php-cli) |
| macOS aarch64, x86_64 | 7.0 – 8.0 | **built** — from source, dependencies bundled beside the binary |
| Linux x86_64, aarch64 | 8.1 – newest | **built** — `static-php-cli` |
| Linux x86_64, aarch64 | 7.0 – 8.0 | **built** — from source inside AlmaLinux 8, dependencies bundled |

The last two rows are the range `static-php-cli` does not build. They are affordable because those
six branches are *final* — 7.0.33 through 8.0.30 will never have another release — so the pipeline
runs a handful of times rather than being kept current for every security release. `php_legacy_unix.py`
compiles them and `relocate.py` makes the result carry its own libraries. On macOS both
architectures are built on a runner of their own: nothing is cross-compiled and nothing runs under
Rosetta, so a branch that will not compile natively for an architecture is simply not offered there.

Getting those two rows green took ten rounds of CI, and almost all of it went on builds that exited
zero and shipped something wrong rather than on code that failed to compile.
[`docs/building-from-source.md`](docs/building-from-source.md) is what that was worth keeping — read
it before opening any other **built** cell, because little of it is about PHP.

For Node.js there is nothing to evaluate and one recipe for every cell:

| OS / arch | Range | How |
| --- | --- | --- |
| Windows x86_64 | **16 – newest** | **borrowed** — official nodejs.org zip, repacked |
| Windows aarch64 | **20 – newest** | ditto; upstream's first Windows-on-ARM build is 20.0.0 |
| macOS aarch64 | **16 – newest** | **borrowed** — official tarball; 16.0.0 is upstream's first native Apple Silicon build |
| macOS x86_64 | **16 – newest** | ditto |
| Linux x86_64, aarch64 | **16 – newest** | ditto |

The floor is 16 because that is where a *native* build exists for every architecture MixEngine runs
on — before it, the only macOS Node is x86_64, and handing that to an Apple Silicon machine would be
offering emulation under the name of a version. Where a line has no build for a target,
`tools/node.py` says so and exits 75 — an empty cell of the table, which the workflow skips rather
than fails, so that one absent build does not stop the release of the five that exist.

For Python the evaluation was as short, and the row had been *assumed* borrowable since before there
was a pipeline. It is:

| OS / arch | Range | How |
| --- | --- | --- |
| Windows x86_64 | **3.10 – newest** | **borrowed** — [`python-build-standalone`](https://github.com/astral-sh/python-build-standalone), repacked |
| Windows aarch64 | **3.11 – newest** | ditto; the 3.10 line has no ARM64 build |
| macOS aarch64, x86_64 | **3.10 – newest** | ditto |
| Linux x86_64, aarch64 | **3.10 – newest** | ditto, the `gnu` builds and never `musl` |

One thing had to be added rather than repacked, and only on Windows: upstream ships `Scripts/` empty,
so `pip` is importable and not runnable. `tools/python.py` writes a two-line `Scripts/pip.cmd` that
calls the interpreter beside it, because the alternative — letting `ensurepip` generate `pip.exe` —
bakes an absolute interpreter path into a tree whose whole purpose is to be movable.

One thing is removed, and only on Linux before 3.13: `_crypt` links against a libxcrypt no archive
here carries, which Debian and Ubuntu install as a base package and Fedora does not — so keeping it
would mean publishing an artifact that works on some glibc distributions and not others. CPython
deprecated `crypt` in 3.11 and removed it in 3.13; this makes the three lines before that behave the
same way. Both the addition and the removal are named in each archive's `mixengine-artifact.json`,
under `upstream.added` and `upstream.removed`, so a reader comparing against upstream finds the
difference stated rather than deducing it.

For Ruby, one of the three columns turned out to be borrowable and two did not:

| OS / arch | Range | How |
| --- | --- | --- |
| Windows x86_64 | **3.2 – newest** | **borrowed** — [RubyInstaller](https://github.com/oneclick/rubyinstaller2) `.7z`, repacked |
| Windows aarch64 | **3.4 – newest** | ditto; upstream's first ARM64 archive is in the 3.4 line |
| macOS aarch64, x86_64 | **3.2 – newest** | **built** — from ruby-lang.org's source, dependencies bundled |
| Linux x86_64, aarch64 | **3.2 – newest** | **built** — the same, inside AlmaLinux 8 for the glibc floor |

RubyInstaller configures Ruby with `--enable-load-relative`, so the standard library, the gem home
and the CA bundle are all computed from the executable's own location — `tools/ruby.py` checks all
four from a directory the archive has been moved to. The macOS and Linux cells have no such
publisher: Homebrew's `portable-ruby` is relocatable and publishes exactly one version,
`ruby/ruby-builder`'s artifacts "embed the install path when built and cannot be moved around" in
its own README's words, and RVM's binaries are prefix-bound and years stale. So `tools/ruby_unix.py`
passes the same flag to a build of its own — and the two recipes share `tools/ruby_smoke.py`, which
is *what they claim* rather than how they got there, because a daemon installing one of these cannot
tell which produced it.

The CA store is the part that is not obvious. A Ruby linked against a distribution's OpenSSL
inherits that distribution's `OPENSSLDIR` — `/etc/pki/tls` on the Red Hat family, `/etc/ssl` on the
Debian one — so an artifact built on one verifies certificates perfectly on the build machine and
fails every handshake on a user running the other, with an error that names nothing. OpenSSL is
therefore compiled here with its four default-path functions taught to answer relative to the
loaded `libcrypto`'s own location, which is `--enable-load-relative` applied one library down, and
the bundle itself ships inside the tree. `OpenSSL::X509::DEFAULT_CERT_FILE` names a file inside the
artifact on all six targets, and the smoke test verifies a real chain over the network rather than
trusting the path.

## Services, and what is different about them

Caddy is the first thing here that is not a runtime, and it is the easiest borrow in the table:

| OS / arch | Range | How |
| --- | --- | --- |
| Windows x86_64 | **2.0 – newest** | **borrowed** — official `caddy_<version>_windows_amd64.zip`, repacked |
| Windows aarch64 | **2.4.5 – newest** | ditto; upstream's first Windows-on-ARM archive |
| macOS aarch64 | **2.4.0 – newest** | ditto; 2.0 through 2.3 are Intel only |
| macOS x86_64 | **2.0 – newest** | **borrowed** — the official `mac_amd64` tarball |
| Linux x86_64, aarch64 | **2.0 – newest** | ditto |

There is nothing to evaluate: every artifact is one statically linked Go binary, with no interpreter
to find, no standard library to locate and no CA bundle to resolve — which is the whole of what the
runtime recipes spend their length on. `tools/caddy.py` states no floors of its own; which targets a
release built is read off that release's own asset list, so an empty cell says so rather than 404ing
half a download later.

**What is different is the proof, and it is the reason this is not the Node.js recipe with another
table in it.** A runtime is packed to be *executed*, and `php -v` answering from a moved tree is the
claim. A service is packed to be *run, configured, health-checked and stopped* — each of those
through a specific mechanism that MixEngine's own Caddy recipe depends on. So the smoke test
exercises all four, from a directory the archive has been moved to: it validates a rendered
Caddyfile with `caddy validate`, starts the server with `caddy run`, asks the admin endpoint for the
configuration back, serves a request, and stops the server through that same endpoint. An artifact
that answers `caddy version` and cannot be health-checked is one MixEngine would find out about
against a user's site.

`caddy run` rather than `caddy start`, incidentally: `start` hands its child the parent's stdout and
returns, so anything capturing that output waits for the *server* to exit. That is a hang rather
than a failure, and it is also what the supervisor will exec.

Two smaller decisions. **The checksum is upstream's SHA-512**, from `caddy_<version>_checksums.txt`,
because that is the algorithm Caddy publishes — the manifest still carries a SHA-256 of the same
bytes, since that is the field every artifact here has, and `upstream.verified_against` records
which one the download was actually checked against. And **nothing is built with `xcaddy`**: a
plugin set baked into an artifact cannot change without a repack, and MixEngine's promise is a web
server that works out of the box rather than one nobody else can reproduce.

### MariaDB, where the table was wrong

Caddy is what a borrow looks like when it works. MariaDB is what the rule is *for*: MixEngine's
runtime table said "official zip / official tarball / official tarball" for all three systems, and
asking the catalogue rather than assuming it — the whole point of "borrow costs one evaluation" —
answered something else. Every release from 10.2 to 13.1 offers Linux and Windows on x86_64 and
nothing else. There has never been a macOS build, and there is no ARM64 tarball of any kind.

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

## Repack, do not rearrange

A borrowed artifact keeps the directory layout its publisher shipped. It is tempting to normalise
every runtime into one `bin/`, `lib/`, `ext/` shape so the daemon needs no per-OS knowledge — and it
would break Windows immediately, where `php.exe` resolves its DLLs from its own directory and moving
them apart makes the binary unloadable in a way that only shows up at run time.

So the abstraction is not the directory. It is **`mixengine-artifact.json`**, written into the root
of every archive, which names where things actually are:

```json
{
  "schema": 1,
  "kind": "php", "version": "8.3.33", "os": "windows", "arch": "x86_64",
  "source": "borrowed",
  "provides": { "php": "php.exe", "php-cgi": "php-cgi.exe" },
  "extension_dir": "ext",
  "extensions": { "static": ["Core", "openssl", "..."], "shared": ["curl", "..."] },
  "requires": { "vcredist": "2019" }
}
```

The daemon reads that file and never guesses a path. An archive without one is not an artifact.

## Adding a version

```bash
# Windows: borrow, repack, verify, smoke-test — runs anywhere Python 3 does
python tools/php_windows.py --version 8.3.33 --out dist/

# macOS / Linux, 8.1 and newer: static-php-cli builds it
python3 tools/php_unix.py --branch 8.3 --out dist/

# macOS / Linux, 7.0 – 8.0: compiled from source, then made to carry its own libraries
python3 tools/php_legacy_unix.py --branch 7.4 --out dist/

# Node.js: one recipe for every target, run on the target it packs for
python tools/node.py --version 22 --out dist/

# Python: likewise, from python-build-standalone's newest release unless one is pinned
python tools/python.py --version 3.12 --out dist/

# Ruby: Windows borrows RubyInstaller's archive
python tools/ruby.py --version 3.4 --out dist/

# Ruby on macOS / Linux: compiled, with its own OpenSSL and its own CA bundle
python3 tools/ruby_unix.py --version 3.4 --out dist/

# Caddy: one recipe for every target, and it runs the server it packed before publishing it
python tools/caddy.py --version 2 --out dist/

# MariaDB: three recipes, chosen by what upstream publishes for the cell being packed
python tools/mariadb.py --version 11.8 --out dist/        # Windows x86_64, Linux x86_64
python3 tools/mariadb_deb.py --version 11.8 --out dist/   # Linux aarch64, out of upstream's .deb
python tools/mariadb_build.py --version 11.8 --out dist/  # macOS, and Windows on ARM64

# Then regenerate and sign the index from what the releases actually contain
python tools/mkindex.py --base-url … --out dist/index.json
minisign -Sm dist/index.json -s minisign.key
```

In practice none of that is run by hand: `.github/workflows/build-php.yml` takes a version, picks the
recipe from it and produces every target; `build-node.yml`, `build-python.yml` and `build-caddy.yml`
do the same with one recipe and six; `build-ruby.yml` runs six legs across two recipes; and
`publish-index.yml` regenerates and signs the index from every release that exists.

`build-mariadb.yml` is the one that is shaped differently, and both differences are MariaDB's rather
than a preference. It runs **three** recipes across six legs, because upstream publishes a binary for
two cells, `.deb` packages for a third and nothing at all for the rest. And it takes a *list* of
versions — `all` expands to every supported series — because MariaDB maintains four at once with
end-of-life dates years apart, so a workflow that took one version would have to be invoked four
times and would miss one.

The five borrowed recipes share `tools/borrow.py` — downloading, hashing, unwrapping the publisher's
wrapper directory where there is one, packing, and running a program with a `PATH` the runner cannot
answer. What none of them share is the smoke test, deliberately: the mechanics are the same for every
publisher and the *claim* is not, and this repository has already been bitten once by two producers
writing the same manifest field to mean two different strengths of proof. The one exception proves
the rule — `tools/ruby_smoke.py` is shared by the two Ruby recipes precisely *because* they produce
the same runtime for one table row, so there the claim is the thing that must not differ.

## The signing key

The index is signed with minisign (Ed25519) and the public key is compiled into MixEngine, so
rotating it needs an application update. The private key lives only in this repository's Actions
secrets:

```bash
minisign -G -p minisign.pub -s minisign.key   # keep minisign.key out of git, forever
```

`minisign.pub` is committed — it is public by definition, and having it in the tree is how a reader
checks that the key compiled into MixEngine is the one signing this index.

## Licences

The tooling here is MIT. **The artifacts are not ours** and each keeps its own licence: PHP under the
PHP License, and whatever `static-php-cli` links in under the terms of those projects. A borrowed
artifact is redistributed unmodified apart from the added manifest; `LICENSES.md` in each release
records what is inside it.
