#!/usr/bin/env bash
# Local dry-run for an AIline release.
#
# Steps:
#   1. Ensure poetry-dynamic-versioning plugin is installed for the active Poetry.
#   2. Install runtime + dev deps.
#   3. Run the test suite (release gate).
#   4. Build sdist + wheel into ./dist (clean rebuild).
#   5. Smoke-test the wheel in a throwaway venv: install + `ailine --help`.
#
# Run from the repo root: bash scripts/release-check.sh

set -euo pipefail

cd "$(dirname "$0")/.."

if ! command -v poetry >/dev/null 2>&1; then
    echo "error: 'poetry' not found on PATH" >&2
    exit 1
fi

echo "==> Ensuring poetry-dynamic-versioning plugin is installed"
poetry self add "poetry-dynamic-versioning[plugin]" >/dev/null 2>&1 || \
    poetry self show plugins 2>/dev/null | grep -q poetry-dynamic-versioning || {
        echo "error: failed to install poetry-dynamic-versioning plugin" >&2
        exit 1
    }

echo "==> Installing project (with dev deps)"
poetry install --with dev --no-interaction

echo "==> Running tests"
poetry run pytest -q

echo "==> Building sdist + wheel"
rm -rf dist
poetry build

WHEEL=$(ls dist/ailine-*.whl | head -n1)
SDIST=$(ls dist/ailine-*.tar.gz | head -n1)
if [[ -z "$WHEEL" || -z "$SDIST" ]]; then
    echo "error: expected both wheel and sdist in dist/" >&2
    exit 1
fi
echo "    built: $SDIST"
echo "    built: $WHEEL"

echo "==> Smoke-testing wheel in a throwaway venv"
TMP_VENV=$(mktemp -d)
trap 'rm -rf "$TMP_VENV"' EXIT
python3 -m venv "$TMP_VENV"
# shellcheck disable=SC1091
"$TMP_VENV/bin/pip" install --quiet --upgrade pip
"$TMP_VENV/bin/pip" install --quiet "$WHEEL"
"$TMP_VENV/bin/ailine" --help >/dev/null

echo "==> OK"
echo "Artifacts in dist/ are ready to ship. Tag with: git tag vX.Y.Z && git push origin vX.Y.Z"
