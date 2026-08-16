# Dates are the one claim here that is not about bytes

*Part of [mixengine-packages](../README.md), which holds the table of what is packaged.*

Everything else this repository publishes is measured. A digest, a load command, a glibc floor, the
version a binary printed about itself when it was told to — all of it can be re-measured from the
archive years later, and the check for it is the measurement again. `data/eol.json` cannot be. "PHP
stops getting security fixes for 8.2 on the 31st of December 2026" is a transcription, and a
transcription with nothing checking it is a rumour with a date on it.

It was checked at P10 for the first time, and in Ruby alone it was wrong three different ways: 3.2
was written 2026-03-31 against upstream's 2026-04-01; 3.4 and 4.0 carried dates nobody had ever
published, extrapolated from Ruby's habit of ending a line on 31 March about four years on; and
3.3's number was right but came from upstream's `expected_eol_date` rather than its `eol_date`,
which is a different claim. PHP, Node.js, Python, MariaDB and PostgreSQL were correct to the day —
which is the shape of this class of bug. It is not that hand-transcription is usually wrong. It is
that nothing tells you which of the forty-four entries is the one that is.

So every date now comes from a machine-readable document its publisher maintains, `tools/eol.py
--update` transcribes them and `tools/eol.py` proves them. Six kinds, six publishers, **no
third-party mirror** — the roadmap expected two of them to need `endoflife.date` and none of them
do. The other four kinds here have no date at all, because Caddy, nginx, Redis and Memcached publish
no schedule; that stays an absence rather than becoming a guess.

Three things about it are worth stating, because each replaced something that looked reasonable.

*The check runs on a clock, not at pack time, because the pattern it grew out of does not
generalise.* `mariadb.py` prints the end-of-life date it saw on every run — free, because the date
arrives in the same document the download does, and enough to catch a moved schedule the next time
that series is packed. But an end-of-life date does not change when something is packed. It changes
on a calendar, and the lines nearest their date are precisely the ones nobody is packing any more:
Ruby 3.2 ended in April 2026 and will never be repacked, so the wrong date would have sat in the
index until a human happened to look. `check-eol.yml` runs weekly, and on any push that touches
either the data or the tool. What stayed in the recipes is the half that costs nothing —
`eol.announce` prints what is written down, makes no network call, and cannot fail a build.

*Each publisher's document is transcribed in full, not trimmed to the versions this repository
offers*, which is why PHP 4.3 and PostgreSQL 6.3 are in the file. The old curated list was the
problem rather than the tidy version of it: **a subset cannot be checked**, because nothing
distinguishes a line deliberately left out from one forgotten. Transcribing the whole document makes
the check an equality, and `mkindex.py` reads the lines it needs and ignores the rest.

*And a corrected date now reaches versions nobody rebuilt.* `mkindex.py` used to apply
`data/eol.json` only to the artifacts a run had just added, which meant the correction to Ruby 3.2
could never have reached the package already published — the file would have been right and the
index would have stayed wrong. It re-dates every package on every run, and **removes** a date the
file no longer states, because un-saying something is as much a part of a correction as saying it.
