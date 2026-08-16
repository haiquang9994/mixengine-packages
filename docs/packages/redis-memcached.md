# Redis and Memcached, where the evaluation had nothing to weigh

*Part of [mixengine-packages](../../README.md), which holds the table of what is packaged.*

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
