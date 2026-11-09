#!/usr/bin/env bash
# Tests for scripts/generate-cloudbridge-media.sh and the pure parts of scripts/cloudbridge_media.py. No network,
# no OpenAI key and no uv environment are used: the wrapper's precondition checks run with a PATH that holds only
# what each case needs, and the alignment and caption code runs its own self-test under python3.
#   Usage: bash scripts/tests/generate-cloudbridge-media.test.sh        Exits non-zero when a check fails.
set -euo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
REPO_ROOT=$(cd "$HERE/../.." && pwd -P)
SCRIPT="$REPO_ROOT/scripts/generate-cloudbridge-media.sh"
WORK=$(mktemp -d)
PASSED=0 FAILED=0
trap 'rm -rf "$WORK"' EXIT

check() {  # check <name> <condition...>
  local name=$1
  shift
  if "$@"; then
    PASSED=$((PASSED + 1))
  else
    FAILED=$((FAILED + 1))
    echo "FAIL: $name" >&2
  fi
}

# A minimal PATH: the shell builtins the wrapper needs, plus whatever a case adds.
mkdir -p "$WORK/bin"
for tool in bash sed dirname basename cat; do
  ln -s "$(command -v "$tool")" "$WORK/bin/$tool"
done

# --- The key is required, and never echoed --------------------------------------------------------------------------
set +e
out=$(env -i PATH="$WORK/bin" OPENAI_API_KEY="" bash "$SCRIPT" 2>&1)
rc=$?
set -e
check "missing key exits 1" test "$rc" -eq 1
check "missing key says what to load" grep -q "OPENAI_API_KEY is not set" <<<"$out"

set +e
out=$(env -i PATH="$WORK/bin" OPENAI_API_KEY="sk-test-never-printed-0123" bash "$SCRIPT" 2>&1)
rc=$?
set -e
check "missing ffmpeg exits 1" test "$rc" -eq 1
check "missing ffmpeg is named" grep -q "ffmpeg is not installed" <<<"$out"
not_printed() { ! grep -q "sk-test-never-printed" <<<"$1"; }
check "the key is never printed" not_printed "$out"

# --- Arguments ------------------------------------------------------------------------------------------------------
set +e
out=$(env -i PATH="$WORK/bin" OPENAI_API_KEY="x" bash "$SCRIPT" --bogus 2>&1)
rc=$?
set -e
check "an unknown argument exits 1" test "$rc" -eq 1
check "an unknown argument is named" grep -q "unknown argument: --bogus" <<<"$out"
help_mentions_flag() { env -i PATH="$WORK/bin" bash "$SCRIPT" --help | grep -q -- "--write-fixture"; }
check "--help prints the usage" help_mentions_flag

# --- The alignment and caption code -----------------------------------------------------------------------------------
if command -v python3 >/dev/null; then
  check "cloudbridge_media.py --self-test passes" python3 -I "$REPO_ROOT/scripts/cloudbridge_media.py" --self-test
else
  echo "skip: python3 is not installed" >&2
fi

echo "passed $PASSED, failed $FAILED"
test "$FAILED" -eq 0
