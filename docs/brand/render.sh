#!/usr/bin/env bash
# Re-render the README hero and the GitHub social preview from banner.html (needs chromium).
# Upload social-preview.png under Settings > General > Social preview (1280x640, under 1 MB).
set -euo pipefail
cd "$(dirname "$0")"
shot() {
  chromium --headless --disable-gpu --hide-scrollbars --force-device-scale-factor=1 \
    --window-size="$2" --screenshot="$1" "file://$PWD/banner.html" >/dev/null 2>&1
}
shot social-preview.png 1280,640
shot hero.png 1600,640
