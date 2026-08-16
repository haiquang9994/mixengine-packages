# nginx, where upstream's own binary turned out to be the specification

*Part of [mixengine-packages](../../README.md), which holds the table of what is packaged.*

| OS / arch | Range | How |
| --- | --- | --- |
| Windows x86_64 | **1.26.0 – newest** | **borrowed** — upstream's own `nginx-<version>.zip`, repacked |
| Windows aarch64 | — | **empty**; upstream publishes one Windows build and it is 32-bit x86 |
| macOS aarch64, x86_64 | **1.26.0 – newest** | **built** from upstream's source release |
| Linux x86_64, aarch64 | **1.26.0 – newest** | ditto |

The shape is [PHP's](php.md) — borrow where the publisher builds, compile where it does not — and
the interesting part is what happened when the two halves were asked to mean the same thing. Run
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
