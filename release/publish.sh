#!/usr/bin/env bash
#
# Regenerate the signed index from every release that exists, and publish it.
#
#     release/publish.sh
#
# This is the step that makes a build installable. `mkindex.py` reads nothing in this repository: it
# downloads every release asset and reads the manifest beside each archive, so the index it produces
# describes whatever is published at the moment it runs — including a corrected end-of-life date on
# a package released months ago.
#
# `publish` defaults to false in the workflow, the way `release` does in the builds, so the default
# here is to actually publish and `--dry` is the way to only generate and verify.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=release/_dispatch.sh
source "$here/_dispatch.sh"

publish=true
for arg in "$@"; do
  case "$arg" in
    --dry) publish=false ;;
    -h|--help)
      cat <<'EOF'
Regenerate the signed index from every release that exists, and publish it.

    release/publish.sh          # generate, sign and publish
    release/publish.sh --dry    # generate and verify only, publish nothing

Run this after every successful release/build.sh. A new release does not enter the
index by itself, and the index is the only thing MixEngine reads.
EOF
      exit 0 ;;
    *)
      echo "Unknown argument '$arg'. See release/publish.sh --help" >&2
      exit 1 ;;
  esac
done

require_gh
repo="$(repo_of)"

if [[ "$publish" == true ]]; then
  echo "publish:  true"
else
  echo "publish:  false  (generate and verify only)"
fi
echo

dispatch publish-index.yml "$repo" -f "publish=$publish"

echo
if [[ "$publish" == true ]]; then
  cat <<EOF
Done. The signed index is at:

    https://github.com/$repo/releases/download/index/index.json

If you just added a whole new LINE rather than a new patch of an existing one, two
things are still outstanding — see "A new line" in release/README.md.
EOF
else
  echo "Done (--dry: the index was generated and checked, nothing was published)."
fi
