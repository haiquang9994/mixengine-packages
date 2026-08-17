# Shared by build.sh and publish.sh: fire a workflow_dispatch and wait for that run.
#
# Sourced, never executed. It exists so the two scripts cannot drift on the one part that is fiddly
# — `gh workflow run` prints nothing a caller can follow, so the run has to be identified by
# watching the listing change rather than by asking for an id that was never handed out.

# The repository these scripts drive, read off the checkout they live in so a fork works unchanged.
repo_of() {
  local root url
  root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  url="$(git -C "$root" remote get-url origin 2>/dev/null || true)"
  if [[ -z "$url" ]]; then
    echo "no git remote called origin here — is this a checkout of the repository?" >&2
    exit 1
  fi
  sed -E 's#.*github\.com[:/]##; s#\.git$##' <<<"$url"
}

require_gh() {
  if ! command -v gh >/dev/null 2>&1; then
    cat >&2 <<'EOF'
The GitHub CLI is not installed. Install it and log in:

    brew install gh
    gh auth login

EOF
    exit 1
  fi
  if ! gh auth status >/dev/null 2>&1; then
    echo "gh is not logged in. Run: gh auth login" >&2
    exit 1
  fi
}

# dispatch <workflow file> <repo> [-f k=v ...]
#
# Waits for the run to finish and exits non-zero if it failed, so the caller can just `if`.
dispatch() {
  local workflow="$1" repo="$2"; shift 2

  local before after id
  before="$(gh run list --repo "$repo" --workflow "$workflow" --limit 1 \
              --json databaseId --jq '.[0].databaseId // 0')"

  echo "==> gh workflow run $workflow --repo $repo $*"
  gh workflow run "$workflow" --repo "$repo" "$@"

  # GitHub takes a few seconds to create the run. Newest-first listing, so a different id at the
  # top is the one just asked for.
  id=""
  for _ in $(seq 1 40); do
    sleep 3
    after="$(gh run list --repo "$repo" --workflow "$workflow" --limit 1 \
               --json databaseId --jq '.[0].databaseId // 0')"
    if [[ "$after" != "$before" ]]; then id="$after"; break; fi
  done

  if [[ -z "$id" ]]; then
    echo
    echo "Dispatched, but no new run appeared within two minutes."
    echo "Look for it by hand: gh run list --repo $repo --workflow $workflow"
    return 1
  fi

  echo
  echo "Run: https://github.com/$repo/actions/runs/$id"
  echo "Running now — leave this open. Ctrl-C only closes this view, it does not cancel the run."
  echo

  if gh run watch "$id" --repo "$repo" --exit-status; then
    return 0
  fi

  echo
  echo "The run failed. Read the part that broke:"
  echo
  echo "    gh run view $id --repo $repo --log-failed"
  echo
  return 1
}
