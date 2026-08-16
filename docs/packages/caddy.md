# Caddy, and what is different about a service

*Part of [mixengine-packages](../../README.md), which holds the table of what is packaged.*

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
