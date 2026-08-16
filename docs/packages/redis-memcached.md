# Redis and Memcached, and the two ways P8 read a source tree right and concluded wrong

*Part of [mixengine-packages](../../README.md), which holds the table of what is packaged.*

The table said "we build with MSVC, or ship Valkey" for Redis on Windows and "we build" for
Memcached everywhere, and P8 was written to decide between compiling both natively on a Windows
runner and declaring the cell empty. It found no Win32 build system for either and closed all four
Windows cells. The reading of the source is right in both cases and was re-checked on a runner
rather than trusted. **The conclusion is wrong in both cases, and for two different reasons.**

*Redis.* No tag of `redis/redis` between 2.6 and 8.10 has ever carried a `CMakeLists.txt`, a
`win32/` or an `msvs/`; it is a `src/Makefile` around POSIX `fork()`, `epoll` and `kqueue`, and the
Windows support that once existed lived in `microsoftarchive/redis`, a separate fork with its own
`src/Win32_Interop` that stopped at 3.0.504 in 2016. All true — and "has no Windows build system"
and "cannot run on Windows" are different claims. The second is about the interfaces a program
calls, not about the files in its tarball. Compiled *against* a POSIX runtime rather than ported to
Win32, the unmodified source builds and runs; `redis-windows/redis-windows` has published exactly
that for 6.2, 7.2, 7.4, 8.2, 8.4, 8.6, 8.8 and 8.10, every line offered here.

*memcached.* What P8 said was that memcached is autotools with a privilege-dropping source file for
each Unix — `linux_priv.c`, `darwin_priv.c`, `freebsd_priv.c`, `openbsd_priv.c`, `solaris_priv.c` —
and none for Windows. Every word describes the source tree correctly. The conclusion does not
follow: each of those files sits behind an `AM_CONDITIONAL` (`Makefile.am:39-57`,
`configure.ac:841-845`) and is **off by default**, optional hardening rather than a component the
program needs. Built under Cygwin the generated `config.h` carries `/* #undef HAVE_DROP_PRIVILEGES
*/` — along with `#undef` for `eventfd` and `mlockall`, each an `AC_CHECK_FUNCS` with a fallback —
and the build completes.

So the accurate sentence about both is **no native Win32 build system**, which is a fact about what
upstream ships rather than about what the program can be. Weighed rather than assumed, each is worth
taking: on each recipe's own configure or make line, with no file in any tarball patched, Cygwin
produces binaries that run as ordinary Windows processes from directories the build never named, on
a `PATH` holding nothing but the operating system.

| OS / arch | Range | How |
| --- | --- | --- |
| macOS aarch64, x86_64 | Redis **7.2 – newest**, memcached **1.6 – newest** | **built** — upstream publishes source only, for every platform |
| Linux x86_64, aarch64 | ditto | ditto |
| Windows x86_64 | Redis **7.2 – newest**, memcached **1.6 – newest** | **built** — Cygwin's toolchain, the same recipes, nothing patched; `cygwin1.dll` travels with each |
| Windows aarch64 | — | **no toolchain builds either natively** — Cygwin has no aarch64 port, and emulation is not published under an `aarch64` manifest |

Cygwin rather than MSYS2, and the choice is about redistribution rather than about which compiles.
MSYS2's own documentation says its runtime is for its build tools rather than for programs to be
shipped; Cygwin publishes `CYGWIN_LICENSE` and `COPYING` as documents, so an archive carrying
`cygwin1.dll` under LGPLv3 can carry its terms too. Both DLLs that travel — `cygwin1.dll` and
`cyggcc_s-seh-1.dll` — were read off the binary's own import table rather than copied from anyone's
list, and they are the only two things outside `C:\Windows` the result loads.

**What it costs, said plainly.** The event loop is `select` rather than `epoll`, because Cygwin has
no `epoll` and `ae.c` falls through to it. `maxclients` settles at about 3168 instead of 10000,
because the runtime cannot raise the descriptor limit as far as the default asks and Redis says so
in its own log. Both are properties of one unreplicated development instance on a developer's
machine, which is the only thing MixEngine ever runs — and both are visible in the artifact rather
than discovered later.

