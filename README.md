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
docs/         what building the "built" rows taught us, for whoever adds the next one, and
              `roadmap.md` — the ordered list of what is left
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

*None of that is ever read* is a claim about the runtime, though, and one row answers it the other
way. `pip install` of a source distribution compiles a C extension on the machine doing the
installing, so CPython's headers and its Windows import library are read there routinely — and are
kept, on every cell, named in a top-level `keeps` that says which path and why. An exemption that
has to be written into the artifact is an exemption somebody argued for; the rule is what makes the
argument necessary.

Whatever is taken out, put in, deliberately kept, admitted missing or **shipped at upstream's path
and not in upstream's bytes** is recorded in `mixengine-artifact.json` — `upstream.removed`,
`upstream.added`, `upstream.changed`, `keeps`, `lacks` — so that "borrowed" keeps meaning something a
reader can check against what the publisher shipped, and "no more than is needed" keeps meaning a
list somebody wrote down rather than a habit somebody remembers. `upstream.changed` is the one
hardest to do without: a file that was modified rather than added or deleted is indistinguishable
from a corrupted download unless the artifact says what was done to it. There was briefly a sixth,
`upstream.stripped`, which said in a sentence what `changed` says as a mapping of path to command —
and two spellings of one fact is the shape of thing this rule exists to remove, so it survives only
in artifacts published before the check below was written.

**Both rules are checked by comparing finished artifacts, because intent is not evidence.** The first
audit of a green MariaDB run — six cells, all six proven against a running server — found the two
Linux artifacts three times apart in size and each of the four asymmetries below, none of which any
recipe knew it had:

* the same feature list applied by three recipes, and only one of them running it: the `.deb` cell
  kept PAM plugins and Galera scripts, the compiled cells kept 21 MB of test binaries, a 14 MB import
  library and eighteen demonstration plugins;
* a feature present on five cells and missing on the sixth *because that cell was smaller* — the
  compression providers InnoDB loads for `innodb_compression_algorithm`, which upstream splits into
  five more `.deb` packages;
* a pattern that had excluded `mysql_ldb` since the first round and never once removed it, because
  deleting its target first made the symlink invisible to `Path.exists`;
* and one difference that is not ours to fix and is written down instead: upstream's own bintar ships
  the client half of the PARSEC authentication plugin without the server half, which its `.deb`
  packages have.

Most of that is invisible in a passing build. A smoke test proves an artifact *runs*; only a diff
proves six of them are the same thing.

That diff is now [`tools/parity.py`](tools/parity.py), and it runs where every artifact of a version
is on one disk at once — the index workflow, which is the only place in the repository that can see
more than one cell. Across the cells it compares feature sets: `extensions.static ∪
extensions.enabled` for PHP, the commands in `provides` for every kind. Within one artifact it
refuses any path matching what the second half of the rule throws out. **Every difference it is not
told about is a defect**, which is what makes the two ways of telling it worth having — `lacks` for
something a cell cannot do at any price, `keeps` for something it carries on purpose, both written
into the artifact with the reason attached, and [`tools/php_parity.py`](tools/php_parity.py) for the
handful of differences that belong to a whole row rather than to one cell.

Pointed at the catalogue as it stands, it reports 370 differences on the PHP row and none on any
other, and every one of them is against an archive packed before the task that closes it. That is
the answer a check like this is supposed to give the first time: not *nothing is wrong*, and not
something new either, but *here is the backlog, and here is the thing that will notice if it comes
back*.

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

**Three recipes, one extension set.** Borrowing the Windows build meant borrowing its publisher's
idea of what PHP is for, and that idea is not this one: 8.3 arrived without `redis` and `mongodb`,
the two the Unix legs *fail a build* over, and with an ODBC bridge, an Oracle client, a Firebird
driver, and PHP's own `dl_test` and `zend_test`. So the set is chosen in
[`tools/php_parity.py`](tools/php_parity.py) and the three recipes reach it three ways: compiled in
by `static-php-cli`, `phpize`d on 7.x, downloaded from php.net's own PECL builds on Windows. What is
surplus there is deleted, along with the libraries it was the only user of, and both directions are
named in `upstream.added` and `upstream.removed`.

Two of those differences could not be resolved by choosing, and are written down instead. Windows
has never had `pcntl`, `posix`, `sysvmsg` or `sysvsem`, and its 7.0 build has no `readline` and no
`dba` where every Unix cell of 7.0 does; 7.0 and 7.1 carry `mcrypt` compiled in, and 7.0 through 7.3
carry `wddx`, which cannot be removed from a build nobody here compiles. Naming them is what lets a
cross-cell check exist at all — everything it does *not* name is then a defect.

