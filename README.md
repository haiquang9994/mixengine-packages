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
docs/         one page per package under `packages/`, the rules they all answer to beside it, and
              `roadmap.md` — the ordered list of what is left
```

Nothing here is built on a developer's machine on purpose. There is no macOS or Linux in this
project's hands, and an artifact built on a machine nobody else can reproduce is an artifact nobody
can audit. The runners are the build machines.

## What is packaged, and where it runs

One table per package. The six columns are the six cells MixEngine runs on; a row is a **version
line**, and every patch release ever published on that line stays in the index — see
[the archive](docs/the-archive.md) for why that is a promise rather than a habit.

| Mark | Means |
| :---: | --- |
| ✅ | an archive for that cell is in the published, signed index today |
| — | no artifact is offered there, for the reason on that package's page |

**No cell below is a recipe waiting to be run.** There used to be a third mark for that — a kind
whose recipe was finished and whose releases page held nothing — and [P12](docs/roadmap.md) was the
task of running the four builds it stood for. It is closed, and the mark is gone with it: every cell
here is either an archive or a stated absence.

The ✅ marks are what the index actually contains; the — marks are what the recipe that would produce
the cell says about it. Where the two could disagree, the index wins and this table is wrong —
`tools/permanence.py` is what re-reads the index, and each package's page is what explains the shape.

### [PHP](docs/packages/php.md)

| Version | macOS aarch64 | macOS x86_64 | Linux x86_64 | Linux aarch64 | Windows x86_64 | Windows aarch64 |
| --- | :---: | :---: | :---: | :---: | :---: | :---: |
| **7.0** | ✅ | ✅ | ✅ | ✅ | ✅ | — |
| **7.1** | ✅ | ✅ | ✅ | ✅ | ✅ | — |
| **7.2** | ✅ | ✅ | ✅ | ✅ | ✅ | — |
| **7.3** | ✅ | ✅ | ✅ | ✅ | ✅ | — |
| **7.4** | ✅ | ✅ | ✅ | ✅ | ✅ | — |
| **8.0** | ✅ | ✅ | ✅ | ✅ | ✅ | — |
| **8.1** | ✅ | ✅ | ✅ | ✅ | ✅ | — |
| **8.2** | ✅ | ✅ | ✅ | ✅ | ✅ | — |
| **8.3** | ✅ | ✅ | ✅ | ✅ | ✅ | — |
| **8.4** | ✅ | ✅ | ✅ | ✅ | ✅ | — |
| **8.5** | ✅ | ✅ | ✅ | ✅ | ✅ | — |

Three recipes reach those five cells, and the Windows one is a borrow — so there is no
Windows-on-ARM column, upstream publishing no ARM64 Windows PHP to borrow.

### [Node.js](docs/packages/node.md)

| Version | macOS aarch64 | macOS x86_64 | Linux x86_64 | Linux aarch64 | Windows x86_64 | Windows aarch64 |
| --- | :---: | :---: | :---: | :---: | :---: | :---: |
| **16** | ✅ | ✅ | ✅ | ✅ | ✅ | — |
| **18** | ✅ | ✅ | ✅ | ✅ | ✅ | — |
| **20** | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| **22** | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| **24** | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |

The floor is 16 because that is where a *native* build exists for every cell, and Windows on ARM
starts at 20.0.0 because that is upstream's first build for it.

### [Python](docs/packages/python.md)

| Version | macOS aarch64 | macOS x86_64 | Linux x86_64 | Linux aarch64 | Windows x86_64 | Windows aarch64 |
| --- | :---: | :---: | :---: | :---: | :---: | :---: |
| **3.10** | ✅ | ✅ | ✅ | ✅ | ✅ | — |
| **3.11** | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| **3.12** | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| **3.13** | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| **3.14** | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |

The 3.10 line has no ARM64 Windows build in `python-build-standalone`, which is the whole of that
absence.

### [Ruby](docs/packages/ruby.md)

| Version | macOS aarch64 | macOS x86_64 | Linux x86_64 | Linux aarch64 | Windows x86_64 | Windows aarch64 |
| --- | :---: | :---: | :---: | :---: | :---: | :---: |
| **3.2** | ✅ | ✅ | ✅ | ✅ | ✅ | — |
| **3.3** | ✅ | ✅ | ✅ | ✅ | ✅ | — |
| **3.4** | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| **4.0** | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |

Windows borrows RubyInstaller, whose first ARM64 archive is in the 3.4 line; the four Unix cells are
compiled here, because no publisher offers a relocatable Ruby for them.

### [Caddy](docs/packages/caddy.md)

| Version | macOS aarch64 | macOS x86_64 | Linux x86_64 | Linux aarch64 | Windows x86_64 | Windows aarch64 |
| --- | :---: | :---: | :---: | :---: | :---: | :---: |
| **2.7** | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| **2.8** | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| **2.9** | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| **2.10** | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| **2.11** | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |

The recipe takes any 2.x and states no floors of its own — which cells a release built is read off
that release's own asset list. For the record: below 2.4.0 there is no macOS ARM build and below
2.4.5 no Windows ARM one.

### [MariaDB](docs/packages/mariadb.md)

| Version | macOS aarch64 | macOS x86_64 | Linux x86_64 | Linux aarch64 | Windows x86_64 | Windows aarch64 |
| --- | :---: | :---: | :---: | :---: | :---: | :---: |
| **10.6** | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| **10.11** | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| **11.4** | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| **11.8** | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| **12.3** | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |

Six full cells that upstream does not have: it publishes x86_64 for Linux and Windows and nothing
else, so three recipes stand behind this row and three of the cells are compiled here.

### [MySQL](docs/packages/mysql.md)

| Version | macOS aarch64 | macOS x86_64 | Linux x86_64 | Linux aarch64 | Windows x86_64 | Windows aarch64 |
| --- | :---: | :---: | :---: | :---: | :---: | :---: |
| **5.6** | ✅ | ✅ | ✅ | ✅ | ✅ | — |
| **5.7** | ✅ | ✅ | ✅ | ✅ | ✅ | — |
| **8.0** | ✅ | ✅ | ✅ | ✅ | ✅ | — |
| **8.4** | ✅ | ✅ | ✅ | ✅ | ✅ | — |
| **9.7** | ✅ | ✅ | ✅ | ✅ | ✅ | — |

Twenty-five cells, and eight of them are compiled here because upstream **withdrew macOS from 5.6
and 5.7 while both lines were still alive**: 5.7.31 offers a `macos10.14-x86_64` tarball and 5.7.44,
the last release of that line, offers no macOS asset of any kind. The two Linux cells of those lines
are compiled rather than half-borrowed, since the ARM one has nothing to borrow and two Linux
artifacts of one version — one Oracle's 2021 build at a glibc floor of 2.12, one built here in 2026
against an OpenSSL this repository supplies — would be two different databases under one version
number. Windows on ARM is empty on every line: Oracle has never published one, at any version.

### [PostgreSQL](docs/packages/postgres.md)

| Version | macOS aarch64 | macOS x86_64 | Linux x86_64 | Linux aarch64 | Windows x86_64 | Windows aarch64 |
| --- | :---: | :---: | :---: | :---: | :---: | :---: |
| **14** | ✅ | ✅ | ✅ | ✅ | ✅ | — |
| **15** | ✅ | ✅ | ✅ | ✅ | ✅ | — |
| **16** | ✅ | ✅ | ✅ | ✅ | ✅ | — |
| **17** | ✅ | ✅ | ✅ | ✅ | ✅ | — |
| **18** | ✅ | ✅ | ✅ | ✅ | ✅ | — |

Windows on ARM is upstream's answer rather than this repository's: PostgreSQL does not compile there
on any version offered here, and the cell opens when 19 ships.

### [Redis](docs/packages/redis-memcached.md)

| Version | macOS aarch64 | macOS x86_64 | Linux x86_64 | Linux aarch64 | Windows x86_64 | Windows aarch64 |
| --- | :---: | :---: | :---: | :---: | :---: | :---: |
| **7.2** | ✅ | ✅ | ✅ | ✅ | — | — |
| **7.4** | ✅ | ✅ | ✅ | ✅ | ✅ | — |
| **8.0** | ✅ | ✅ | ✅ | ✅ | ✅ | — |
| **8.2** | ✅ | ✅ | ✅ | ✅ | ✅ | — |
| **8.4** | ✅ | ✅ | ✅ | ✅ | ✅ | — |
| **8.6** | ✅ | ✅ | ✅ | ✅ | ✅ | — |
| **8.8** | ✅ | ✅ | ✅ | ✅ | ✅ | — |
| **8.10** | ✅ | ✅ | ✅ | ✅ | ✅ | — |

The floor is 7.2 because that is the oldest line upstream still patches *and* the last one under a
BSD licence. Windows x86_64 is the same source compiled against Cygwin rather than ported, with
`cygwin1.dll` beside the binaries under LGPLv3; the ARM cell is empty because there is no Cygwin
for it and an aarch64 archive may not hold x86_64 binaries.

**7.2 is the one empty cell in this table that is a version rather than an architecture.** It
compiles under Cygwin and then faults in its own startup — an access violation between `time()` and
the version banner, traced on the runner — while the same code runs in 7.4. Neither patching the
source nor building that one line at a different optimisation level is something this repository
will do, so Windows starts at 7.4 and 7.2 ships the four cells it can.

### [Memcached](docs/packages/redis-memcached.md)

| Version | macOS aarch64 | macOS x86_64 | Linux x86_64 | Linux aarch64 | Windows x86_64 | Windows aarch64 |
| --- | :---: | :---: | :---: | :---: | :---: | :---: |
| **1.6** | ✅ | ✅ | ✅ | ✅ | ✅ | — |

One line, because 1.6 is the only one upstream still tags. Windows x86_64 is **not** the same empty
cell as Redis's, and the reason once given for it — a privilege-dropping source file per Unix and
none for Windows — was a correct reading of the source tree and a wrong conclusion: those files are
optional and the build completes without them. Upstream has no native Win32 build system, so the
cell is compiled under Cygwin on the same configure line with nothing patched, and `cygwin1.dll`
(LGPLv3) travels beside the binary. Windows aarch64 stays empty because Cygwin has no aarch64 port.

### [nginx](docs/packages/nginx.md)

| Version | macOS aarch64 | macOS x86_64 | Linux x86_64 | Linux aarch64 | Windows x86_64 | Windows aarch64 |
| --- | :---: | :---: | :---: | :---: | :---: | :---: |
| **1.26** | ✅ | ✅ | ✅ | ✅ | ✅ | — |
| **1.27** | ✅ | ✅ | ✅ | ✅ | ✅ | — |
| **1.28** | ✅ | ✅ | ✅ | ✅ | ✅ | — |
| **1.29** | ✅ | ✅ | ✅ | ✅ | ✅ | — |
| **1.30** | ✅ | ✅ | ✅ | ✅ | ✅ | — |
| **1.31** | ✅ | ✅ | ✅ | ✅ | ✅ | — |

Upstream's only Windows build is a 32-bit x86 one, which runs natively under WOW64 and is what the
x86_64 cell borrows. Putting it in an archive whose manifest says `aarch64` would be a lie in the
index, so that cell is empty.

## The rules every row above answers to

- **[Borrow before you build](docs/borrow-before-you-build.md)** — an artifact is repacked from a
  publisher who already produces something relocatable, or it is compiled here; borrowing costs one
  evaluation and building costs a pipeline kept current for every security release.
- **[One version means one thing, and no more than is needed](docs/one-version-means-one-thing.md)**
  — the cells of a version have to be the same software, and none of them ships anything a running
  process does not read. Both halves are checked by diffing finished artifacts, because intent is
  not evidence.
- **[Repack, do not rearrange](docs/repack-do-not-rearrange.md)** — a borrowed tree keeps its
  publisher's layout, and `mixengine-artifact.json` is what says where things are. An archive
  without one is not an artifact.

## The rest of it

- **[Adding a version](docs/adding-a-version.md)** — every recipe's command line, and which workflow
  runs it.
- **[Dates are the one claim here that is not about bytes](docs/end-of-life-dates.md)** — why
  `data/eol.json` is transcribed from six publishers and checked on a clock.
- **[Nothing that has been published may be deleted](docs/the-archive.md)** — what the permanence
  promise costs, what GitHub can and cannot enforce, and the signing key.
- **[Building from source](docs/building-from-source.md)** — ten rounds of CI on PHP's legacy rows,
  and almost none of it about PHP. Read it before opening any **built** cell.
- **[Roadmap](docs/roadmap.md)** — the ordered list of what is left, and why each thing is in that
  order.

## Licences

The tooling here is MIT. **The artifacts are not ours** and each keeps its own licence: PHP under the
PHP License, and whatever `static-php-cli` links in under the terms of those projects. A borrowed
artifact is redistributed unmodified apart from the added manifest; `LICENSES.md` in each release
records what is inside it.
