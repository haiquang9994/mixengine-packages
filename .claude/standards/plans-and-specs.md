# Plans and specs

`docs/superpowers/` is where an agent's working documents land. It holds two folders that look
alike and are treated very differently.

## `specs/` — tracked, linkable

Design documents (`YYYY-MM-DD-slug-design.md`). They describe what a change is and why it is shaped
that way, and they outlive the work. They are committed, they go to the remote, and any document
may link to them — a [roadmap](../../docs/roadmap.md) task pointing at its design is the normal
case.

## `plans/` — local only, never referenced

Step-by-step implementation plans (`YYYY-MM-DD-slug.md`). They are scaffolding for one stretch of
work: long, quickly stale, and meaningless once the branch lands. They are gitignored — they exist
on the machine that wrote them and nowhere else.

**Never link to a file under `plans/`.** Not from `README.md`, `docs/`, `.claude/`, a commit
message, a PR body, or a code comment. A link to a path nobody else has is a dead link for every
other reader.

When you want to point at the reasoning behind a change, point at its spec, or write the rule down
where rules live in this repository: a page under [docs/](../../docs/), or the package page it
belongs to. If something in a plan is worth keeping, move it there — do not leave it in the plan
and link there.