One more field came out of this. `extensions.shared` says what an artifact can load and says nothing
about what it will; on Windows nine extensions that are compiled into every Unix cell are loadable
modules, because no Windows build exists with them static. `extensions.enabled` is the set the
daemon is expected to switch on, so `static ∪ enabled` is what a cell actually does, and that is
the thing six cells have to agree about.

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

One decision was needed, and it is the only one this row makes: **`include/node` is dropped, on
every cell**. Upstream ships 59 MB of C++ headers to Unix and none at all to Windows — 2,726 files
in 24.19.0, 29.7% of that archive, byte for byte its own `node-v24.19.0-headers.tar.gz` — so one
version meant two things before anything here chose. The reading that keeps them is that `node-gyp`
needs headers, and it does; what it does not do is read *these*. `node-gyp` looks inside the runtime
it is running under only when the build set `use_prefix_to_find_headers`, a flag distributions pass
so their `-dev` package can compile offline, and every official build has it false — read out of the
`process.config` baked into the Linux binaries rather than assumed. Without it, `configure.js`
downloads `process.release.headersUrl` into `~/.node-gyp/<version>` and compiles against that, which
is why native modules have always built on Windows against an archive with no `include/` at all.

Keeping them everywhere was not reachable in any case: `node-gyp --nodedir` on Windows links against
`<nodedir>/$(Configuration)/node.lib`, a path out of a build tree that the headers tarball has never
contained and that upstream publishes separately, per architecture.

What goes with them is whatever else sits at the root and is not the runtime, because `tools/node.py`
keeps a list of what stays rather than a list of what goes: `include/` and `share/` on Unix,
`install_tools.bat` — a Chocolatey install of Python and the VC build tools, onto the whole machine —
and `nodevars.bat` on Windows, `README.md` and `CHANGELOG.md` on both. The keep-list is why Node 16's
`share/systemtap` and `node_etw_provider.man` went too, neither of which anything here knew existed.
What stays, checked rather than assumed, is npm's 2.7 MB of `docs/` and `man/`: `npm help-search`
reads `docs/content`, and `npm help` opens `docs/output/*.html` on Windows and runs `man` against
`man/man[1-7]` on Unix. Once both cells are pruned, the whole remaining difference between the
Windows and Linux trees of 24.19.0 is `node.exe` against `bin/node` and the per-shell launchers
beside it.

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

**One decision was needed, and it is tkinter — dropped, on every cell.** Not for its size, though it
is 11.7 MB of a 59.8 MB Windows archive, but because upstream ships a *different* tkinter to each
half of the table: Windows gets Tk 8.6 with Tix 8.4.3 beside it, Unix gets Tk 9.0 with itcl and the
Tcl Thread package, and on 3.14 the Windows DLLs turn into Tcl 9 and carry the whole Tcl script
library inside themselves. So `python 3.13.15` already meant one toolkit on one machine and a
different one on the next. Dropping it rather than levelling it up is the rule's own test applied to
the feature: nothing MixEngine runs draws a window, and the two things in the standard library that
need a display are the graphical toolkit and the editor written in it. The Unix cells lose the whole
of `share/` with it — a manual page whose Windows twin has never existed, and a 2.2 MB terminfo
database the ncurses compiled into these very builds cannot reach, because the paths built into that
binary are `/etc/terminfo`, `/lib/terminfo` and `/usr/share/terminfo` and none of them is inside the
archive — and the Windows cells lose the per-extension import libraries, 31 of them on 3.10, which
link a module *into* a Python being compiled and are opened by nothing that installs a wheel.

**And one thing is kept that this document rules out everywhere else: the C API.** `include/` on all
six cells, plus `libs/python3XX.lib` and `libs/python3.lib` on the two with a linker that needs
them. That is the opposite answer to the one P2 gave PHP, where `dev/php8.lib` was deleted as *"892
KB of import library in a runtime that is not an SDK"*, and the difference is not a lapse — it is
what "no more than is needed" means when the question is asked per runtime. A PHP extension reaches
a developer's machine as a DLL somebody else compiled. A Python extension frequently does not:
`pip install` of any source distribution without a matching wheel compiles C *on the machine doing
the installing*, against `Python.h` and, on Windows, linked to `python3XX.lib`. Take them out and
the runtime still starts, still passes every check in its smoke test, and fails the first such
install. So each archive carries a top-level `keeps` in its manifest naming the path and the reason,
`borrow.declare` refuses to write a `keeps` naming something the tree does not have, and the smoke
test proves the claim from the other side by asking the relocated interpreter where its headers are
and finding `Python.h` there.

