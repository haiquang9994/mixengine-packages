# Adding a version

*Part of [mixengine-packages](../README.md), which holds the table of what is packaged.*

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

# Ask whether the archive the published index promises is still there
python tools/permanence.py               # HEAD every asset, hash this week's eighth of them
python tools/permanence.py --slices 1    # hash all of it, which is a six-gigabyte download
```

In practice none of that is run by hand: `.github/workflows/build-php.yml` takes a version, picks the
recipe from it and produces every target; `build-node.yml`, `build-python.yml`, `build-caddy.yml`,
`build-redis.yml`, `build-memcached.yml` and `build-nginx.yml` do the same with one recipe and six;
`build-ruby.yml` runs six legs across two recipes; and `publish-index.yml` regenerates and signs the
index from every release that exists. Two run on a clock rather than on a request — `check-eol.yml`
and `check-archive.yml` — and [dates](end-of-life-dates.md) and [the archive](the-archive.md) are
why: both watch something that can go wrong while nothing in this repository is touched.

Three of those keep legs in the matrix that produce nothing by design — both Windows legs for Redis
and Memcached, the ARM64 one for nginx. An empty cell stated in every run's log is worth a runner
minute; a row somebody has to remember is missing is not. See
[Redis and Memcached](packages/redis-memcached.md) and [nginx](packages/nginx.md) for why each of
those cells is empty.

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
