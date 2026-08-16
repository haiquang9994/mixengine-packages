# Building from source: what the PHP 7 and Ruby pipelines cost to learn

`relocate.py`, `php_legacy_unix.py` and `ruby_unix.py` explain *why they are shaped the way they
are*. This file is for the other half — the failures that shaped them. It is written for whoever
opens the next "we build" cell in [MixEngine's runtime table][table] (nginx, PostgreSQL, Redis),
because most of what follows is not about PHP or Ruby at all.

The Ruby half of this file is [further down](#ruby-took-four-rounds-and-none-of-it-was-ruby), and
its headline is worth putting at the top: **four rounds to green, and not one of them was the
language** — plus two more spent making the four artifacts of one version the same artifact. Every
failure was in the shared packing code or in this repository's own idea of what a check should ask,
which is what a second "we build" cell is for.

The PHP 7.0–8.0 pipeline took ten rounds of CI to go green on four targets. Almost none of that was
spent on code that failed to compile. It was spent on **builds that exited zero and produced
something wrong**, which is the failure this repository is least able to afford: an artifact is
published once and then trusted forever by machines that cannot ask questions.

[table]: https://github.com/haiquang9994/MixEngine/blob/master/.claude/operations/runtime-packaging.md

## The rule the whole thing reduces to

> A build machine is the one machine where a broken artifact works.

Every dependency is installed, every path exists, every library is the right version — that is what
makes it a build machine. So no check performed *in place* proves anything about the archive. The
three that do:

1. **Move the tree before testing it.** Not to a sibling directory — somewhere the build has never
   named. `smoke.relocated` in the manifest records that this happened.
2. **Ask the loader, not the program.** `ldd` / `otool -L` from the moved tree, and fail on any
   reference resolving outside it. A program that starts proves only that *today's* machine has
   something to satisfy it; `php -v` printed a version happily while pointing at `/opt/homebrew`.
3. **Exercise every feature the manifest claims**, not one of them. The smoke test loaded the first
   extension and stopped, so an extension that compiled and could not load was invisible for three
   rounds — and the manifest went on advertising it.

And where a check can fail in two places, split it. "The extensions do not load" was one question
too many: loading them **where they were installed**, before packing, separates a build fault from a
packing fault in seconds and would have saved most of the rounds below.

## Silent failures

The expensive ones. Each of these exited zero.

**PHP ignores `extension=` in complete silence without `HAVE_LIBDL`.** Four rounds. Both
`php_load_php_extension_cb` and `php_load_zend_extension_cb` in `main/php_ini.c` are compiled with
*empty bodies* when it is missing, so every `extension=` line is read and discarded without a word.
PHP derives the macro from a `dlopen` test that looks in libc first, and glibc before 2.34 keeps
`dlopen` in `libdl` — so on an old distribution, chosen on purpose, the test fails and the loaders
vanish. `-ldl` in `LDFLAGS` up front is the whole fix. Nothing about the symptom points at linking.

**`display_startup_errors` is Off by default, and loading an extension is a startup error.** Two
rounds before that. PHP refuses an extension and says nothing, on a command that exits zero. Any
generated ini used for testing must turn it on. Where it still says nothing, `dl()` answers a
different question — it distinguishes "dynamic modules are not supported" (the `HAVE_LIBDL` case
above) from dlopen's own complaint, which the ini path never surfaces.

**`-n` and `-c` together.** One says "use no ini", the other "use this ini". Passing both looks like
an ini that was never read, which is indistinguishable from an extension that refused.

**An extension may not answer to its file name.** `opcache` reports as `Zend OPcache`, so a
perfectly loaded opcache read as absent. It is also a `zend_extension`, and loading one with
`extension=` reports as not-loaded rather than as an error.

**PECL declares release candidates stable.** `igbinary 3.2.17RC1` arrived with `<stability>stable`.
The version string is the better witness — require `[0-9.]+` and let the declaration lose.

**A PECL search that gives up reports "nothing supports this PHP".** Reaching a `mongodb` that still
supports 7.0 means walking past every 2.x and most of 1.x, about eighty releases; a cap of forty did
not fail, it shipped an artifact missing an extension the index promised. Read the range from
`rest/r/<pkg>/deps.<version>.txt` — a few hundred bytes, not a tarball — which makes 250 deep
cheaper than 40 was against tarballs. And a declared range is a *claim*: where the newest release
that claims a branch will not compile against it, try the next one down.

**A library that links but cannot load.** Build systems older than `@rpath` sometimes set a dylib's
install name to its bare file name, with no directory — ICU's Darwin makefile is one. Linking works,
because `-L` tells the *linker* where the file is; loading never can, because the bare name is
copied into everything that links it and dyld has nowhere to look. What that does to `configure` is
the interesting part: **link probes keep passing while every run probe fails to launch**, so
autoconf records "this platform cannot do that" feature after feature and finally stops on whichever
one it cannot live without. PHP announced that macOS has no `struct flock`. The real fault was a
library installed twenty minutes earlier. If a run of `configure` suddenly answers "no" to things
the platform obviously can do, suspect the last library you installed, not the platform.

**Measuring the wrong number.** `otool -l` spells two different things `version`: inside
`LC_VERSION_MIN_MACOSX` it is the minimum, and inside `LC_BUILD_VERSION` it appears again in the
list of *tools* that produced the file, where it is the linker's. Grepping the dump for `version` and
taking the highest reported `requires.macos: 1115.7.3` — the arm64 runner's linker. Read fields
relative to the load command that contains them.

## A probe that fails to compile does not fail the build

The most transferable thing here, and it applies to any autotools project older than about 2015.

`configure` decides what the platform can do by compiling small programs and seeing what happens. A
probe that does not compile is not an error — it is a **no**, and the no is written into
`php_config.h` and built against. So a compiler that has grown stricter since the probes were
written does not produce a compiler error. It produces a *wrong configuration*, which fails much
later, somewhere unrelated, in code that looks like the project's own bug.

gcc 14 and clang 16 turned six long-standing warnings into errors. Both Linux legs died on it, in
two places that look nothing alike:

- **7.0.** The broken-sprintf probe is `main() {char buf[20];exit(sprintf(buf,"…")!=11);}` — no
  includes, no return type. Rejected, so configure concluded sprintf *is* broken and put
  `int zend_sprintf(…)` into `php_config.h`. That header has no include guard; `ext/intl` reaches it
  once at C++ scope and once through `extern "C"`, and the build died on conflicting linkage for a
  function nobody had asked for.
- **7.3.** The `readdir_r` probe passes a `DIR *` to `close()`. Rejected, so configure fell through
  to "old-style", and `main/reentrancy.c` called the two-argument `readdir_r` that no libc has
  shipped since 2005.

Nothing in either message points at `configure`. The fix is one list:

```
-Wno-error=implicit-function-declaration -Wno-error=implicit-int -Wno-error=int-conversion
-Wno-error=incompatible-pointer-types
```

plus `-Wno-error=return-mismatch -Wno-error=declaration-missing-parameter-type` on gcc, whose
spellings clang does not have. This is the same argument as choosing the era's distribution, applied
to the compiler's opinions rather than to its libraries — and it is worth reaching for **before**
reading a confusing compile error in old code, not after.

## One version green proves nothing about its siblings

7.4 went green on all four targets. 7.0 and 7.3 then failed immediately on both macOS legs, inside
*this repository's own code*: the generated `icu-config` shim was written with `encoding="ascii"`
and its comment contains an em dash. The shim exists only for branches before 7.4, so the target
that had been proven four times over had never executed that line.

Two things follow. Write generated files as UTF-8 — a build must not be able to fail over the
punctuation in its own comments. And when a matrix has a per-item branch in the *tooling*, a green
item is evidence about that item only: pick the next versions to try by which ones take a different
path through your code, not by which ones are adjacent.

## Every leg must resolve a version the same way

`7.0` failed on Windows too, and that leg compiles nothing. It borrows, and it had a rule: a branch
that `releases.json` does not describe has no "newest", so name the exact version. Reasonable while
Windows was the only leg reaching back that far — and wrong the moment the Unix recipes learned to
resolve a branch to its final patch, because one dispatch of `7.0` then produced four artifacts and
one error.

Worth stating in general: if legs resolve versions independently, they must agree, or the release
job groups assets from one build into two releases. A frozen branch is the *easier* case — "newest
of a supported branch" is a moving claim, while the last patch of a branch that will never have
another is a fact, and the archive listing states it.

## Mach-O, and why it is not ELF with different flag names

ELF relocation is one `patchelf --set-rpath '$ORIGIN'`. Mach-O has four ways to go wrong:

- **Rewriting invalidates the signature.** On Apple Silicon an unsigned Mach-O is killed by the
  kernel, not diagnosed by the linker: `Killed: 9`, no message. `codesign -f -s -` after every file
  this touches. Miss it and the build passes on Intel and dies on arm64.
- **Matching a dependency by file name hijacks the system's.** Homebrew's `libintl` asks for
  `/usr/lib/libiconv.2.dylib` — Apple's. Point it at the Homebrew `libiconv` bundled under the same
  name and it aborts on startup with `Symbol not found: _iconv`, because GNU's exports `_libiconv`.
  Never redirect a reference into `/usr/lib` or `/System`, whatever it is called.
- **Adding an rpath is not removing one.** `@loader_path/../lib` was added while the build's absolute
  `LC_RPATH` entries stayed, and those still exist on the machine that built it — so the archive
  verifies here and loads a stranger's Homebrew there. Delete every search path except the anchor.
- **Ask the original what it needs, not the copy.** `@rpath` resolves relative to the file asking, so
  a copy sitting alone in a half-filled `lib/` cannot answer for itself: `libwebp` wanting
  `@rpath/libsharpyuv.0.dylib` looked missing while sitting next to it in the Cellar.
- **Only the linker can leave room for a longer path.** `install_name_tool` cannot lengthen anything
  in a Mach-O whose load commands were packed tight — "larger updated load commands do not fit". So
  every binary and every library that will later be rewritten has to be *linked* with
  `-Wl,-headerpad_max_install_names`, including dependencies built along the way. It costs nothing
  and cannot be added afterwards.

## Loud failures, catalogued

These fail the build honestly. They are here only so nobody spends an afternoon rediscovering that
old source needs an old toolchain — which is the argument for building inside AlmaLinux 8 rather
than patching around a current distribution.

| Symptom | Cause |
| --- | --- |
| `false` is not a null pointer constant; `f()` takes no parameters | gcc-toolset defaults to **C23**. Ask for `-std=gnu17` / `-std=gnu++17` |
| `ext/intl` cannot find `TRUE`/`FALSE` | ICU 68 removed the macros. `-DU_DEFINE_FALSE_AND_TRUE=1` |
| `unknown type name 'UnicodeString'` | ICU 61 stopped emitting `using namespace icu;`. `-DU_USING_ICU_NAMESPACE=1` |
| `operator==` overrides with the wrong return type | ICU 70 changed those virtuals from `UBool` to `bool`. **No macro fixes this** — the version guard has to be in the source, so anything released before ICU 70 needs an ICU older than 70 |
| `phpize` fails on ≤ 7.3 | autoconf 2.70 broke it. Build 2.69, or use a distribution that has it |
| `RSA_SSLV23_PADDING` undefined | OpenSSL 3 removed it. PHP 7 wants 1.1.1 |
| `xmlError` is const | libxml2 2.12. Pin 2.9.14 |
| `ext/intl` finds no ICU before 7.4 | those branches only know `icu-config`, which modern ICU dropped |
| `res_9_dn_expand` undefined on macOS | since the macOS 14 SDK, configure stops asking for `-lresolv` |
| "Cannot find libz" on a Mac that has zlib | there has been no `/usr/include` since Xcode 10. Pre-7.4 probes read `$DIR/include/zlib.h` and search `/usr/local` and `/usr`, so the SDK has to be named: `--with-zlib=$(xcrun --show-sdk-path)/usr` |
| ICU 60.3 will not build on macOS | its 2017 `config.sub` predates `arm64-apple-darwin`, and its Darwin makefile emits `-install_namelibicudata.60.dylib` as one argument. Borrow ICU instead |

Before reading any of these too literally, check whether the compiler actually rejected *this*
source or merely rejected a `configure` probe an hour earlier — see above. Both of the Linux entries
that looked like PHP failing to compile were that.

The ICU rows are worth one more sentence, because they are the sharpest evidence for the
old-distribution argument. On Linux, inside AlmaLinux 8, none of them happen: ICU 60 is simply what
is there. On macOS all three had to be discovered, and the third cannot be worked around from
outside the source at all — leaving no choice but to build a pinned ICU 67 alongside. A dependency
that only compiles against a *range* of versions is the strongest reason to control the toolchain
rather than accept whatever a package manager installed this month.

**A prefix you hand a build system becomes part of every compile.** This one is worth stating on its
own, because it is invisible and it bites twice. `--with-zlib=<dir>` does not only find zlib:
ext/zlib passes that directory to `PHP_ADD_INCLUDE` and `PHP_ADD_LIBPATH`, and in a static build
those land in the flags *every* compile and link gets. Point it at `<sdk>/usr` — the obvious answer,
since that is where the system's zlib now lives — and the whole SDK moves to the front of both
search paths, ahead of the Homebrew prefixes. The SDK has a `libreadline` that is really libedit and
a `libiconv` that is Apple's rather than GNU's, so the link fails on `_rl_done` and `_libiconv_open`
in extensions that have nothing to do with zlib, having found real libraries that are the wrong
ones. Keeping the SDK out of *our* flags did not help; this path never went through them.

The fix generalises: **hand a build system a prefix containing only what you are claiming it
contains.** A directory of symlinks costs nothing and cannot shadow anything.

Two configure-flag habits worth keeping, both learned by shipping past a warning:

- **An unrecognised `--with-…` is a warning, not an error.** `--with-onig` does not exist in 7.4;
  configure said "unrecognized options" and carried on. Read that line.
- **A flag that enables and a flag that only hints are different things.** Passing `--with-icu-dir`
  bare does not mean "look in the usual places" — configure runs `yes/bin/icu-config`. A hint with
  nowhere to point belongs omitted, not passed bare. Likewise `--with-iconv=/usr` sends PHP looking
  for a libiconv that glibc does not have, because iconv is in the C library there.

## Ruby took four rounds, and none of it was Ruby

The PHP range above cost ten rounds and most of them were the language: an ICU that would not
compile, a `configure` probe that answered wrongly, an extension that built and would not load. Ruby
compiled on all four targets the first time it was asked, with `--enable-load-relative` doing
everything RubyInstaller's Windows archive proves it does. What failed, four times, was **the
packing code and the checks** — which is the argument for opening a second "we build" cell at all.

**A file can carry the right magic number and never be loaded by anything.** `machine_files` found
every ELF and Mach-O in the tree by its first four bytes, which is correct for PHP, where the only
machine code is binaries and extensions. A Ruby tree also holds `debug.o`, left in a bundled gem's
build directory, and a `.dSYM` bundle beside every extension macOS compiles. Neither is ever loaded,
and each *refuses the tool that would have rewritten it*: `ldd` answers "not a dynamic executable",
which was then read as the name of a missing library, and `install_name_tool` answers "string table
not at the end of the file". Read `e_type` and `filetype` out of the header — ET_EXEC/ET_DYN,
MH_EXECUTE/MH_DYLIB/MH_BUNDLE — rather than the magic. And an install name belongs to a *dylib*:
Ruby's extensions are MH_BUNDLE and have no `LC_ID_DYLIB` to set.

**A library does not have to carry a search path, and whoever asks for it usually does.** The build
links Ruby with `-Wl,-rpath,<deps>/lib`, so `ruby` resolves `libssl.so.3` perfectly. `libssl.so.3`
then names `libcrypto.so.3` with no search path of its own, so asking *it* answers "not on this
machine" about a library sitting in the same directory. This is the same asymmetry `loader_search`
already corrected *inside* a finished tree, one step earlier and with a different-looking symptom.
`bundle()` takes the build's own prefix now, for the same reason `verify()` takes the tree's.

**Two checks, two different PATHs, and mixing them up looks like a broken toolchain.** Every check
that asks the *artifact* a question strips the runner's PATH, so the runner's own Ruby cannot answer
for it. The check that compiles a native gem must do the opposite: the compiler is supposed to come
from the machine, because on a user's machine it will. Inheriting the strict PATH produced mkmf's
"you have to install development tools first" on an image whose compiler was simply somewhere else
(`/opt/rh/gcc-toolset-14`). *Strip the environment for questions about the archive; keep it for
questions about the machine the archive will land on.*

**The proof directory has a space in it on purpose, and one step cannot have one.** macOS `mkmf`
points an extension at the interpreter with `-bundle_loader <bindir>/ruby` and does not quote the
path, so `ld` reads `…/moved here/tree/bin/ruby` as a library name and cannot find it. That is
upstream's escaping, it says nothing about relocation — and it is worth knowing on its own, because
**a macOS user whose home directory contains a space cannot build native gems against a Ruby
installed under it.** The recipe compiles its gem from a second moved copy without the space, and
this paragraph is the rest of the answer.

**`rbconfig.rb` is not a log.** It is the configuration every native gem is compiled with, years
later, on somebody else's machine: the compiler, the flags, the header directory. Which makes two
things build-machine leaks rather than noise — `-I/tmp/mixengine-ruby-a1b2c3/deps/include`, a
directory that will never exist again, and `CC=/opt/rh/gcc-toolset-14/root/usr/bin/gcc`, a compiler
that will not either. Hand the build a **bare** `CC`, so `PATH` answers at gem-build time, and take
this build's temporary directories back out of what it wrote down. Then compile a gem from the moved
tree, which is the difference between believing that and having asked.

**Ship the runtime, not the build of it.** `make install` compiles each bundled gem's extension in
place and leaves the Makefile, the `mkmf.log` and the objects behind — every one naming a directory
that stopped existing when the build finished. The `.dSYM` bundles are a third of a macOS archive on
their own.

**Two artifacts of one version should differ only in their target**, and measured, they did not.
Three times, none of them a failure and all of them invisible from outside the archive:

- The Intel runner had GMP installed as another formula's dependency and the arm64 runner did not,
  so one Ruby used it for Integer arithmetic and its sibling did not.
- Installing GMP explicitly fixed half of it. **Homebrew's prefix is a compiler search path on
  Intel and not on Apple Silicon** — `/usr/local/include` is looked in anyway, `/opt/homebrew/include`
  is not — so `brew install gmp` produced `checking for gmp.h... yes` on one runner and `no` on the
  other, with no error on either. Ask `brew --prefix <formula>` and pass it; it is the only spelling
  that is true on both.
- `tar --zstd` is refused on the manylinux image, so one version was published as `.tar.zst` on
  macOS and `.tar.gz` on Linux. Installing the `zstd` compressor did not change it — the refusal is
  tar's own — and nothing had printed either fact, because the fallback *worked*.

**A fallback that succeeds is the hardest kind of difference to notice**, which is an argument for
printing what was actually used rather than what was asked for — `pack` says which format it packed
and quotes the refusal now — and for reading the four logs of one version side by side before
believing they built the same thing.

**One thing generalised from PHP and did not need to be discovered again**: build inside the era —
here only for the floor. Nothing in Ruby 3.2+ wants an old toolchain, so AlmaLinux 8 buys glibc 2.28
instead of the runner's 2.39 and costs nothing, because everything Ruby *is* version-sensitive about
(OpenSSL, libyaml, libffi) is compiled by the recipe on every target alike.

### The trust store, which is a claim about the build machine

Worth its own heading because it is invisible, it is not Ruby-specific, and every remaining "we
build" cell that speaks TLS will meet it.

`OPENSSLDIR` is fixed when OpenSSL is compiled, and everything downstream is a statement about the
machine that compiled it: `X509_get_default_cert_file` answers `/etc/pki/tls/cert.pem` on the Red
Hat family and `/etc/ssl/cert.pem` on the Debian one. Ruby republishes whichever it got as
`OpenSSL::X509::DEFAULT_CERT_FILE`. So an artifact built on AlmaLinux verifies certificates
perfectly on the runner and fails **every** handshake on a Debian user's machine, with an error that
names neither the file nor the reason — and `gem install` is the first thing that user will run.

Three answers were considered and only one is complete. Setting `SSL_CERT_FILE` from the runtime
covers the programs that read the environment and leaves the constant lying. Shipping a bundle and
documenting it is not an answer at all. What `ruby_unix.py` does is compile OpenSSL with its four
default-path functions taught to resolve against **the loaded `libcrypto`'s own location** —
`dladdr` on a symbol in the library, two directories up, `ssl/cert.pem` — falling back to the
compiled-in path when that file is not there. It is `--enable-load-relative` applied one library
down, it is what RubyInstaller gets from MSYS2 on Windows, and it makes the constant true.

Then prove it twice, because neither half implies the other: the path has to be **inside the moved
tree** (a bundle pointing at the packaging machine works perfectly on the packaging machine), and a
real chain has to **verify over the network** from there (a path that exists proves nothing about
what is in the file). The Python row learned the second half separately, by asserting a certificate
*count* that reads 0 on a perfectly working Linux.

## Before opening the next one

- Build inside the era the source was written for. It costs a `container:` line; the alternative
  costs a patch set per subsystem.
- Bundle everything outside the C runtime, and **measure** the floor that leaves rather than assuming
  the build machine's.
- Build each architecture on a runner of that architecture. Nothing here cross-compiles and nothing
  runs under emulation, so a target that will not build natively is simply not offered — which the
  daemon can state, unlike a binary that fails in the loader.
- Put the proof in the manifest. `smoke.relocated`, `smoke.ran`, `smoke.loaded_extensions` and
  `requires` are what a reader has instead of the log, which expires.
- Ask what a *runtime* has to be able to do after it moves, not only what it has to be able to
  start. For Ruby that was three things nothing in the PHP range had asked for: verify a
  certificate, install into its own gem home, and compile a native extension against its own
  headers. Each of them found something.
- Where the same runtime is produced by two recipes, share the *claim* and not the mechanics.
  `ruby_smoke.py` is what the borrowed Windows archive and the compiled Unix one both have to
  satisfy, because a daemon installing one of them cannot tell which recipe produced it.
- **Where a row borrows on one cell and compiles on the rest, read the specification off the
  borrowed binary rather than deciding it.** nginx is the clearest case: `nginx -V` prints the
  configure line upstream built its Windows zip with, so the compiled cells are configured against
  that and a constant in the recipe holds both sides to it. The decision a build otherwise makes by
  omission — which modules a version has — is one the publisher already made for the cell nobody
  compiles, and matching it is the only way the row means one thing. `php -i`, `ruby -e
  'RbConfig::CONFIG'` and `caddy build-info` are the same question asked of three other publishers.