**The second kept thing is the one that looks least defensible and is the same decision.** Every
Unix cell carries `lib/libpython3.X.so` — 30.3 MiB on Linux, 16.7 MiB on macOS — which is a second
complete copy of an interpreter `bin/python3.X` already contains statically, and which nothing that
ships in the archive loads: of the twelve dynamically linked ELF files in a Linux tree, none names it
in `DT_NEEDED`, and the macOS binary's `LC_LOAD_DYLIB` list says the same. It is kept because of what
upstream ships beside it. There is a `libpython3.X.so` symlink whose only possible consumer is a
linker, the runtime loader going by `SONAME` instead; `python3-config` derives its prefix from its
own location precisely so a *moved* tree still answers, and prints `-L<tree>/lib -lpython3.X` with
`PY_ENABLE_SHARED=1` written in so it will not look anywhere else. Delete the library and the
artifact is describing a file it does not carry — repairable only by rewriting the publisher's record
of its own build. Windows settles the same question with no choice in the matter, `python3XX.dll`
being the interpreter there and `libs/python3XX.lib` the same file an embedder links, so two cells
can embed a Python whatever anyone decides. **That is the whole of the remaining difference between
the halves**, and the arithmetic says so exactly: pruned and stripped, Linux is 18.6 MiB larger than
Windows on 3.13.15 while this file weighs 19.1 MiB, so without it the Linux artifact would be the
*smaller* one. macOS lands within 1.4 MiB of Windows carrying a 15.3 MiB copy of its own.

**Stripped, because a variant named `install_only_stripped` still shipped 14.5 MiB of tables nothing
in a running process reads**, and it was named honestly: upstream's step removed the debug
information and only that — 207 MB of it from one library — which by definition left the symbol
tables behind, and from 3.12 on left something stranger beside them. `.note.bolt_info` appears in
`bin/python3.X` and in no library; `.rela.text` appears in the library and in no executable. So BOLT
ran on the executable and consumed its relocations, and the library, linked with the same
`--emit-relocs` and then never optimised, carries 6.5 MB of the input to a step that skipped it. The
direction is again Windows': its stripped variant ships **no `.pdb` at all**, so there is nothing to
level up to. The flag is not the same on both Unix halves and that is the point — ELF hides a second,
allocated symbol table a `strip` may not touch, while Mach-O has one table whose exported range
`--strip-all` empties, taking 1,755 symbols and `_Py_Initialize` with it, so macOS gets `-x`. And
because the file most at risk is the one nothing in the archive loads, the proof is not a test run:
`python.mapped` compares every allocated section, every program header and every table a linker reads
across the operation and refuses to publish an artifact where any of it moved, `python.countersigned`
recomputes the arm64 code signature's 4,230 page hashes rather than trusting the tool to have
re-signed, and `upstream.changed` names each file and the command that changed it, because a file
carrying upstream's path and not upstream's bytes is the one difference that reads as corruption.

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

**What they claim was shared and what they *decide* was not, and that had cost 225 MB.** The four
compiled cells pass `--disable-install-doc` and delete `share/man`, `share/doc` and `share/ri` on an
explicit argument — that a development environment has no business shipping four copies of Ruby's
manual for every version of every line. The two borrowed cells had never been told, because
RubyInstaller ships a general-purpose Ruby and nothing had measured what that meant: 60.3 MB of a
108 MB tree on 3.4.10, and **224.9 MB of a 276 MB tree on 4.0.6**, four fifths of an artifact of a
programming language being RDoc's HTML rendering of that language's own manual. The list is
`tools/ruby_parity.py` now and both recipes read it, which halves the Windows archive — 34.0 MB to
17.2 MB on 3.4.10, 53.5 MB to 18.8 MB on 4.0.6.

**And one asymmetry here cannot be closed at any price, so the artifact states it.** Asked what
RubyInstaller does about the three flags the Unix build passes, a Windows Ruby answers that it has
no YJIT — `RubyVM::YJIT` is undefined and `ruby --yjit` warns, because CRuby does not build YJIT for
`x64-mingw-ucrt` — and that `gem install` of a gem with a C extension exits 1 with `MSYS2 could not
be found`, the compiler being a separate ~1 GB toolchain published as its own installer. The Unix
recipe *fails a build* over both. Neither can be moved, so both are written into a top-level `lacks`
field: a daemon can then refuse to enable a feature the cell does not have instead of passing a flag
that warns, and a blueprint asking for a native gem can fail where it is written rather than on
somebody's machine. It is the only field here that is an admission, and an absence nothing states is
an absence a reader has to discover.

