#!/usr/bin/env bash
# Build the release notes for a tag from the commits since the previous tag.
#
# The repo commits straight to main with descriptive subjects and no PRs, so
# GitHub's own generated notes have nothing to work from. The commits ARE the
# changelog, grouped by the conventional prefix they already carry.
#
# Usage: packaging/changelog.sh <tag>            # that tag vs the tag before it
#        packaging/changelog.sh <from> <to>
set -euo pipefail

if [ "$#" -eq 2 ]; then
  FROM="$1"; TO="$2"
else
  TO="${1:?usage: changelog.sh <tag> | <from> <to>}"
  # the nearest tag reachable from the commit BEFORE this one
  FROM="$(git describe --tags --abbrev=0 --match 'v*' "${TO}^" 2>/dev/null || true)"
fi

if [ -n "$FROM" ]; then
  echo "## What changed since \`${FROM}\`"
  RANGE="${FROM}..${TO}"
else
  echo "## What changed"
  RANGE="${TO}"
fi
echo

# One subject per commit, no merge noise. Conventional prefixes map to headings;
# anything unprefixed lands in "Other" rather than being dropped, because a
# release note that silently omits a change is worse than a slightly messy one.
subjects="$(git log --no-merges --pretty=format:'%s' --reverse "$RANGE")"
if [ -z "$subjects" ]; then
  echo "_No commits between the two tags._"
  exit 0
fi

emit_group() {
  local title="$1" prefix="$2" body=""
  body="$(printf '%s\n' "$subjects" | grep -E "^(${prefix})(\(|:)" || true)"
  if [ -n "$body" ]; then
    echo "### ${title}"
    echo
    printf '%s\n' "$body" | sed -E 's/^([a-z]+)(\([^)]*\))?: /- /'
    echo
  fi
}

emit_group "Features"      "feat"
emit_group "Fixes"         "fix"
emit_group "Performance"   "perf"
emit_group "Documentation" "docs"
emit_group "Other"         "chore|ci|test|refactor|build|style"

# anything with no conventional prefix at all
other="$(printf '%s\n' "$subjects" | grep -Ev '^(feat|fix|perf|docs|chore|ci|test|refactor|build|release|style)(\(|:)' || true)"
if [ -n "$other" ]; then
  echo "### Other"
  echo
  printf '%s\n' "$other" | sed -E 's/^/- /'
  echo
fi
