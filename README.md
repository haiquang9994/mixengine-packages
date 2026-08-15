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
docs/         what building the "built" rows taught us, for whoever adds the next one
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
`tools/node.py` refuses with that sentence rather than with a 404 from the middle of a download.

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

# Then regenerate and sign the index from what the releases actually contain
python tools/mkindex.py --base-url … --out dist/index.json
minisign -Sm dist/index.json -s minisign.key
```

In practice none of that is run by hand: `.github/workflows/build-php.yml` takes a version, picks the
recipe from it and produces every target, `build-node.yml` does the same with one recipe and six, and
`publish-index.yml` regenerates and signs the index from every release that exists.

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
