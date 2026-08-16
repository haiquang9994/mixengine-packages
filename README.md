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
| macOS aarch64, x86_64 | **14 – newest** | **borrowed** — one `osx-binaries.zip`, a **universal** build serving both cells |
| Linux x86_64, aarch64 | **14 – newest** | **borrowed, rearranged** — the project's own `.deb` packages from `apt-archive.postgresql.org` |
| Windows aarch64 | — | nobody publishes one |

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
