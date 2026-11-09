#!/usr/bin/env bash
# Generate the three captioned replay clips of the fictional CloudBridge Summit (simulated excerpts, SPEAKER-1):
# OpenAI text-to-speech over a title slide, assembled with ffmpeg, with WebVTT captions timed from a transcription of
# the generated audio. The clips go to apps/web/public/media/cloudbridge/ (served at /media/cloudbridge/), with a
# manifest.json recording their provenance. The work itself is in scripts/cloudbridge_media.py; this wrapper checks
# the tools and the key, and runs it in the backend's uv environment with the `media` dependency group (Pillow).
#   Usage: scripts/generate-cloudbridge-media.sh [--write-fixture]
#     --write-fixture  also writes the clips into the fixture's `transcripts` entries (cloudbridge.json)
#   Needs OPENAI_API_KEY in the environment (. ~/.config/conferenceops/env.sh), ffmpeg, ffprobe and uv. Model calls
#   are cached under .cache/cloudbridge-media/, so a re-run only pays for a clip whose text changed.
#   Tests: scripts/tests/generate-cloudbridge-media.test.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

die() {
  echo "generate-cloudbridge-media: $*" >&2
  exit 1
}

main() {
  local tool
  for arg in "$@"; do
    case "$arg" in
      --write-fixture | --self-test) ;;
      -h | --help)
        sed -n '2,11p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
        return 0
        ;;
      *) die "unknown argument: $arg (usage: scripts/generate-cloudbridge-media.sh [--write-fixture])" ;;
    esac
  done
  # The key is only checked for presence, never printed (SEC-1).
  [[ -n "${OPENAI_API_KEY:-}" ]] || die "OPENAI_API_KEY is not set. Load it first: . ~/.config/conferenceops/env.sh"
  for tool in ffmpeg ffprobe uv; do
    command -v "$tool" >/dev/null || die "$tool is not installed"
  done
  exec uv run --project "$ROOT/backend" --group media python "$ROOT/scripts/cloudbridge_media.py" "$@"
}

main "$@"