The three alternatives are still no, for the reasons P8 gave. **Valkey**, which MixEngine's own
table named, is the same POSIX program forked and sends a Windows user to WSL, which [ADR
0003](https://github.com/haiquang9994/MixEngine/blob/master/.claude/decisions/0003-no-container-isolation.md)
excludes. **Memurai** is proprietary, and a repository that redistributes what it packs cannot pack
one. **The community rebuilds** are the fork nobody maintains — and compiling here is precisely how
their method is borrowed without their binaries: the tarball is upstream's, checked against
upstream's own SHA-256, and nothing in it is patched.

**One constraint leaves this repository and lands on MixEngine.** `getAbsolutePath()` in Redis's
`server.c` decides a path is absolute with `if (relpath[0] == '/')` and otherwise joins it to
`getcwd()`, so no Windows spelling of the config path survives — `C:\…` and `C:/…` both arrive glued
onto the working directory. The supervisor has to set a working directory and name `redis.conf`
relatively, which is what the smoke test does on every platform so that one rule covers all five
cells. A `/cygdrive/c/…` path would also work and is refused: it would put the emulation layer's
private spelling into a command line MixEngine builds.

**What the Redis cell costs beyond that, said plainly.** The event loop is `select` rather than
`epoll`, because Cygwin has no `epoll` and `ae.c` falls through to it. `maxclients` settles at about
3168 instead of 10000, because the runtime cannot raise the descriptor limit as far as the default
asks and the server says so in its own log. Both are properties of one unreplicated development
instance on a developer's machine, which is the only thing MixEngine ever runs, and both are visible
in the artifact rather than discovered later.

**Both `windows/aarch64` cells are empty for one reason, and it is not upstream's.** Cygwin
has no aarch64 port: the toolchain and runtime are not upstream — `aarch64-pc-cygwin` is waiting on
GCC — and porting the packages has not started, so there is nothing to build the cell with. MSYS2
does not answer it either; its `msys2-runtime`, which is the POSIX layer, is x86_64 only and its
own documentation says the unixy tools go through emulation. `CLANGARM64` is native ARM64 but is
mingw against UCRT, and neither program has an `#ifdef _WIN32` around a socket anywhere — that route is a
patch set, and nothing in this repository is patched. What is left is the x86_64 image under
emulation, which does work and was measured working on a `windows-11-arm` runner. It is still not
published: an archive whose manifest says `arch: aarch64` and whose payload is x86_64 is a lie in
the index, which is the refusal `nginx.py` already makes about its own 32-bit payload and the rule
[building-from-source.md](../building-from-source.md) sets for the whole repository — *nothing here
cross-compiles and nothing runs under emulation*. Installing the x86_64 archive on a Windows ARM
machine is a decision the daemon can make in front of the user; it is not one a manifest should make
behind them.

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
and nothing outside `libSystem` on macOS. On those four cells these are the only *built* rows here
that need no bundled libraries at all, and `relocate.verify` is what says so rather than the build
flags. What TLS would buy is an encrypted loopback connection between two processes on one
developer's machine, in exchange for an OpenSSL to bundle, to keep current and to measure a floor
against.

**The Windows cell is where that sentence stops being true, and the price is one file.** A Cygwin
binary links `cygwin1.dll` — it is what supplies the POSIX layer memcached is written against — so
that archive is two files rather than one, 4.7 MB rather than 1.6, and the DLL is **LGPLv3**. That
licence asks for more than its text: the recipient must be able to obtain the library's source and
relink against a modified one. Dynamic linking answers the second half by itself, since the DLL sits
beside the `.exe` and can be replaced; the first half is `licenses/CYGWIN-SOURCE.txt`, which names
the exact package the file came from and where Cygwin publishes its source. Nothing is patched, so
naming the package is enough to point at the corresponding source.

**And on Windows `relocate.verify` proves nothing, which is worth stating rather than assuming.**
`relocate.kind` reads a file's first four bytes and answers `None` for a PE, so `machine_files`
finds nothing in a Windows tree and `verify` returns no problems — it does not fail, it *passes
without looking*. Teaching it to read PE would turn that no-op into a real check inside seven other
recipes at once, since Caddy, Node, Python, nginx, MariaDB and PostgreSQL all call it unconditionally
on Windows, so `tools/memcached.py` answers the question locally instead: `cygcheck` reads the import
table at pack time and refuses anything that is neither Cygwin's nor `%SystemRoot%`'s, and the smoke
test then starts the binary on a `PATH` trimmed to the operating system. That last one is the check
that matters, and it is not theoretical — the spike that measured this cell passed once with a tree
containing no DLL at all, because Cygwin's own `bin` was on `PATH` and the loader found the library
there. The ARM leg, which has no Cygwin, is where it came out, as `STATUS_DLL_NOT_FOUND`.

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