**A second asymmetry was written down and then measured, and it was not there.**
`RbConfig::CONFIG['ENABLE_SHARED']` is `yes` on the two borrowed cells and `no` on the four compiled
ones, which reads as *two cells can be embedded in a program and four cannot* — the question P4a
answered for CPython, apparently coming out the other way. `--disable-shared` does not mean no
libruby. It means libruby is a **static archive**: `lib/libruby-static.a` on Linux and
`lib/libruby.3.4-static.a` on macOS, 41.4 MB and 28.3 MB on 3.4.10 and the largest file in either
tree. And each half names its own copy from a record that survives the move — `rbconfig.rb` begins
by deriving its prefix from its own location, which is `--enable-load-relative` again, so
`CONFIG['LIBRUBYARG']` reads `-Wl,-rpath,$(libdir) -lruby-static $(MAINLIBS)` on the compiled cells
and `-lx64-ucrt-ruby340` on the borrowed ones, which is what the 2.4 MB
`lib/libx64-ucrt-ruby340.dll.a` resolves. So all six hand an embedder a link line naming a file
inside the artifact, the difference is linkage and not capability, and the import library is not
surplus after all. Both stay, with `include/` beside them, declared in `keeps` — on the Windows
cells too, where `lacks` has just said no compiler is present: `ridk install` adds one, and headers
deleted here cannot be.

What the same measurement did find is that **the Unix cells shipped their debug information**, which
is P4b's finding on a different row and is levelled in the same direction. Of 3.4.10's Linux tree,
`bin/ruby` was 20.5 MB with 11.7 MB of DWARF in it and the static library 41.4 MB of which 26.1 MB
was DWARF and its relocations; Windows has none, RubyInstaller having linked with `-s`. Every
compiled file is now stripped and every one of them is proven across the operation, which takes
104.4 MB of Linux tree to 63.1 MB and 78.4 MB of macOS tree to 55.3 MB.

**It takes two instructions, because the tree holds two kinds of file and either instruction would
destroy the other kind.** `bin/ruby` and the extension modules are loaded, and their symbol tables
are dead weight — those get `--strip-all` on Linux and `-x` on macOS, and are checked by comparing
every byte a loader maps and every table a linker reads before and after. The static library is
*linked against*, which is what `keeps` has just finished arguing, and its symbol table is the entire
point: `--strip-all` over `libruby-static.a` takes it from 41.4 MB to 7.9 MB and leaves a file that
resolves nothing, a broken artifact that no test here would catch because nothing inside the tree
links against it either. So it gets `--strip-debug`, and it is checked by a different comparison —
the archive's own symbol index, then every member's globals, its relocations resolved *by name*, and
the bytes of every section that will end up in somebody else's binary. By name because a successful
strip renumbers both tables underneath them; comparing the tables as bytes would report every
working run as a failure.

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

### PostgreSQL, where most of the download is not a database

MariaDB's evaluation found the table wrong about *which* platforms upstream builds for. PostgreSQL's
finds something else: the project itself publishes **no binaries at all**, only source. What the
download page points at on Windows and macOS is EnterpriseDB's, and what EDB publishes is an
installer's payload rather than a server.

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

#### The macOS cells, where one archive is written down three times

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

#### The Linux cells, where the publisher is the project and the layout may not be touched

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

#### The empty cell, which is upstream's answer and not this repository's

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
`REL_19_STABLE` yet. See P7c in [docs/roadmap.md](docs/roadmap.md) for the conditions and the date
they were last asked.

### Redis and Memcached, where the evaluation had nothing to weigh

The table said "we build with MSVC, or ship Valkey" for Redis on Windows and "we build" for
Memcached everywhere, and P8 was written to decide between compiling both natively on a Windows
runner and declaring the cell empty. Asked rather than assumed, **there is no first option**. Redis
8.10 has no `CMakeLists.txt`, no `win32/` directory and no project file of any kind: it is a
`src/Makefile` around POSIX `fork()`, `epoll` and `kqueue`, and its own README lists Linux, OSX,
OpenBSD, NetBSD and FreeBSD. memcached is autotools, with a privilege-dropping source file for each
Unix — `linux_priv.c`, `darwin_priv.c`, `freebsd_priv.c`, `openbsd_priv.c`, `solaris_priv.c` — and
none for Windows. Neither is a build that needs the right flags. Neither is a build that exists.

| OS / arch | Range | How |
| --- | --- | --- |
| macOS aarch64, x86_64 | Redis **7.2 – newest**, memcached **1.6 – newest** | **built** — upstream publishes source only, for every platform |
| Linux x86_64, aarch64 | ditto | ditto |
| Windows x86_64, aarch64 | — | **upstream has no Windows build system**, and no fork of either project supplies one |

