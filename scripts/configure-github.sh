#!/bin/sh
set -eu
# Run after pushing the CI workflow and authenticating with `gh auth login`.
# Protects only the repository's default branch, with no mandatory human review.
if ! command -v gh >/dev/null 2>&1; then
  echo "Install GitHub CLI, run 'gh auth login', then rerun this script." >&2
  exit 1
fi
REPOSITORY="${1:-$(gh repo view --json nameWithOwner --jq .nameWithOwner)}"
BRANCH="$(gh api "repos/$REPOSITORY" --jq .default_branch)"
ENCODED_BRANCH="$(python3 -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.argv[1], safe=""))' "$BRANCH")"
PROTECTED="$(gh api "repos/$REPOSITORY/branches/$ENCODED_BRANCH" --jq .protected)"
if [ "$PROTECTED" = true ]; then
  echo "Existing protection for $REPOSITORY/$BRANCH was left unchanged." >&2
  echo "Add 'CI required' to its required status checks in GitHub settings." >&2
  exit 1
fi
gh api --method PUT "repos/$REPOSITORY/branches/$ENCODED_BRANCH/protection" --input - <<'JSON'
{
  "required_status_checks": {"strict": true, "contexts": ["CI required"]},
  "enforce_admins": true,
  "required_pull_request_reviews": null,
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false
}
JSON
gh api --method PATCH "repos/$REPOSITORY" \
  -F delete_branch_on_merge=true -F allow_squash_merge=true >/dev/null
gh api --method PUT "repos/$REPOSITORY/vulnerability-alerts"
echo "Protected $REPOSITORY/$BRANCH with the 'CI required' check."
echo "Install the Renovate app for this repository: https://github.com/apps/renovate"
