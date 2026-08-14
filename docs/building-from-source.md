# Building from source: what the PHP 7 pipeline cost to learn

`relocate.py` and `php_legacy_unix.py` explain *why they are shaped the way they are*. This file is
for the other half — the failures that shaped them. It is written for whoever opens the next "we
build" cell in [MixEngine's runtime table][table] (nginx, Ruby, PostgreSQL, Redis), because most of
what follows is not about PHP at all.

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

The macOS SDK deserves a sentence of its own, because it cuts both ways. A dependency may be
*pointed* at it — that is the only way `--with-zlib` can be answered before 7.4 — but `<sdk>/usr`
must never join the include and library flags every compile gets. Put it there and Apple's headers
sit ahead of the Homebrew prefixes, which is how a build compiles against the system `iconv.h` while
linking GNU libiconv: no error until a dyld symbol failure at startup.

Two configure-flag habits worth keeping, both learned by shipping past a warning:

- **An unrecognised `--with-…` is a warning, not an error.** `--with-onig` does not exist in 7.4;
  configure said "unrecognized options" and carried on. Read that line.
- **A flag that enables and a flag that only hints are different things.** Passing `--with-icu-dir`
  bare does not mean "look in the usual places" — configure runs `yes/bin/icu-config`. A hint with
  nowhere to point belongs omitted, not passed bare. Likewise `--with-iconv=/usr` sends PHP looking
  for a libiconv that glibc does not have, because iconv is in the C library there.

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
