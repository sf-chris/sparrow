#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

for required in README.md LICENSE SECURITY.md CONTRIBUTING.md CODE_OF_CONDUCT.md CHANGELOG.md DESIGN.md ROADMAP.md; do
  if [ ! -s "$required" ]; then
    echo "Missing required release file: $required" >&2
    exit 1
  fi
done

if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  tracked="$(git ls-files)"
  private_tracked="$(
    echo "$tracked" |
      rg '(^|/)(\.env($|\.)|data/|node_modules/|\.venv/|settings\.local\.json$)' |
      rg -v '(^|/)\.env\.example$' || true
  )"
  if [ -n "$private_tracked" ]; then
    echo "Private/runtime files are tracked by git." >&2
    echo "$private_tracked" >&2
    exit 1
  fi
fi

if rg -n --hidden \
  -g '!.env' -g '!data/**' -g '!frontend/node_modules/**' -g '!.venv/**' \
  -g '!frontend/dist/**' -g '!.claude/**' \
  '(sk-ant-api[0-9A-Za-z_-]{20,}|ANTHROPIC_API_KEY=[^[:space:]#]{12,}|TMDB_API_KEY=[0-9a-fA-F]{24,})' .; then
  echo "Potential credential found in the release tree." >&2
  exit 1
fi

echo "Release tree checks passed."
