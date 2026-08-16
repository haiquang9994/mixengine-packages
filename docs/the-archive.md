# Nothing that has been published may be deleted

*Part of [mixengine-packages](../README.md), which holds the table of what is packaged.*

The index is cumulative by promise: a blueprint pinning PHP 8.1.29 has to keep installing years after
8.1 stopped being built. That promise is not a property of the code, it is a property of the
**releases**, and it makes every asset ever uploaded here load-bearing rather than historical. In
full, and there are two kinds of them:

- **Every archive.** `php-8.1.29-linux-x86_64.tar.zst` and its two hundred siblings. One deleted is
  one version that stops installing on one platform, silently, for everybody who pinned it.
- **Every `<archive>.json` beside it.** These are easy to mistake for debris and they are the input
  the index is *made from*: `publish-index.yml` does not rebuild the index from anything in this
  repository, it downloads every release asset and reads the manifest next to each archive. A
  deleted sidecar leaves the archive perfectly intact and quietly drops that cell out of every index
  generated afterwards.

The **`index` tag is the single exception**, and by design: it holds the newest `index.json` and its
signature, nothing else, and each publish moves it. That is why the URL MixEngine reads never
changes and why nothing accumulates there.

**A deletion cannot be undone, which is the part that is easy to get wrong.** The instinct is that a
lost artifact can be rebuilt from the recipe that made it — and it can, but not to the same bytes.
These are compressed archives packed at a different minute by a different runner from sources that
may themselves have moved, so the sha256 in the index will not match, and the index is signed.
Recovering means publishing a *different* artifact under the same version and re-signing an index
that now describes it differently, which anyone who pinned the old hash is entitled to read as
tampering. There is no quiet repair for this. There is only how long it takes to find out.

GitHub cannot be told any of the above. Tag protection, if you turn it on in the repository settings,
stops a tag being deleted and does not stop a release or an individual asset being deleted under it,
which is the failure mode this section is about. So the rule is written here and the enforcement is
detection: `check-archive.yml` runs `tools/permanence.py` every Wednesday against the published,
signature-verified index, and asks two questions of it.

*Is every asset still there* — one `HEAD` for each of the 388 URLs the current index implies, which
costs seconds and no bandwidth. The `Content-Length` that comes back for free is compared to the size
the index recorded, and that catches the second-most-likely accident after deletion: a build workflow
re-run against an existing tag, uploading a rebuilt archive over the old one with `--clobber`. Same
URL, same name, different file.

*Is it still the bytes we signed* — which cannot be answered without downloading the whole thing, six
gigabytes today and growing with every version published. So a fixed **fraction** is hashed each run
rather than a fixed count, and the difference matters: a count keeps the weekly bill flat and lets
coverage rot as the archive grows, a fraction keeps coverage flat and lets the bill grow with the
thing it is insuring. At the default of eight slices every asset is hashed within eight weeks however
many there are. Which slice an asset is in comes from a digest of its URL, so a version published
mid-cycle joins one fixed slice and is hashed inside the cycle instead of reshuffling everything
else out of the week it was in.

## The signing key

The index is signed with minisign (Ed25519) and the public key is compiled into MixEngine, so
rotating it needs an application update. The private key lives only in this repository's Actions
secrets:

```bash
minisign -G -p minisign.pub -s minisign.key   # keep minisign.key out of git, forever
```

`minisign.pub` is committed — it is public by definition, and having it in the tree is how a reader
checks that the key compiled into MixEngine is the one signing this index.