The three ways round it were each asked and each answers no. **Valkey**, which MixEngine's own table
named as the alternative, is a fork of the same POSIX program and is not supported on Windows
either; its installation page sends a Windows user to WSL, which [ADR
0003](https://github.com/haiquang9994/MixEngine/blob/master/.claude/decisions/0003-no-container-isolation.md)
excludes. **Memurai** is proprietary, and a repository that redistributes what it packs cannot pack
one. **The community rebuilds** are the fork nobody maintains that the plan already refused. So both
Windows cells are stated rather than filled — and the Windows legs of both workflows run anyway and
exit 75, because an empty cell that says so in every run's log is worth a runner minute more than a
row somebody has to remember is missing.

**Redis is the first row here that spans a licence change, and it is why the floor is 7.2.** Through
7.2 Redis is BSD-3. 7.4 is RSALv2 or SSPLv1, neither of them OSI-approved; 8.0 added AGPLv3 as a
third option a redistributor may choose. All of those permit what this repository does, and the
AGPLv3 option is the one that makes an 8.x artifact easy to be honest about: complying means offering
the corresponding source, and every artifact's `recipe` field already names the exact upstream
tarball and the SHA-256 it was checked against, because nothing in it is patched. The floor is at 7.2
rather than lower because that is the oldest line upstream still patches *and* the last one a user
who will not accept a source-available licence can install.

**Core Redis, and none of the modules the tarball vendors.** Since 8.0 the release archive ships
RediSearch, RedisJSON, RedisTimeSeries, RedisBloom and vector-sets — 6,671 files, and the reason
`redis-8.10.0.tar.gz` is 21 MB where `redis-7.2.15.tar.gz` is 3.4 MB. Building them wants LLVM 21,
Rust 1.94 and a CMake pinned between 3.25 and 3.31.6, on four cells, for every security release, to
ship data structures a local web development environment does not reach for — and it would make the
7.2 cells of this row mean something different from the 8.x ones, since 7.x has no modules to build
at all. Upstream supplies the switch by name (`scripts/build.sh redis` is "Redis only"), and what
that script runs for the core is `make -C src all`, which is what the recipe drives directly so one
code path serves both lines.

**Neither service ships TLS**, and the consequence is the good kind: with `BUILD_TLS` off and
`--enable-tls` unasked, `redis-server` and `memcached` import nothing outside the C runtime on Linux
and nothing outside `libSystem` on macOS. These are the only *built* rows here that need no bundled
libraries at all, and `relocate.verify` is what says so rather than the build flags. What TLS would
buy is an encrypted loopback connection between two processes on one developer's machine, in
exchange for an OpenSSL to bundle, to keep current and to measure a floor against.

Three smaller decisions, one per project and one shared.

*memcached's libevent is pinned here and linked statically.* It is the only library memcached needs,
and taking the runner's would make each artifact carry whatever that image happened to have — the
thing this repository levels out everywhere else — while leaving a shared object to bundle and
re-point afterwards. 2.1.13-stable is written down with its SHA-256 and checked, which is one better
than `ruby_unix.py` does for the three libraries it pins and costs three lines. Its BSD-3 text ships
beside memcached's, because after a static link there is no file in the archive that came from it and
a walk over the tree would find nothing to license.

*memcached is built without `--enable-shutdown`, on purpose.* It would give the supervisor a graceful
stop to send; what it actually gives is an unauthenticated `shutdown` verb on a loopback port that
anything served by the same machine can reach. [ADR
0008](https://github.com/haiquang9994/MixEngine/blob/master/.claude/decisions/0008-no-signal-stop-on-windows.md)
already names Memcached as a service where stopping without a signal costs nothing — a cache has
nothing unflushed to lose — so the artifact is stopped by terminating it, and the smoke test proves
that is enough.

*And the digest memcached publishes is a SHA-1, which is said plainly rather than smoothed over.*
There is a `<tarball>.sha1` beside every release and nothing stronger anywhere. SHA-1 is not
collision-resistant, so what that check is worth is exactly what it is: proof that the bytes fetched
over TLS from memcached.org are the bytes memcached.org describes, not proof against an attacker who
can choose both halves of a pair. The transport is doing most of the work, and the recipe prints
which algorithm it used so a reader is not left to assume the strongest one. Redis needs no such
paragraph: `redis/redis-hashes` states a SHA-256 and the URL for every tarball it has ever published,
in one document, which is the same trade `caddy.py` makes with a release's own checksums file.

One thing this row did change outside itself. `download.redis.io` answers **403** to
`Python-urllib/3.x` and 200 to any other `User-Agent`, on the same URL in the same second — so
`borrow.fetch` gained a `headers` argument, defaulting to none so that the other eight recipes are
untouched, and `tools/redis.py` passes a `User-Agent` naming itself. Without it the recipe resolves a
version perfectly and then dies on the download with a status that reads like the release was
withdrawn.

The proof is Caddy's, asking a cache's questions. Redis: the archive is moved somewhere it has never
been, `redis-server` starts against a rendered `redis.conf`, `redis-cli ping` answers, `INFO server`
is checked against the version this archive claims to be — which is what catches a `redis-cli`
talking to some *other* Redis the runner already had running, and the reason neither recipe uses a
fixed port — a key is written and read back, and the server is stopped with `redis-cli shutdown
nosave`. memcached has no client to prove anything with, since `bin/memcached` is the entire archive,
so its smoke test speaks the text protocol over a socket: `version`, then `set` and `get`, then a
terminate and a bounded wait.

### nginx, where upstream's own binary turned out to be the specification

| OS / arch | Range | How |
| --- | --- | --- |
| Windows x86_64 | **1.26.0 – newest** | **borrowed** — upstream's own `nginx-<version>.zip`, repacked |
| Windows aarch64 | — | **empty**; upstream publishes one Windows build and it is 32-bit x86 |
| macOS aarch64, x86_64 | **1.26.0 – newest** | **built** from upstream's source release |
| Linux x86_64, aarch64 | **1.26.0 – newest** | ditto |

The shape is PHP's — borrow where the publisher builds, compile where it does not — and the
interesting part is what happened when the two halves were asked to mean the same thing. Run
`nginx -V` on upstream's Windows binary and it prints the configure line it was built with. That
line is not a curiosity; it is a **specification**, and the four compiled cells are configured
against it rather than against anybody's taste in modules: the same twenty-two `--with-` flags, the
same three libraries, the same empty prefix. Which modules a version of nginx has here is upstream's
decision, transposed, and `tools/nginx.py` compares what every cell actually reports back against
that constant — so an upstream that changes its build stops the recipe instead of quietly publishing
a row that means two things. A check that read only the compiled side could not tell those apart.

The floor of **1.26.0** was measured the same way rather than chosen. Every Windows zip from 1.26.0
to 1.31.3 carries exactly those twenty-two flags; 1.24.0 carries twenty, missing `stream_realip` and
`stream_ssl_preread` — so a 1.24 row would mean one thing on Windows and another everywhere else,
which is *One version means one thing* broken by a version number. The second reason is worse:
1.24.0's zip is linked against **OpenSSL 1.1.1t**, which stopped receiving public security fixes in
September 2023, and a published zip is frozen. Borrowing it means shipping that OpenSSL forever.

**nginx publishes no digest of anything.** Not a `.sha256`, not a checksums file, not a line in an
API — 594 tarballs and 331 zips, each with a detached PGP signature and nothing else. Every other
recipe here checks a download against a digest its publisher states, and keeping that property means
verifying the signature. The trust anchor is not the wire: seven key fingerprints are pinned in
`tools/nginx.py`, the key files are fetched from `nginx.org/keys/` and each is checked against its
pin *before* it is imported, and a signature is accepted only when gpg's machine-readable status
output names one of those fingerprints in a `VALIDSIG`. Fetching a key over the same connection as
the archive would prove only that one party served both. Two things that came out of building it:
`nginx_signing.key` is **three** public keys in one file rather than one — a keyring served under
the name of a key, which is exactly where an extra would go unnoticed, so all three are pinned
individually and a fourth stops the run — and the gpg that exists on a Windows runner is Git's,
an MSYS program that reads `--homedir C:\...` as a *relative* path and prepends its own working
directory to it. A relative homedir is the one spelling both builds understand.

**The Windows artifact is a 32-bit x86 binary, and that is upstream's only Windows build.** There is
no `-win64` asset in any of its 331 zips, at any version, and there never has been. On x86_64 that
runs natively under WOW64 and is what every nginx-on-Windows user is already running. On ARM64 it
would be an i386 payload in an archive whose manifest says `arch: aarch64`, which is a lie in the
index, so that cell is empty and its workflow leg runs anyway to say so. nginx *does* have an MSVC
build system — unlike Redis, this is a build that exists — and filling the cell would mean
maintaining a Windows-on-ARM compiler pipeline with three vendored libraries, for every security
release, to serve one cell. That is the trade *Borrow before you build* exists to refuse.

Because the Windows cell is a borrow, what it cannot do is upstream's decision rather than this
repository's, and the artifact says so in `lacks`: nginx.org calls its own Windows build a beta,
uses only `select()` and `poll()`, starts several workers of which one does any work, and supports
no UDP. A daemon reading that can decline to render `worker_processes auto;` on a cell where the
extra workers do nothing, instead of being mysteriously slower on one platform. **HTTP/3 is off on
every cell** for the same reason: QUIC is UDP, and a row where `listen ... quic` works on four cells
and is a parse error on the other two is exactly the asymmetry the module table exists to prevent.

Two things about the compiled cells. The three libraries — **PCRE2 10.47, zlib 1.3.2 and OpenSSL
3.5.7**, the versions upstream compiled into the newest Windows binary — are handed to nginx as
*source directories*, which is how nginx's own build compiles them in; the result imports nothing
outside the C runtime, and `relocate.verify` is what says so. And the prefix is **empty**, which is
upstream's own answer to relocation and not an invention here: `--prefix=` makes configure define
`NGX_PREFIX` not at all, and nginx then takes its prefix from `-p` or from the working directory.
The trap is that the sub-paths do not survive it evenly — with an empty prefix `conf/nginx.conf`
compiles to `/conf/nginx.conf`, and on Unix a leading slash *is* absolute while on Windows nginx
wants a drive letter and prepends the prefix anyway. So the compiled-in default finds the config on
one platform and looks in the filesystem root on the other. Nothing relies on it. The contract,
proven on every cell, is

```
nginx -p <instance> -c conf/nginx.conf -e stderr
```

— a prefix, a config path *relative* to it, and an error log on stderr where a supervisor can
capture what nginx says before it has read a config file. `make install` is not used at all: with an
empty prefix it would install into the root of the filesystem, so the tree is assembled by hand,
which is also how it comes to match the borrowed cell's exactly.

The proof is Caddy's with one thing Caddy's cannot have: **nginx has no admin endpoint**, so there
is no configuration to read back and no way to ask the server what it is running. A reload is
`-s reload` against a running master, and what proves it took is the request. So the smoke test
renders a configuration, serves a request from the moved tree, **rewrites the configuration so the
body changes**, reloads, and waits for the *new* body — a reload that did nothing leaves the old one
being served indefinitely, which passes any check that only asks whether the process survived. It
also waits rather than reading once, because `-s reload` returns as soon as the master has read the
file while the old workers finish behind it. Then `-s quit`, and a bounded wait for the master to
go. The configuration `include`s the archive's own `mime.types` by absolute path, which is how a
generated config reaches a data file that lives in the artifact — and the path is quoted, because
the tree is moved to a directory with a space in its name on purpose and an unquoted nginx directive
stops at the first one.

One finding belongs to whoever writes MixEngine's nginx recipe rather than to this one. An instance
prefix needs `logs/` **and** `temp/` created before nginx starts. `logs/` is where the pid file goes
and nginx never creates it; `temp/` is subtler, because nginx does create `temp/client_body_temp`
and the four beside it — with a single `mkdir` rather than a chain, so a missing parent is
`[emerg] ... CreateDirectory() failed (3)` on a configuration that passed `nginx -t` one line
earlier. That is not a guess; it is what the first run of this smoke test did.

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

# PostgreSQL: two recipes, because the project publishes no binaries of its own
python tools/postgres.py --version 18 --out dist/         # EDB's, most of which is never unpacked
python3 tools/postgres_deb.py --version 18 --out dist/    # Linux, out of the project's own .deb

# Redis and Memcached: compiled everywhere, because neither project publishes a binary anywhere —
# and refusing to run on Windows, because neither has a Windows build to compile
python3 tools/redis.py --version 8 --out dist/
python3 tools/memcached.py --version 1.6 --out dist/

# nginx: one recipe that borrows on Windows and compiles on Unix, against the configure line it
# reads off the borrowed binary. Needs a gpg — nginx signs its releases and hashes none of them
python tools/nginx.py --version 1.30 --out dist/

# Then regenerate and sign the index from what the releases actually contain
python tools/mkindex.py --base-url … --out dist/index.json
minisign -Sm dist/index.json -s minisign.key

# And, when a publisher moves a support schedule, transcribe it again rather than editing a date
python tools/eol.py            # compare every written date against its publisher
python tools/eol.py --update   # rewrite them from it, and commit the diff
```

In practice none of that is run by hand: `.github/workflows/build-php.yml` takes a version, picks the
recipe from it and produces every target; `build-node.yml`, `build-python.yml`, `build-caddy.yml`,
`build-redis.yml`, `build-memcached.yml` and `build-nginx.yml` do the same with one recipe and six;
`build-ruby.yml` runs six legs across two recipes; and `publish-index.yml` regenerates and signs the
index from every release that exists. `check-eol.yml` is the only one that runs on a clock, for the
reason the next section gives.

Three of those keep legs in the matrix that produce nothing by design — both Windows legs for Redis
and Memcached, the ARM64 one for nginx. An empty cell stated in every run's log is worth a runner
minute; a row somebody has to remember is missing is not. See those two sections above.

`build-mariadb.yml` is the one that is shaped differently, and both differences are MariaDB's rather
than a preference. It runs **three** recipes across six legs, because upstream publishes a binary for
two cells, `.deb` packages for a third and nothing at all for the rest. And it takes a *list* of
versions — `all` expands to every supported series — because MariaDB maintains four at once with
end-of-life dates years apart, so a workflow that took one version would have to be invoked four
times and would miss one.

`build-postgres.yml` takes a list for the same reason, and runs **two** recipes across five legs: EDB
builds three of the six cells and the project's own `.deb` packages cover two more, leaving Windows
on ARM as the only cell nobody publishes anything for. The two macOS legs download the same universal
archive, which looks wasteful and is the point — what each one proves is that *this* Mach-O slice
starts and serves on *this* machine, and a single leg producing both artifacts could only ever have
run one of them.

The borrowed recipes share `tools/borrow.py` — downloading, hashing, unwrapping the publisher's
wrapper directory where there is one, packing, and running a program with a `PATH` the runner cannot
answer. What no two *kinds* share is the smoke test, deliberately: the mechanics are the same for
every publisher and the *claim* is not, and this repository has already been bitten once by two
producers writing the same manifest field to mean two different strengths of proof.

Inside a kind the opposite holds, and for the same reason read backwards. `ruby_smoke.py` is shared
by the two Ruby recipes, `mariadb_smoke.py` by three and `postgres_smoke.py` by two, precisely
*because* each set produces one runtime for one row of the table: two producers of the same thing
that check it differently will drift, and the drift is invisible because they agree on the field
name. Where the routes disagree about a tree's shape the shared module carries every spelling — see
`postgres_smoke.LAYOUT`, which is a table of them and not an `if`.

## Dates are the one claim here that is not about bytes

Everything else this repository publishes is measured. A digest, a load command, a glibc floor, the
version a binary printed about itself when it was told to — all of it can be re-measured from the
archive years later, and the check for it is the measurement again. `data/eol.json` cannot be. "PHP
stops getting security fixes for 8.2 on the 31st of December 2026" is a transcription, and a
transcription with nothing checking it is a rumour with a date on it.

It was checked at P10 for the first time, and in Ruby alone it was wrong three different ways: 3.2
was written 2026-03-31 against upstream's 2026-04-01; 3.4 and 4.0 carried dates nobody had ever
published, extrapolated from Ruby's habit of ending a line on 31 March about four years on; and
3.3's number was right but came from upstream's `expected_eol_date` rather than its `eol_date`,
which is a different claim. PHP, Node.js, Python, MariaDB and PostgreSQL were correct to the day —
which is the shape of this class of bug. It is not that hand-transcription is usually wrong. It is
that nothing tells you which of the forty-four entries is the one that is.

So every date now comes from a machine-readable document its publisher maintains, `tools/eol.py
--update` transcribes them and `tools/eol.py` proves them. Six kinds, six publishers, **no
third-party mirror** — the roadmap expected two of them to need `endoflife.date` and none of them
do. The other four kinds here have no date at all, because Caddy, nginx, Redis and Memcached publish
no schedule; that stays an absence rather than becoming a guess.

Three things about it are worth stating, because each replaced something that looked reasonable.

*The check runs on a clock, not at pack time, because the pattern it grew out of does not
generalise.* `mariadb.py` prints the end-of-life date it saw on every run — free, because the date
arrives in the same document the download does, and enough to catch a moved schedule the next time
that series is packed. But an end-of-life date does not change when something is packed. It changes
on a calendar, and the lines nearest their date are precisely the ones nobody is packing any more:
Ruby 3.2 ended in April 2026 and will never be repacked, so the wrong date would have sat in the
index until a human happened to look. `check-eol.yml` runs weekly, and on any push that touches
either the data or the tool. What stayed in the recipes is the half that costs nothing —
`eol.announce` prints what is written down, makes no network call, and cannot fail a build.

*Each publisher's document is transcribed in full, not trimmed to the versions this repository
offers*, which is why PHP 4.3 and PostgreSQL 6.3 are in the file. The old curated list was the
problem rather than the tidy version of it: **a subset cannot be checked**, because nothing
distinguishes a line deliberately left out from one forgotten. Transcribing the whole document makes
the check an equality, and `mkindex.py` reads the lines it needs and ignores the rest.

*And a corrected date now reaches versions nobody rebuilt.* `mkindex.py` used to apply
`data/eol.json` only to the artifacts a run had just added, which meant the correction to Ruby 3.2
could never have reached the package already published — the file would have been right and the
index would have stayed wrong. It re-dates every package on every run, and **removes** a date the
file no longer states, because un-saying something is as much a part of a correction as saying it.

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
