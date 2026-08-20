#!/usr/bin/env bash
#
# Build one kind and publish it as a GitHub release.
#
#     release/build.sh <kind> [version]
#
# Two things this exists to remember, because both are silent when you get them wrong. Every build
# workflow's `release` input defaults to **false** — a run without it builds everything, uploads the
# artifacts for inspection and publishes nothing installable. And the input is not called the same
# thing on every workflow: php takes `branch`, mariadb, mysql and postgres take a comma-separated
# `versions`, the other seven take `version`.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=release/_dispatch.sh
source "$here/_dispatch.sh"

# kind | workflow file | input name | default version | example | what the version may say.
#
# One table rather than a case per script, because the whole point of this file is that these facts
# are looked up instead of remembered.
kinds() {
  cat <<'EOF'
php|build-php.yml|branch||8.4|a branch (8.4) or an exact version (8.4.24). NOT "latest".
node|build-node.yml|version||22|a line (22), an exact version (22.23.2), or "lts".
python|build-python.yml|version||3.14|a line (3.14), an exact version (3.14.7), or "latest".
ruby|build-ruby.yml|version||3.4|a line (3.4), an exact version (3.4.10), or "latest".
caddy|build-caddy.yml|version||latest|a line (2), an exact version (2.11.4), or "latest".
nginx|build-nginx.yml|version||1.30|1.30 is stable, 1.31 is mainline, or "latest".
redis|build-redis.yml|version||8.10|a line (8.10), an exact version (8.10.0), or "latest".
memcached|build-memcached.yml|version||1.6|a line (1.6), an exact version (1.6.45), or "latest".
mariadb|build-mariadb.yml|versions|all||A LIST: "all" (the default), "latest", or "11.8,10.11".
mysql|build-mysql.yml|versions|all||A LIST: "all" (the default) or "5.6,8.4". NOT "latest".
postgres|build-postgres.yml|versions|all||A LIST: "all" (the default), "latest", or "18,16.10".
EOF
}

if [[ $# -lt 1 || "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat <<'EOF'
Build one kind and publish it as a GitHub release.

    release/build.sh <kind> [version]

When this finishes you STILL have to run release/publish.sh, or nobody can install it.

EOF
  printf '  %-10s  %-31s  %s\n' "KIND" "EXAMPLE" "WHAT THE VERSION MAY SAY"
  while IFS='|' read -r kind workflow input default example note; do
    [[ -z "$kind" ]] && continue
    printf '  %-10s  %-31s  %s\n' \
      "$kind" "release/build.sh $kind $example" "$note"
  done < <(kinds)
  cat <<'EOF'

Pass --no-release to build without publishing, when you only want to look at the artifacts.
EOF
  exit 0
fi

release=true
args=()
for arg in "$@"; do
  case "$arg" in
    --no-release) release=false ;;
    *) args+=("$arg") ;;
  esac
done
set -- "${args[@]}"

kind="$1"
row="$(kinds | grep "^${kind}|" || true)"
if [[ -z "$row" ]]; then
  echo "No such kind '$kind'. Run 'release/build.sh --help' for the list." >&2
  exit 1
fi
IFS='|' read -r _ workflow input default example note <<<"$row"

version="${2:-$default}"
if [[ -z "$version" ]]; then
  echo "$kind needs a version: $note" >&2
  exit 1
fi

# php resolves a branch to its newest patch by itself but has no notion of "the newest branch", so
# `latest` would dispatch fine and fail every leg minutes later.
if [[ "$kind" == "php" && "$version" == "latest" ]]; then
  echo "php does not take 'latest'. Name a branch (8.4) or an exact version (8.4.24)." >&2
  exit 1
fi

require_gh
repo="$(repo_of)"

echo "kind:     $kind"
echo "version:  $version"
if [[ "$release" == true ]]; then
  echo "release:  true"
else
  echo "release:  false  (build only, nothing is published)"
fi
echo

dispatch "$workflow" "$repo" -f "$input=$version" -f "release=$release"

echo
if [[ "$release" == true ]]; then
  cat <<'EOF'
Done. The release is up, but nobody can install it yet: MixEngine only reads the signed index.

Next:

    release/publish.sh

EOF
else
  echo "Done (--no-release: no release was created, only artifacts to download and look at)."
fi
