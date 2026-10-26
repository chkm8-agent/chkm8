#!/bin/bash
# Prepare a local repo for chkm8's GitHub (github.com/chkm8-agent) with the privacy guards on.
#   infra/git/setup-repo.sh <repo dir>
# Sets, in that repo's .git/config only (no global git config is touched):
#   - identity: chkm8-agent + GitHub noreply email (no real email in commit metadata)
#   - credentials: infra/git-credential-chkm8 (token from the macOS Keychain at push time)
#   - redact filter: real private values <-> <redacted:name> placeholders (see infra/git/redact)
#   - hooks: infra/git/hooks (pre-commit + pre-push scans)
# and writes a default .gitattributes if the repo has none.
set -euo pipefail
REPO="$(cd "${1:?usage: setup-repo.sh <repo dir>}" && pwd)"
TOOLS="$(cd "$(dirname "$0")" && pwd)"
cd "$REPO"
[[ -d .git ]] || git init -q -b main

git config user.name "chkm8-agent"
git config user.email "279372783+chkm8-agent@users.noreply.github.com"
git config --replace-all credential.https://github.com.helper ""
git config --add credential.https://github.com.helper "$TOOLS/../git-credential-chkm8"
git config filter.redact.clean "$TOOLS/redact clean"
git config filter.redact.smudge "$TOOLS/redact smudge"
git config filter.redact.required true
git config core.hooksPath "$TOOLS/hooks"
# every commit lists r3lik as co-author (prepare-commit-msg); noreply address = counts on his GitHub graph
git config chkm8.coauthor "r3lik <3232601+r3lik@users.noreply.github.com>"

if [[ ! -f .gitattributes ]]; then
  cat > .gitattributes <<'EOF'
# Private values (emails, wallet, server IP, ...) are swapped for <redacted:name> placeholders on
# commit and restored on checkout by the "redact" filter (chkm8 infra/git/redact). A clone without
# that filter just sees the placeholders.
* filter=redact
# binaries: never filtered
*.png -filter
*.jpg -filter
*.jpeg -filter
*.webp -filter
*.gif -filter
*.ico -filter
*.pdf -filter
*.pkl -filter
*.woff -filter
*.woff2 -filter
*.ttf -filter
*.otf -filter
*.zip -filter
*.gz -filter
EOF
fi
echo "ok: $REPO (identity, credentials, redact filter, hooks)"
