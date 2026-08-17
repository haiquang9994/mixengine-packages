# What to do when there is a new version

*The runbook. Why any of it is shaped this way is in
[docs/adding-a-version.md](../docs/adding-a-version.md).*

## The short version

Two commands, in that order, and that is the whole job:

```bash
release/build.sh php 8.4    # build every target, publish it as a GitHub release
release/publish.sh          # regenerate the signed index from every release there is
```

If you cannot remember a kind's name or how its version is spelled:

```bash
release/build.sh --help
```

The scripts read the repository off `git remote origin`, wait for the run to finish, and print the
next step. All they need is a logged-in `gh` (`gh auth login`).

## Three things that are silent when you get them wrong, and are handled here

1. **`release` defaults to `false`** on every build workflow, and so does `publish` on
   `publish-index`. Dispatching by hand from the GitHub UI and forgetting to turn it on builds
   everything, uploads artifacts for inspection, and publishes nothing. `build.sh` turns it on; pass
   `--no-release` when you actually want the inspection-only run.
2. **The version input is not called the same thing on every workflow.** `php` takes `branch`,
   `mariadb` and `postgres` take a *list* called `versions`, the other seven take `version`. That
   table lives inside `build.sh` so it does not have to live in your head.
3. **A finished build is not a finished job.** A new release does not enter the index by itself, and
   the signed index is the only thing MixEngine reads. Until `publish.sh` runs, nobody can install
   what you just built.

## What each kind's version may say

| Kind | Example | What the version may say |
| --- | --- | --- |
| `php` | `release/build.sh php 8.4` | a branch (`8.4`) or an exact version (`8.4.24`). **Not** `latest` |
| `node` | `release/build.sh node 22` | a line, an exact version, or `lts` |
| `python` | `release/build.sh python 3.14` | a line, an exact version, or `latest` |
| `ruby` | `release/build.sh ruby 3.4` | a line, an exact version, or `latest` |
| `caddy` | `release/build.sh caddy latest` | a line, an exact version, or `latest` |
| `nginx` | `release/build.sh nginx 1.30` | `1.30` is stable, `1.31` is mainline |
| `redis` | `release/build.sh redis 8.10` | a line, an exact version, or `latest` |
| `memcached` | `release/build.sh memcached 1.6` | a line, an exact version, or `latest` |
| `mariadb` | `release/build.sh mariadb` | a list; empty means `all`, every supported series |
| `postgres` | `release/build.sh postgres` | a list; empty means `all`, every supported major |

`mariadb` and `postgres` take a list because upstream maintains several lines at once with
end-of-life dates years apart. Running `all` is the correct thing to do, not the lazy one.

Some cells of the table in the [main README](../README.md) are empty **on purpose** — Redis on
Windows, Memcached and nginx on Windows ARM, and others. The run's log names every cell it left
empty and why. That is not a failure.

## A new line

Everything above is for a **new patch of a line that already exists** — PHP 8.4.24 → 8.4.25. That
case touches no file in this repository.

A **whole new line** — PHP 8.6, Node 28, PostgreSQL 19, Ruby 3.5 — needs three more things after
`build.sh` and `publish.sh`:

```bash
python tools/eol.py --update   # transcribe the schedule from the publisher again
git add data/eol.json && git commit -m "chore(eol): transcribe the schedule again"
```

- **Never type a date into `data/eol.json` by hand.** `tools/eol.py --check` will fail on it, which
  is exactly what it was written to do.
- **Add a row to the table in the [main README](../README.md).** The table lists *lines*, so a new
  patch never changes it.
- **Run `release/publish.sh` again** after committing the date, because the date lives in the index
  and `mkindex.py` re-dates *every* package on every run, not only the ones it just added.

A new line often has cells that do not exist yet — Windows on ARM usually arrives late. An empty
cell has to be empty *for a reason that is written down* in [docs/packages/](../docs/packages/),
not empty because nobody looked.

## Do not

**Do not re-run an old build over a tag that is already published.** A new patch is a new version is
a new release, so the flow above is safe. But re-running an old build uploads over the existing
assets with `--clobber`: same URL, same name, different bytes — while the signature on the published
index still describes the old ones. `check-archive.yml` will catch it the following Wednesday. The
full reasoning is in [docs/the-archive.md](../docs/the-archive.md).

## Nothing tells you a new version exists

The two scheduled jobs — `check-eol.yml` for a schedule that moved, `check-archive.yml` for assets
that changed under you — both watch something *already published* going wrong. Neither watches for
something new appearing. So every so often:

```bash
release/build.sh caddy latest
release/build.sh redis latest
# …
```

Each recipe asks upstream what the newest release is. If it resolves to a version already published
it will rebuild and clobber that same tag, so **do not run these blind** — compare the
[table in the main README](../README.md) against upstream's release page first, or use
`--no-release` to see which version it resolves to without publishing anything.
