# mixengine-packages build plan

This repository releases on its own clock, so it needs its own order of work. What is here is that
order: what is packed, what the rules say about it that is not true yet, and what has not been packed
at all.

Legend: `[ ]` todo · `[~]` in progress · `[x]` done · **(rule)** = a conformance debt against
[*One version means one thing, and no more than is needed*](../README.md#one-version-means-one-thing-and-no-more-than-is-needed).

---

## Where we are

Every row of the runtime table is packed, and both service rows that have been evaluated are packed.
What is *not* done is the rule itself.

| Kind | Cells | Recipes | Conforms |
| --- | --- | --- | --- |
| PHP | 7.0 – newest, 6 targets | `php_windows`, `php_unix`, `php_legacy_unix` | yes — P2 |
| Node.js | 16 – newest, 6 targets | `node` | yes — P3 |
| Python | 3.10 – newest, 6 targets | `python` | yes — P4, P4a and P4b |
| Ruby | 3.2 – newest, 6 targets | `ruby`, `ruby_unix` | yes — P5 and P5a, less P5b |
| Caddy | 2.0 – newest, 6 targets | `caddy` | yes |
| MariaDB | 10.6 – newest, 6 targets | `mariadb`, `mariadb_deb`, `mariadb_build` | yes — it is where the rule came from |

The rule was written **after** MariaDB, because MariaDB is what taught it: three routes to one
version produced three different feature sets, and fixing that is what
[`10d4e81`](https://github.com/haiquang9994/mixengine-packages/commit/10d4e81) and
[`6c344d0`](https://github.com/haiquang9994/mixengine-packages/commit/6c344d0) began. They did not
finish it: a later audit of the six *finished* artifacts of a green run found four more asymmetries
no recipe knew it had, and closing those took seven further commits; what that audit found is
written down in [the README](../README.md#one-version-means-one-thing-and-no-more-than-is-needed),
because it is the argument for P6. The rule has never been applied backwards to the four runtime
rows that were packed before it existed, and P1–P6 are that work. Measured against upstream's own
archives, the gaps were not marginal: PHP 8.3 on Windows was missing two extensions this repository
fails a Unix build over, which P2 closed; Node.js 24.19.0 was 106 MB on Windows against 198 MB on
Linux, which P3 closed; CPython 3.13.15 shipped Tk 8.6 to Windows and Tk 9.0 to Unix under one
version number, which P4 closed by shipping neither; and Ruby 4.0.6 on Windows was 276 MB of which
225 MB was documentation the four compiled cells are configured never to build, which P5 closed.
Four times now a task has come out somewhere other than where it was pointed — P3 kept npm's manual
pages after going and reading npm, P4a keeps 30 MiB of a shared library nothing in the archive
loads, P4b found its own title wrong and two of its stated reasons for existing wrong with it, and
P5a was written to close an asymmetry that a look at the six trees says is not there. That is what a rule is
for, and it is the argument for measuring the artifacts rather than the release notes: every one of
those four was written from what the publisher says and corrected by what the publisher ships.

Nothing below is a rewrite. Every recipe already downloads, verifies, relocates, proves and packs
correctly; what they do not do is *choose*, and choosing once is the whole of the rule.

---

## Conformance — apply the rule to the rows packed before it

### [x] P1 — Give the borrowed recipes somewhere to say what they took out **(rule)**

`upstream.removed`, `upstream.added` and `upstream.stripped` are what keep "borrowed" checkable
against what the publisher shipped. `tools/python.py`, `tools/mariadb.py` and `tools/mariadb_deb.py`
wrote them — the three recipes written against the rule, or corrected under it. `node.py`,
`php_windows.py` and `ruby.py` had no parameter for it at all, so even a correct decision made in
P2–P5 would have had nowhere to be declared. `borrow.publish` already carries whatever the manifest
holds, so this was a `describe(…, added, removed)` signature in three recipes and nothing more.

**First, because P2–P5 each end in a manifest field that does not exist yet.** `php_windows.py` does
not use `borrow.py` at all — it predates it — so it either grows the two arguments itself or is moved
onto `borrow` in passing; moving it is the larger change and is not required to close this.

Closed as `borrow.declare`, which is one function rather than four copies of six lines, and which
does one thing the fields did not do before: **it checks the claim against the tree before writing
it.** A path in `added` has to be there, a path in `removed` has to be gone — by `os.path.lexists`,
because a dangling symlink is still a file in the archive and `exists` follows the link and answers
no, which is precisely how `mysql_ldb` survived four rounds of being excluded. `python.py` gave up
its own copy of the two fields to it; `php_windows.py` imports `borrow` for this one function and
keeps fetching, hashing and packing by itself, because moving the rest is still the larger change.
The MariaDB recipes are the remaining writers of `upstream.*` outside `declare`, and they also write
`stripped`, which nothing else does — folding them in belongs with P6, where the check that reads
these fields is written.

### [x] P2 — PHP: one extension set, chosen once **(rule)**

The widest gap here, and the only one that contradicts something this repository already enforces
elsewhere.

Measured on `php-8.3.33-nts-Win32-vs16-x64.zip` — 88 entries, 90.9 MB unpacked, 40 DLLs in `ext/`
totalling 28.0 MB — against `php_unix.py`'s `STATIC_EXTENSIONS` and `php_legacy_unix.py`'s `PECL`:

*Absent from the Windows archive in any form:* **`redis`, `mongodb`**, `igbinary`, `xdebug`, `yaml`,
`zstd`. The first two are the pair `php_legacy_unix.py` fails a build over — *"MixEngine offers
{package} on every version it ships, so an artifact without it is not one worth publishing"* — and
the Windows leg publishes without them and says nothing. They exist as official Windows DLLs
(xdebug.org publishes its own; PECL publishes Windows builds of the rest), so this is a download and
a load-check per branch, not a compile.

*Present only on Windows:* `odbc`, `pdo_odbc` — an ODBC bridge is the README's own example of what a
local development environment does not do — plus `oci8_19`, `pdo_oci`, `pdo_firebird`, `snmp`,
`imap`, `enchant`, `tidy`, `gettext`, `com_dotnet`, and **`dl_test` and `zend_test`, which are PHP's
own test extensions**. Also `dev/php8.lib`, 892 KB of import library in a runtime that is not an SDK.

*Different in kind rather than in presence:* `curl`, `openssl`, `mbstring`, `intl`, `gd`, `zip`,
`sodium`, `sqlite3` and `fileinfo` are compiled in on Unix and are loadable modules on Windows. That
one is **not** a defect to fix here — a static extension cannot be turned off, and Windows publishes
no build with them static — but it means the Windows artifact only behaves like its Unix twin if
whoever installs it enables that set. `extensions.shared` already names them; what this task owes is
the artifact saying that the set is *expected to be enabled* rather than merely available.

Resolve each difference in one direction for all six cells, and **say so beside the other recipe** —
a deletion in the packer and a `--with-` in the compiler are one decision written twice, and the rule
is explicit that each has to name the other.

Closed, and not beside the other recipe but *inside* it: the set is `tools/php_parity.py` and all
three recipes read it, because "say so beside" is a comment and comments do not fail a build. The
extensions this repository adds are one list now, reached three ways — compiled in on 8.1+,
`phpize`d on 7.x, downloaded from php.net's own PECL builds on Windows, where all six of them exist
for every branch except `zstd` before 7.2, so 7.0 and 7.1 do without it on all six cells rather than
on four.

The surplus is thrown out by a **keep-list rather than a delete-list**, which is the one decision
here worth arguing with. A delete-list is written against the archive somebody measured, 8.3's,
and says nothing about 7.3's `php_interbase.dll` or about 8.6; the keep-list answered for all
eleven branches without being told about any of them, dropping `xmlrpc`, `interbase`,
`phpdbg_webhelper` and `oci8_12c` on the old ones as a matter of course. Two consequences came with
it. `com_dotnet` goes too — P6 below expected it to stay as a named exemption, and "the platform
has no equivalent" turned out to be a reason to look at the feature rather than to keep it. And
4.7 MB left with it: `prune` deletes libraries **by reachability**, computed with the publisher's
own `deplister.exe` and then deleting that too, so dropping `enchant` takes 3.0 MB of GLib with it on every branch without a
table saying which library belongs to which extension.

Three things the task found that reading could not:

* **PHP 7.0 through 7.4 call GD `php_gd2.dll`**, and 8.0 renamed it. A keep-list matched on file
  stems threw GD out of five branches *silently* — nothing loads what is no longer there — and it
  was caught by running the recipe against 7.0. The file is renamed to the extension inside it
  now, and `php_parity.check` reads the whole compiled-in set rather than only what this
  repository adds, which is the check that would have failed.
* **`oci8`, `pdo_oci` and `pdo_firebird` cannot load at all** in the archive as published: they want
  client libraries the publisher does not ship. `snmp` loads and creates `C:\usr\snmp\persist` on
  the way up. Three of the fifteen dropped extensions were never usable.
* **Windows 7.0 has no `readline` and no `dba`**, and 7.0–7.1 carry `mcrypt` compiled in with
  7.0–7.3 carrying `wddx`. None of the four is closeable by borrowing, so they are named in
  `php_parity` beside `pcntl` and `posix` — the exemption list P6 needs, measured not guessed.

`extensions.enabled` is the third half of this. `shared` said "available" about `curl` and about
`odbc` alike, so nine extensions that are compiled into every Unix cell and are loadable modules on
Windows had no way to be described as *expected*. `static ∪ enabled` is now what a cell does and
`static ∪ shared` is what it could be asked to do, which is the comparison P6 wants. The Windows
smoke test also stopped proving one extension and started proving all of them, which is both the
drift `php_smoke` exists to name and the only thing standing between a reachability sweep and a
library deleted in error.

Proven on 7.0, 7.4 and 8.3 end to end, on Windows: every extension in each archive loads from a
relocated tree, and the 8.3 artifact is *smaller* than upstream's zip while carrying six extensions
it did not have.

### [x] P3 — Node.js: decide what `include/node` is for **(rule)**

One recipe, six cells, and it chooses nothing — so the cells differ by whatever upstream happened to
put in each archive. Node 24.19.0, measured:

| | files | unpacked |
| --- | --- | --- |
| `win-x64.zip` | 1,989 | 106.1 MB |
| `linux-x64.tar.gz` | 4,708 | 198.5 MB |

The 92 MB is almost entirely **`include/node/**` — 2,726 headers, 59.0 MB, 29.7% of the Linux
archive**, down to OpenSSL headers for `solaris-x86-gcc`. The Windows zip has no such directory, so
one version already means two things. Both halves also carry ~3.0 MB of `npm/docs` and `npm/man`.

The decision is genuinely a decision rather than an obvious deletion, which is why it is a task:
`node-gyp` wants those headers, and fetches them from nodejs.org itself when they are absent. So
either they are dropped everywhere and `node-gyp` keeps working over the network, or they are kept
everywhere — which means *adding* them to the Windows cell, from upstream's own
`node-v<version>-headers.tar.gz`. What is not allowed is the status quo, where the answer is
whichever one the publisher chose per platform.

Dropped, on all six cells, and the argument that settled it is not the one above. **`node-gyp` does
not read those headers.** It looks inside the runtime it is running under only when the build set
`use_prefix_to_find_headers` — a flag distributions pass so their `-dev` package can compile
offline — and every official build has it false, read out of the `process.config` baked into the
Linux 24.19.0 and 26.7.0 binaries rather than assumed. With it false there is no choice left to
make: `configure.js` downloads `process.release.headersUrl` into `~/.node-gyp/<version>` and
compiles against that. Which is why native modules have always built on Windows against an archive
carrying no `include/` at all — the platform that already answers the question is the one this
copies, and "dropped everywhere and `node-gyp` keeps working over the network" is not a trade so
much as a description of what already happens on three of the six cells.

*Keep them everywhere* was also not reachable, which the task did not know. `node-gyp --nodedir`
pointed at an installed tree on Windows links against `<nodedir>/$(Configuration)/node.lib`, a path
out of a **build** tree; the headers tarball has never contained one and upstream publishes it
separately, per architecture. The symmetric option was never "add 59 MB to three cells" but "add
59 MB and a fourth download, and still be told the two halves are not the same thing".

**A keep-list again**, for the reason P2 gives, and it earned itself twice over on a row where a
delete-list would have looked sufficient: measured across 16.20.2, 20.19.5, 24.19.0 and 26.7.0 on
both platforms, one naming `include` and `share/{doc,man}` would have shipped Node 16's
`share/systemtap` and its `node_etw_provider.man`. Neither had been seen by anything here. What
`tools/node.py` keeps at the root is now the interpreter, its libraries, the launchers already
named in `LAYOUT`, and `LICENSE`; everything else there goes, including `install_tools.bat` — which
is not a tool but a Chocolatey install of Python and the VC build tools onto the whole machine —
and `CHANGELOG.md`, the only file whose *contents* differed between the two cells for a reason that
is not line endings.

The one thing the task expected to delete and this kept: npm's 2.7 MB of `docs/` and `man/`. All
three parts are read by a documented command — `npm help-search` reads `docs/content`, `npm help`
opens `docs/output/*.html` on Windows and runs `man` against `man/man[1-7]` on Unix — and both
cells already carried all three, so the rule's answer is keep. "No more than is needed" is a claim
about need, and the way to settle it was to go and read npm rather than to weigh the directory.

Proven on 16.20.2, 24.19.0 and 26.7.0: packed end to end on Windows, and `prune` run against the
real Linux tarball of each, which is as far as a Windows machine can take the other half. Node
24.19.0 on Linux goes from 4,708 files and 198.5 MB to 1,977 and 138.8 MB — and once both cells are
pruned, **the entire remaining difference between the Windows and Linux trees is `node.exe` against
`bin/node` and the per-shell launchers beside it.** Every other path matches.

### [x] P4 — Python: tkinter, and the same two questions **(rule)**

`tools/python.py` is the recipe that already does this properly — `install_only_stripped`, `_crypt`
removed with the reason written down, `upstream.added` and `upstream.removed` both populated. Two
things are left.

**tkinter is described as excluded and is not excluded.** `MODULES` says *"tkinter is deliberately
not here — upstream ships it, it needs a display library on Linux, and nothing a local web
development environment does touches it"*, which is exactly the rule's own test for *dropped
everywhere* — but the comment only removes it from the smoke test. cpython 3.13.15 still ships
`tcl/`, `tcl/tcl86t.lib` and `tcl/tk86t.lib` on Windows and `lib/itcl4.3.8/*.a` on Linux. Either
delete it on all six cells and record it in `upstream.removed`, or delete the claim.

**Headers and `libs/python313.lib` stay, and should say why.** 1.8 MB of `include/` on both systems
plus 563 KB of import libraries on Windows — the same shape the README rules out — except that here
they are load-bearing: `pip install` of an sdist with a C extension links `python313.lib`. That is a
decision the rule permits ("something it would reach for is enabled everywhere"), and what it is
missing is being stated rather than merely being true. 62.7 MB Windows against 98.3 MB Linux is a gap
that should be explainable line by line after this task.

Both closed, and the first one turned out not to be an argument about size. **Upstream ships a
different tkinter to each half of the table**: Windows carries Tk 8.6 with Tix 8.4.3, Unix carries
Tk 9.0 with itcl 4.3.8 and the Tcl Thread package, and 3.14 replaces the Windows DLLs with Tcl 9
and moves the entire Tcl script library inside them. So `python 3.13.15` meant one toolkit on one
machine and another on the next before anything here chose, which is the rule's first half failing
in a way the *"needs a display library on Linux"* comment never described. Dropped rather than
levelled up, by the rule's own test: nothing a local web development environment does draws a
window, and the two things in the standard library that need a display are the toolkit and IDLE.

**A keep-list twice more**, in the two places the surplus versions itself in its own name — `lib/`
on Unix keeps `python3.X`, `libpython3*` and `pkgconfig` and nothing else, `libs/` on Windows keeps
`python3*.lib` — and names elsewhere. It earned itself twice, again: `libs/` on 3.10 and 3.11 holds
**31 per-extension import libraries** (`_socket.lib`, `_tkinter.lib`, one per built-in module) that
3.12 onwards does not, which exist to link a module *into* a Python being compiled and are opened
by nothing that installs a wheel; and `lib/itcl4.3.8` and `lib/thread3.0.6` carry their versions in
their names, so a delete-list would have been out of date at the next Tcl release. `share/man` goes
with them on Unix — one manual page whose Windows twin has never had one.

**And then a sweep that fails the pack if any part of any path still matches Tcl or Tk**, which is
what makes the drop a rule rather than a list. It is not decoration: 3.14 renamed `tcl86t.dll` and
`tk86t.dll` to `tcl90.dll` and `tcl9tk90.dll`, and a recipe written against 3.13 would have shipped
both in silence — the `php_gd2.dll` failure from P2 exactly, one runtime over. The sweep also found
the one path in either tree that looks like the toolkit and is not, `share/terminfo/t/tkterm`, an
ncurses description of a terminal emulator; it is named in `NOT_TOOLKIT` rather than dodged by
narrowing the sweep to where Tcl lives today, since moving Tcl is precisely what 3.14 did.

The second question closed the other way, and the reversal is the useful part. `include/` and
`libs/python3XX.lib` are kept — the opposite of P2, which deleted `dev/php8.lib` as *"892 KB of
import library in a runtime that is not an SDK"* and was right to. A PHP extension reaches a
developer as a DLL somebody else compiled; a Python extension frequently does not, because
`pip install` of any sdist without a matching wheel compiles C on the installing machine against
`Python.h`. So **"no more than is needed" is a question about the runtime, not about the file
type**, and the answer now travels in the artifact: a top-level `keeps` mapping each exempt path to
why, written by `borrow.declare`, which refuses a `keeps` naming something the tree does not have.
The schema carries it and P6 is what will read it. The smoke test proves it from the other side —
it asks the relocated interpreter where its headers are and requires `Python.h` to be there, and
requires `import tkinter` to fail, because a deletion checked only against the file system is
checked only in the direction that cannot go wrong twice.

**A latent bug the simulation caught**, which was in `site_packages` before this task and would have
bitten on macOS rather than here: `tree / "Lib"` is answered *yes* by a case-insensitive file system,
and a default macOS install has one. Deciding "is this a Unix layout" by looking for `lib/python3.*`
first cannot be answered by accident; asking for `Lib` first pointed every caller one level above the
standard library on two of the six cells.

Proven on 3.10.21, 3.13.15 and 3.14.7 — three different Tcl layouts — packed end to end on Windows,
and `prune` run against the real Linux *and* macOS tarballs of each, which is as far as a Windows
machine reaches. All three manifests validate against the schema. What the gap looks like now, on
3.13.15: Windows 59.8 MB → 48.1, Linux 93.8 → 83.5, macOS 61.4 → 52.2. What is left of it is P4a.

### [x] P4a — Python: Unix ships the interpreter twice **(rule)**

P4 owed an explanation of the Windows/Linux gap line by line, and produced one. After it, 35.4 MB
of the remaining 35.4 MB is two things, both Unix-only, and neither is a feature:

**`lib/libpython3.13.so.1.0` is a second complete copy of the interpreter — 30.3 MB on Linux,
16.7 MB on macOS — and nothing in the tree loads it.** `bin/python3.13` links CPython statically:
read out of the ELF's own `DT_NEEDED` it asks for `libpthread`, `libdl`, `libutil`, `librt`, `libm`
and `libc` and for no `libpython` at all, and the macOS binary's `LC_LOAD_DYLIB` list says the same.
Nor does anything else: of the twelve dynamically linked ELF files in a 3.13.15 Linux tree, **zero**
name it. Windows carries `python313.dll` once and that one is the interpreter. This is the same
shape as `include/node` — weight one publisher gives one platform, that the platform's own
executable never opens — with one difference that makes it a task rather than a line in P4:
deleting it is what stops `python3-config --libs --embed` from linking, so it is a decision about
whether a MixEngine Python is ever *embedded* in another process, and that deserves being asked
rather than assumed. `_sysconfigdata__*.py` names the file and would need to stop.

**`share/terminfo` is ~1.9 MB and 2,800 files, and whether the bundled ncurses can even reach it is
not measurable from here.** The interpreter has `readline` and `curses` compiled in, so a terminfo
database is genuinely wanted; the question is whether these builds look for it beside themselves or
at the `/tools/deps` prefix they were built under — in which case the copy in the archive is
unreachable, the system's own database answers, and this is 1.9 MB of nothing. Settling it needs a
Linux machine with `TERM` set and the system database moved out of the way. Windows has no `share/`.

**The terminfo half needed no Linux machine after all, and the answer is that the database is
unreachable.** ncurses does not guess where its database is; the search order is compiled into it,
and reading the strings out of `bin/python3.13` itself gives the whole of it: `$TERMINFO`, then
`$TERMINFO_DIRS`, then `~/.terminfo`, then the three absolute paths built in at configure time —
`/etc/terminfo:/lib/terminfo:/usr/share/terminfo`. **Not one of them is inside the tree**, and no
`<prefix>/share/terminfo` string exists in the binary to be found. So a MixEngine Python on Linux
reads the machine's own database, as it would have with or without this directory, and the 2.2 MB
and 1,833 files it ships are answered by nobody. It is also a directory *four of the six cells do
not have* — macOS ships `share/man` and nothing else, Windows ships no `share/` at all — so this is
the rule's first half failing quietly on one platform. The whole of `share/` goes on Unix now rather
than `share/man` alone, and the deletion took the sweep's exemption list with it: `share/terminfo`
was the only entry in `NOT_TOOLKIT`, the one path in either tree matching `TOOLKIT` without being
the toolkit, and an exemption for a directory that no longer ships is exactly the stale claim
`borrow.declare` was taught to refuse. Better to delete the directory than to keep explaining it.

**The library half closed the other way, and the reversal is the point of the task.**
`lib/libpython3.13.so.1.0` stays, on all four Unix cells, and the reason is not that the measurement
was wrong — it is right, nothing in the archive loads that file. It is that *the archive says the
file is there for something outside itself to link*, in three separate places, and all three are
upstream's own words rather than an inference:

* `lib/libpython3.13.so` is a **symlink** to it. The runtime loader resolves libraries by `SONAME`
  and never looks at that name; the only program on a Unix machine that opens `libfoo.so` is a
  linker. Upstream shipped a file whose sole consumer is `ld`.
* `bin/python3.13-config` is written to survive relocation — it computes `prefix_real` from its own
  path with `dirname`/`readlink` before substituting it into every variable — and `--ldflags
  --embed` prints `-L<tree>/lib -lpython3.13`. Not a build-time path left behind; a path deliberately
  recomputed for the tree it finds itself in.
* `PY_ENABLE_SHARED="1"` sits in that same script and in `_sysconfigdata`, and it is the flag that
  *suppresses* the fallback to the config directory. With it set there is nowhere else to look.

Deleting the library would leave an artifact describing a file it does not carry, on three counts,
and the only repair would be rewriting `python3-config` and `_sysconfigdata` — editing the
publisher's record of its own build, which is a worse thing to ship than 30 MiB. Windows settles the
parity question in the same direction with no choice at all: `python3XX.dll` **is** the interpreter
there (`python.exe` is a 91 KB launcher) and `libs/python3XX.lib`, kept by P4 so a C extension can
compile, is the same file an embedder links. Two cells can embed a Python whatever this repository
decides, so dropping the Unix library would not be a saving but a way of making one version mean two
things again — the failure P4 had just finished closing.

So it is declared instead. `keeps` gains three entries per Linux cell and one per macOS cell — the
library, its linker symlink, and `lib/libpython3.so`, the 20 KB stable-ABI forwarder that is what
`python3.dll` is on Windows — each with the reason travelling in the manifest. **This is the case
the field was worth building for**, and it is not the one P4 built it for: no per-artifact check
would ever flag a `.so` in `lib/`, so nothing but a written reason could stop the next person from
noticing 30 MiB of apparent duplication and deleting it. The smoke test proves it from the far side,
the way it proves `Python.h`: it computes the path `python3-config` is about to hand a linker and
requires the file to be there, after the tree has moved.

That check earned itself on its first run, and on the platform it does not apply to. It first asked
whether `sysconfig` reports an `LDLIBRARY` at all, on the assumption that Windows reports none —
and Windows reports `python313.dll`, a real file that lives at the root rather than under `lib/`,
described by a variable meaning *"what a POSIX linker is given"* on a platform with neither a POSIX
linker nor a `python3-config` to hand anything over. The Windows pack failed on a file it was never
missing. It asks about the platform now. Two other things worth keeping: `sysconfig`'s own `LIBDIR`
is still `/install/lib`, the build machine's path, so the check has to reconstruct the directory the
way the shell script does rather than believe what the interpreter reports; and there is no
`libpython*.a` in any `install_only` tree, so a static link was never on the table.

**And the gap P4 promised to explain line by line is now explained.** Pruned, on 3.13.15: Windows
48.1 MiB, macOS 52.2, Linux 81.4. Linux is 33.3 MiB larger than Windows and **30.3 MiB of that is
the library this task decided to keep**; macOS is 4.1 MiB larger while carrying a 16.7 MiB duplicate
of its own, which is to say every other part of the macOS tree is *smaller* than its Windows twin.
What is left over on Linux after the library — about 3 MiB — is not surplus but shape: OpenSSL,
SQLite and the rest are compiled into the interpreter there and shipped as separate DLLs on Windows,
which is the same feature set spelled two ways, the way P2's static-versus-shared PHP extensions
are. There is one thing left that *is* surplus, and it is in the binaries themselves: P4b.

Run against every tarball of 3.10, 3.11, 3.12, 3.13 and 3.14 on all three operating systems —
fourteen cells, every one clean through `prune`, `keeps` and `borrow.declare` — and packed end to
end on Windows for 3.10.21, 3.13.15 and 3.14.7, all three validating against the schema.

### [x] P4b — Python: the stripped variant is not stripped **(rule)**

`tools/python.py` takes `install_only_stripped` and says why at length: *"the same tree without the
debug symbols, and the saving is not marginal"*. Measured on the tarballs rather than on the
release notes, that is true of the tree and **not true of the two largest files in it**. Reading
the ELF section headers of Linux 3.13.15:

| | file | in sections the loader never maps |
| --- | --- | --- |
| `lib/libpython3.13.so.1.0` | 31,801,480 | **11,748,357** |
| `bin/python3.13` | 31,372,864 | **2,790,993** |

14.5 MB of an 85.3 MB tree, in sections with no `SHF_ALLOC` bit — nothing in a running process ever
reads them. Most of it is one section: `.rela.text`, 6.5 MB, which is a *relocatable object's*
table appearing in a finished shared library, the signature of a link done with `--emit-relocs` so
that a post-link optimiser could run. It arrived with 3.12 — 3.10 and 3.11 have none of it and only
1.6 MB of non-allocated weight altogether — and 3.14 carries 12.2 MB, so this grows rather than
settles. macOS is milder and the same in kind: `LC_SYMTAB` is 1.5 MB of the 3.13.15 dylib.

The obvious fix is the platform's own `strip`, and it is available: `build-python.yml` runs each
cell on its own native runner, so binutils is on the Linux legs and the Xcode tools on the macOS
ones. Two things make it a task rather than a line in P4a. It would be the second recipe to shell
out to a toolchain binary — PHP's `deplister.exe` is the first — and it would be the first to
*modify* a borrowed executable rather than delete files around it, which moves the smoke test from
"proves the archive is complete" to "is the only thing standing behind a mutated interpreter".
And the smoke test cannot cover what P4a just kept `libpython` for: it runs the interpreter, it
does not link anything against the library, so a `strip` that damaged the dynamic symbol table
would pass every check in the file and fail in somebody's `pip install`. Settle what proves a
stripped library still links before stripping one.

**The title is wrong and the variant is honestly named**, which is the first thing reading it as a
diff against `install_only` shows. Upstream's step removed every `.debug_*` section and every
`.rela.debug_*` beside it and touched nothing else: 80 MB out of `bin/python3.13`, 207 MB out of
`lib/libpython3.13.so.1.0`, a tarball of 118.5 MB becoming one of 34.8 MB. What it left is what
`--strip-debug` was never going to take. So the recipe's own sentence — *"the same tree without the
debug symbols"* — was exact, and the 14.5 MB measured above is not debug information at all.

It is two things with different owners, and the archive names the second one itself:

- **A symbol table**, on every version from 3.10 on and on both Unix formats: `.symtab` and
  `.strtab` on Linux, `LC_SYMTAB` on macOS. 1.6 MB per binary on 3.10, 2.4–2.8 MB on 3.13.
- **8.8 MB of relocations that were an input to an optimisation that never ran on the file holding
  them.** `.note.bolt_info` — a note llvm-bolt writes and nothing else does — is in `bin/python3.X`
  on 3.12, 3.13 and 3.14 and in no library; `.rela.text` is in the library on those same three
  versions and in no executable. So BOLT ran on the executable and consumed its relocations, while
  the library was linked with the same `--emit-relocs` and then left alone. That is why the weight
  appears in 3.12 and grows to 12.2 MB by 3.14, and it is why no amount of `strip -d` finds it.

Both come out together whether one wants them to or not: `.rela.text` names its symbols by index
into `.symtab`, so a tool that removes the table removes the relocations with it.

**Windows decides the direction, from the far side.** Its `install_only_stripped` ships **no `.pdb`
at all** — zero files, of 3,303 — so a Windows cell has already had its symbols taken and no
levelling-up is available. Taking the other two down to it is the move `prune` made with tkinter, in
the same direction and for the same clause of the rule.

#### The proof, which is what the task actually asked for

The check is `python.mapped`, and it is structural rather than empirical because the empirical proof
does not reach: the file most at risk is the one P4a kept *because nothing in the archive loads it*,
so a smoke test may start the interpreter a thousand times without touching it. Instead — if every
byte the loader maps and every table the linker reads is identical before and after, the two files
cannot behave differently, and running either one would say no more than that. On ELF the line is
drawn by the hardware: `SHF_ALLOC` is exactly the set `strip` may not touch, and `.dynsym`,
`.dynstr`, `.gnu.hash`, `.dynamic` and the loader's `.rela.dyn`/`.rela.plt` are all inside it.
Program headers are compared whole and separately. Measured across the operation on ten cells: 26
allocated sections identical, 9 program headers identical, byte for byte, including on the BOLTed
executable whose layout was rewritten after the linker was done.

**The flag is not the same on both Unix halves, and that is the finding, not a portability wrinkle.**
ELF keeps two symbol tables and a linker reads only the allocated one. Mach-O keeps *one*, whose
exported range `LC_DYSYMTAB` indexes — and `--strip-all` there empties it: measured on
`libpython3.13.dylib`, 1,755 exported symbols become 0 and `_Py_Initialize` stops existing in the
file, to save 66 KB more than `-x` saves. So `--strip-all` on Linux and `-x` on macOS.

Two more things only measuring found:

- **The arm64 cells are ad-hoc signed and the kernel answers a stale signature with `SIGKILL`.** Not
  an error a caller prints — the process does not start. And it would pass every other check here,
  for the same reason the library is unreachable. So `python.countersigned` recomputes the
  CodeDirectory's 4,230 page hashes rather than trusting the tool to have re-signed; corrupting one
  byte makes it name page 1420. `x86_64-apple-darwin` carries no `LC_CODE_SIGNATURE` at all, so the
  check is real on two cells of six and answers `None` on the rest rather than calling that a fault.
- **`mapped` refused its own first run, and was right to.** Comparing Mach-O segments whole reported
  `__TEXT` as changed on every *successful* strip, because `__TEXT` starts at file offset 0 and
  therefore contains the header and every load command — including the `LC_SYMTAB` and
  `LC_CODE_SIGNATURE` offsets a strip is *supposed* to move. It compares sections within segments
  now, which is the granularity the ELF branch always used.

#### Two claims in the paragraph above this one were wrong

This is not the second recipe to shell out to a toolchain binary and it is not the first to modify a
borrowed executable. `mariadb.strip_debug` has run `strip --strip-debug` over every machine file in a
borrowed bintar since before this repository had a rule — it is the reason `upstream.stripped`
exists. What is new here is not the operation but the *checking*, and MariaDB is the argument for it:
that function passes `capture_output=True` without reading the exit code, keeps whatever came out of
the tool, and declines to claim a saving only if the tree got bigger. Reconciling it with what P4b
built belongs where P1 already put it — with P6, which is where the check that reads these fields is
written.

`upstream.changed` is the fourth kind of difference and the one `borrow.declare` was written without,
because for three tasks there was no such thing: a recipe added files, removed files, or left them
alone. A file that ships at upstream's path and is not upstream's bytes is the difference a reader
comparing two archives is least able to account for, because it looks like a corrupted download. It
maps each path to the command that made it — `strip --strip-all` — rather than to prose: the reader
holding both archives wants to know what was done, and the argument lives here.

#### What it comes to, on 3.13.15

| | after P4a | after P4b | |
| --- | --- | --- | --- |
| Windows | 48.1 MiB | 48.1 MiB | nothing to do |
| macOS | 52.2 MiB | **49.5 MiB** | 2.7 MiB, 3 binaries |
| Linux | 81.4 MiB | **66.7 MiB** | 14.7 MiB, 4 binaries |

Four binaries, because `lib-dynload` holds three entries and one of them is `.empty`: everything
except `_dbm` and `_tkinter` is compiled into the interpreter, `prune` deletes `_tkinter`, and `_dbm`
is 2.38 MB on Linux with 834 KB of symbol table in it because gdbm is linked in statically.

The gap between Linux and Windows was 33.3 MiB after P4a and is **18.6 MiB** now — and
`lib/libpython3.13.so.1.0` is 19.1 MiB of that, which is to say all of it and more. Without the
second copy of the interpreter P4a decided to keep, the Linux artifact would be *smaller* than the
Windows one. macOS is within 1.4 MiB of Windows while carrying a 15.3 MiB duplicate dylib of its own.
Nothing is left in this row that one cell has and another does not.

Run on all ten Unix cell-versions available — 3.10, 3.11, 3.12, 3.13, 3.14 on `x86_64-unknown-linux-gnu`
plus 3.13.15 on the other three Unix triples — every one clean through `prune`, `strip_symbols`,
`keeps` and `borrow.declare`, and packed end to end on Windows for 3.10.21, 3.13.15 and 3.14.7, whose
archives came out byte-for-byte the size they were before P4b, which is what "no-op" has to mean.
Two honest limits on that. The tool driving it here was `llvm-objcopy` (as `rust-objcopy`), because
it reads ELF and Mach-O from any host and a runner's own `strip` cannot be run from this machine —
`mapped` is precisely what makes the substitution acceptable, since whatever the tool, the artifact
is not published unless the mapped image is identical. And extracting a Unix tarball on Windows turns
symlinks into copies, so the local run stripped `bin/python` and `bin/python3` as well; on a runner
`relocate.machine_files` skips them and the four paths above are what `upstream.changed` will name.

### [x] P5 — Ruby: make the two recipes answer the same questions **(rule)**

`ruby_smoke.py` exists precisely so that a borrowed Ruby and a compiled Ruby make the same claim, and
it works. What sits outside it does not:

- `ruby_unix.py` prunes `share/man`, `share/doc`, `share/ri`; `ruby.py` prunes nothing. This is the
  packer/compiler pair the rule names, with neither side mentioning the other.
- `--enable-yjit`, `--enable-libedit` (chosen over GNU readline for the **licence**, not the API) and
  `--disable-shared` are decisions taken for four cells out of six. Nobody has asked what
  RubyInstaller does about any of the three.
- `ruby_unix.py` proves two things `ruby.py` does not: that YJIT turns on, and that
  `gem install bigdecimal` compiles a native extension in the moved tree. Those are claims about
  *Ruby*, so by `ruby_smoke.py`'s own argument they belong in it — or their absence on Windows
  belongs in the manifest.

Unpacking a `.7z` needs 7-Zip, which the machine this was audited on does not have, so the Windows
half is *unknown* rather than *wrong*. Measure it first; the answer decides whether this task is a
prune, a note, or nothing.

**The blocker had already been removed by somebody fixing something else.** `borrow.seven_zip` falls
back to `%SystemRoot%\System32\tar.exe` — bsdtar, which ships with Windows and reads 7-Zip — a
fallback added because 7-Zip itself failed on a `windows-2022` runner. So the audit ran here after
all, on 3.2.11, 3.4.10 and 4.0.6. It is **a prune and a note**, and the prune is the largest single
thing this repository has thrown out.

#### The prune: half the archive, and four fifths of one of them

| | tree | after | zip | after |
| --- | --- | --- | --- | --- |
| 3.2.11 | 86.3 MB | 39.2 MB | 28.8 MB | **14.7 MB** |
| 3.4.10 | 108.0 MB | 47.7 MB | 34.0 MB | **17.2 MB** |
| 4.0.6 | 276.1 MB | 51.1 MB | 53.5 MB | **18.8 MB** |

`share/doc` and `share/ri` — RDoc's HTML rendering of Ruby's own manual, and the `ri` database — are
60.3 MB of the 3.4.10 tree and **224.9 MB of the 4.0.6 tree**, which is 81% of an artifact of a
programming language. It grew four and a half times in one line while the language grew by 8%: 1,334
HTML files averaging 160 KB where 3.4 had 1,502 averaging 32 KB.

**The direction was decided by the Unix half rather than by the size**, which matters because the
size alone would have been a reason to do something hasty. `ruby_unix.py` does not merely delete
those directories, it passes `--disable-install-doc` so they are never generated — a positive choice
with an argument written beside it, that this repository has no business shipping four copies of
Ruby's manual on four targets for every version of every line. Nothing on the Windows side argues
back; the `.7z` carries them because RubyInstaller is a general-purpose distribution of Ruby.

`share/ri` was checked rather than assumed, and it is **not** the CPython terminfo case: it is
genuinely reachable, `RDoc::RI::Paths.path` names it inside the tree and `RDoc::RI::Driver` answers
`String#upcase` out of it. What it is not is reachable through anything either recipe publishes —
neither `provides` has ever held `ri` or `rdoc`, and IRB's own `help` in 3.4 routes to its command
table rather than to RDoc. A working feature of a Ruby installation that no MixEngine command
reaches, on two cells of six, against four that were configured never to build it.

The list is `tools/ruby_parity.py`, read by both recipes, for the reason P2 built `php_parity.py`:
"say so beside the other recipe" is a comment, and this is precisely the drift a comment did not
prevent — `ruby_unix.PRUNE` carried an argument about "four targets" while the other two carried
225 MB nobody had weighed.

#### The note: two things the six cells cannot be made to share

Asked what RubyInstaller does about the three flags the Unix build passes — the second bullet above
— a 3.2.11, a 3.4.10 and a 4.0.6 all answer the same way:

- **YJIT is not there.** `RubyVM::YJIT` is undefined and `ruby --yjit` answers *"warning: Ruby was
  built without YJIT support"*. CRuby does not build YJIT for `x64-mingw-ucrt`. `ruby_unix.py`
  configures `--enable-yjit` and its smoke test **refuses to publish** a Ruby that answers false to
  `RubyVM::YJIT.enabled?`, on the grounds that the flag warns rather than fails without a Rust
  compiler — so this is a capability four cells are checked for and two cannot have.
- **No native gem compiles.** `gem install bigdecimal` exits 1 with *"MSYS2 could not be found.
  Please run `ridk install`"*. The toolchain is a separate ~1 GB MSYS2 that RubyInstaller publishes
  as its own installer, which is why the archive borrowed here is the one without it. `ruby_unix.py`
  proves the opposite claim from the moved tree and calls it a claim about Ruby.
- **libedit is a difference on one line only.** Windows is built `--without-ext=readline` and
  `require "readline"` resolves to the pure-Ruby Reline on every line. `ruby_unix.py`'s own comment
  says the same is true of its 3.3+ cells, so only 3.2 differs — there the four Unix cells carry
  real libedit, ncurses and tinfo beside them. Recorded rather than acted on: the `Readline` API is
  the same either way and what differs is how a line editor behaves inside `irb`.

The first two are written into a new top-level `lacks`, a mapping of capability to reason. It is the
only field in the schema that is an admission rather than a decision, and it exists because the
alternative is an artifact quietly smaller than its version number promises — a daemon can read it
and refuse to enable a feature the cell has not got, and a blueprint asking for a native gem can
fail where it is written. The four cells that lack nothing ask and write no field, which is not the
same as never having asked.

Packed end to end on Windows for all three lines, every manifest validating against the schema with
`upstream.removed` naming both directories and `lacks` naming both capabilities. What is left is
P5a, which is a question this audit raised rather than one it was sent to answer — and which, on
being asked properly, turned out to be two answers already in the trees and one task nobody had
written down.

### [x] P5a — Ruby: the shared library, and the linker's half of the archive **(rule)**

Two questions P5 raised and did not answer, both written down as *needs the compiled half changed,
and this machine cannot build one*. Neither did. The first was a misreading, the second follows from
correcting it, and what actually needs a rebuild is a third thing the measurement turned up.

#### The asymmetry was read off a config variable and is not in the trees

`RbConfig::CONFIG['ENABLE_SHARED']` is `yes` on the two borrowed cells and `no` on the four compiled
ones, and P5a was written from that: *two cells can be embedded in a program and four cannot*. What
this repository is supposed to do at that point is go and look, and looking says `--disable-shared`
does not mean no libruby — it means libruby is a **static archive**. `lib/libruby-static.a` is
41.4 MB on Linux and `lib/libruby.3.4-static.a` is 28.3 MB on macOS, on 3.4.10; either is the
largest file in its tree, larger than the `bin/ruby` that already contains it linked.

The half that decides it is what names them. `rbconfig.rb` opens with

```ruby
TOPDIR = File.dirname(__FILE__).chomp!("/lib/ruby/3.4.0/x86_64-linux")
```

— that is `--enable-load-relative`, the flag this whole row turns on, so `libdir` follows the tree
wherever it is put — and then publishes `CONFIG["LIBRUBYARG"]` as `-Wl,-rpath,$(libdir)
-lruby-static $(MAINLIBS)` on the compiled cells and `-l$(RUBY_SO_NAME)` on the borrowed ones, which
is what `lib/libx64-ucrt-ruby340.dll.a` resolves. `lib/pkgconfig/ruby-3.4.pc` restates it with
`prefix=${pcfiledir}/../..`. So every one of the six hands an embedder a link line naming a file
inside the artifact, and deleting either file leaves the artifact describing something it does not
carry — the failure P4a kept CPython's unloaded `libpython` to avoid, here applying to six cells
rather than four. `--disable-shared` stays, and its comment is right for a better reason than it
gives: a static libruby is one fewer file for `bin/ruby` to find after the move, and the embedder is
still served.

The one thing that *is* uneven is smaller than the question. Windows' `LIBRUBYARG_STATIC` names a
`lib…-static.a` RubyInstaller does not ship, so the other of the two spellings is the empty one
there. Nothing follows it unless an embedder asks for it by name, and there would be nothing to ship
that answered.

#### So the second question answers itself, and it is two entries in `keeps`

The import library is not dead weight, and `include/` — 2.0 MB, the same on all six — is what a gem
with a C extension is compiled against. Both stay on the Windows cells although `lacks` has just
said no compiler is present, for the reason P4 kept `libs/python3XX.lib` on a Windows shipping no
compiler either: *cannot today* and *cannot ever* are different artifacts, `ridk install` is the
supported way a user closes the gap, and headers deleted here cannot be.

`ruby_parity.keeps(tree, os)` reads both off the finished tree — a glob, because the middle of the
name is the build's `RUBY_SO_NAME` and it is spelled three ways — and **refuses to describe a tree
that has neither**, since a build that stopped installing the static library is the decision
changing under the recipe rather than a file to shrug at. `ruby_unix.py` is the first *built*
artifact in this repository to go through `borrow.declare`, which took one fix: `declare` used
`setdefault` and would have hung an empty `upstream` on a manifest with nothing borrowed, and the
schema requires `url` and `sha256` in any `upstream` that exists.

#### What actually needs a rebuild is the thing neither question asked about

Weighing the static archive to argue about it is how this turned up: **63% of `libruby-static.a` is
debug information.** By ELF section, on 3.4.10 for Linux — 9.6 MB of `.rela.debug_info`, 4.3 MB of
`.debug_str`, 4.2 MB of `.debug_info`, against 3.9 MB of `.text`. And the binaries beside it are in
the same state, which is the part P4b would recognise:

| | tree | `bin/ruby` | its DWARF | static libruby | its DWARF |
|---|---|---|---|---|---|
| linux-x86_64 | 106 MB | 20.5 MB | 11.7 MB | 41.4 MB | 26.1 MB |
| macos-aarch64 | 81 MB | 6.9 MB | 0.9 MB | 28.3 MB | 19.0 MB |

macOS is lighter only because `assemble` already deletes the `.dSYM` bundles, which is where the
compiler put the executable's debug information and is not where it put the archive's. Windows has
none of this: RubyInstaller links with `-s`, visible in the `DLDFLAGS` its own `.pc` publishes. That
is P4b's finding on another row — Windows already stripped, the other four levelled down to it — and
it is **P5b**, not this task, because it changes what a compiled cell contains and nothing here can
compile one.

#### Verified

All three Windows lines packed end to end (14,717,349 / 17,177,020 / 18,833,034 bytes — the manifest
grew by its `keeps` block and nothing else did), each manifest validating with
`keeps: ['include', 'lib/libx64-ucrt-ruby3X0.dll.a']`. The Unix half cannot be built here, so what
was checked is the part P5a changed, against the published `ruby-3.4.10` artifacts for
`linux-x86_64` and `macos-aarch64`: `keeps` finds `include` and the right static library in each,
`declare` writes them, no `upstream` block appears on a built manifest, both validate, and a tree
with no static libruby is refused.

### [ ] P5b — Ruby: the four compiled cells ship their debug information **(rule)**

Measured under P5a and left there. `bin/ruby` and `lib/libruby*-static.a` carry 37.8 MB of DWARF on
a 106 MB Linux tree and 19.9 MB on an 81 MB macOS one; RubyInstaller ships none, having linked with
`-s`. This is P4b's task on the Ruby row and the direction is the one P4b settled — level down to
the cell that already has nothing — but two things differ and both are why it is separate.

*The strip has to be verified on a machine that can build.* P4b could prove its own operation on
Windows because `python.py` borrows on every platform; `ruby_unix.py` compiles and runs nowhere but
macOS and Linux. The proof P4b built — `mapped()` comparing every allocated section and program
header across the operation, `countersigned()` recomputing the arm64 code signature — is what should
be reached for, and lifting it out of `python.py` into something both recipes share is most of the
work.

*One of the two files is an `ar` archive, not an executable*, and it is the one P5a has just written
into `keeps` as needed. `strip --strip-debug` over a static library is a different claim from
stripping a binary nothing links: what has to survive is every symbol an embedder resolves, so the
check is the archive's symbol index and each member's `.symtab`, not a loader's view. Measured with
`llvm-objcopy --strip-debug`, it would take the Linux archive from 41.4 MB to 15.3 MB and the macOS
one from 28.3 MB to 9.3 MB.

Do it with P6 or before it, not after — for the reason this task inherited from P5a: what P6 checks
is that six cells agree, and a row where four of them carry 38 MB the other two do not is a row that
check would have to be pointed at with an exception.

### [ ] P6 — Make the rule something CI can fail on **(rule)**

P2–P5 are one-time corrections; this is what keeps them. `verify.py` already validates each artifact
against the schema. What it cannot do is compare the artifacts of *one version to each other*, which
is the whole of the rule's first half. This is not a hypothetical check: MariaDB's four asymmetries
were found by doing precisely that by hand, on a run whose six cells had all passed their smoke
tests, and P2 then packed five branches of PHP without GD for the same reason — a file renamed
between eras, invisible to every per-artifact check because what is missing cannot fail a load test.
Twice now, and both times by comparing rather than by reading.

Two checks, both cheap because every fact they need is already in `mixengine-artifact.json`:

*Across cells* — for one `(kind, version)`, the feature sets must match. For PHP that is
`extensions.static ∪ extensions.enabled` — the set a cell actually runs with, which is what P2
added `enabled` for; `shared` is only what it could be asked to do, and on Windows it says the same
word about `curl` and about a debugger. The exemption list is no longer a guess: `php_parity` names
the four extensions Windows has never had, the two its old builds gained late, and the two its 7.x
builds compile in and cannot drop. Everything outside those names is a defect this check fails on.
For the other kinds the comparison is `provides`.

*Within one artifact* — no path matching what the second half of the rule forbids and the manifest
does not declare: `.pdb`, `.dSYM`, `*.lib`/`*.a`, `include/`, `share/man`, `share/doc`, `test/`. PHP
on Windows is where to point it first, because P2 already deletes every one of those there: a check
that finds nothing in the row that was just cleaned is a check that has been tested. Node.js is the
second, and it is the row that shows the check has to read `upstream.removed` rather than only the
tree — `include/`, `share/man` and `share/doc` are on that list because P3 named what it dropped,
and a check reading the tree alone would say nothing about a cell that never had them. A
recipe that legitimately keeps one of them declares it, and the declaration is what the check reads
— so "no more than is needed" becomes a list somebody wrote down rather than a habit somebody
remembers. That field now exists: P4 added a top-level `keeps`, a mapping of path to reason rather
than a list, because a check reading a bare path can only report *declared* and the argument is the
part worth keeping. CPython was its first writer — `include/` on six cells, `libs/` on two and the
shared interpreter on four — and P5a made Ruby the second, on all six at once and from both recipes,
which is the case that matters most here: `libx64-ucrt-ruby340.dll.a` and `libruby-static.a` are
what this check would otherwise read as two rows disagreeing, and they are one decision spelled by
two toolchains. Ruby is also the first *built* artifact to write the field, so the check cannot
assume a `keeps` implies an `upstream` beside it.
The Unix entries are also the reminder that this check will never be the whole of the field: no
pattern above would flag a `.so` in `lib/`, and P4a declared 30 MiB of one anyway, because the
reason it is kept is not reconstructable from the file. What the check enforces is that everything
matching the patterns is declared; what the field is *for* is larger than that, and stays larger.

The fold-in P1 deferred to here got larger while waiting, and clearer. `upstream.stripped` is a
sentence MariaDB writes about an unchecked `strip --strip-debug`; `upstream.changed` is a mapping
P4b writes about a strip that refuses to publish unless the loader's and the linker's whole view of
the file came through identical. Two spellings of one fact is the shape of thing this rule exists to
remove, and the fold-in is not a rename: it is `mariadb.strip_debug` reading its own exit code and
answering to `python.mapped`, which is worth more to that row than to this one — MariaDB strips 371
MB of bintar down to 27 and nothing checks what came out.

Run it in `publish-index.yml`, where every artifact of a version is visible at once. An empty cell is
not a failure — a target upstream never built is already an `exit 75`, and this must keep that
distinction or it will block the release of the five cells that do exist.

---

## Services still to pack

Caddy and MariaDB are in. The remaining three are the same shape, and each owes the same two things:
an evaluation — **borrow before you build**, asking the catalogue rather than assuming it, which is
how MariaDB's row turned out to be wrong in three cells — and a smoke test that exercises *run,
configure, health-check, stop* rather than `--version`.

### [ ] P7 — PostgreSQL

The evaluation question is Windows and macOS. EDB publishes a Windows binary archive and macOS
builds; Linux has no relocatable upstream tarball worth the name, and the `.deb`-rearrangement route
MariaDB's aarch64 cell uses is the obvious candidate. The smoke test's own terms are `initdb`, a
server started against a rendered `postgresql.conf`, `pg_isready`, a row written and read back
through a real table, and `pg_ctl stop -m fast` — the `mariadb-install-db` / `mariadb-admin ping` /
`mariadb-admin shutdown` trio, one database over.

### [ ] P8 — Redis and Memcached

**The evaluation here is likely to answer "build", and on the platform that usually borrows.**
Neither project publishes an official Windows binary; what circulates is a Microsoft fork abandoned
at 3.0 and community rebuilds. So this task decides between compiling both natively on a Windows
runner and declaring the cell empty — and an empty cell is a real answer, not a failure, as long as
the index says so instead of a user finding out.

The smoke test is the cheapest in the table: start, `PING`, `SET`/`GET`, `SHUTDOWN NOSAVE`.

### [ ] P9 — nginx

nginx publishes a Windows zip itself and nothing relocatable for macOS or Linux, so the shape is
PHP's rather than Caddy's. The proof has one thing Caddy's does not: nginx has no admin endpoint, so
reload is `-s reload` against a running master and health is a request actually served.

---

## The index

### [ ] P10 — End-of-life dates for every kind, not only MariaDB

`data/eol.json` carries what the index promises about a version's support window. MariaDB's entry is
transcribed from a publisher API and reprinted on every run; the runtime entries are transcribed from
schedule pages by hand and nothing checks them. PHP and Node.js both publish machine-readable
schedules (`endoflife.date` mirrors the rest); reading them the way `mariadb.py` reads MariaDB's
turns four hand-maintained entries into four transcriptions with a source.

### [ ] P11 — Prove the archive is permanent

The index promises that a blueprint pinning PHP 8.1.29 keeps working forever, which makes every
release asset load-bearing and an accidental deletion unrecoverable. Nothing states that today: no
protection on the releases, no periodic check that every URL in the published index still answers,
no line in the README saying which assets may never be deleted. A scheduled workflow that `HEAD`s
every artifact the current index names is the smallest thing that would notice.

---

## Working on this file

- Tick the task here; one file, not a phase per section, because this repository is one pipeline.
- New work goes **where it belongs in the order**, with the next free suffix on the task it follows
  (`P2a`, `P2b`) rather than at the end.
- **One note, one place.** Why a recipe does what it does belongs in its docstring, beside the code
  it is about; what a packaging decision settled for the whole repository belongs in
  [`../README.md`](../README.md); what building something the hard way taught belongs in
  [`building-from-source.md`](building-from-source.md). What this file carries is only what none of
  those can: what has not been done yet, and what has to be decided before it can be.
