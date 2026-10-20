#!/usr/bin/env bash
# Start a locally installed Strata (github.com/Niko1221/Strata) the way its own setup left it:
# the newest run-<model>.sh its setup wrote, else its ./setup.sh (which starts the model on later runs).
# Folder: $STRATA_DIR, else ~/coding/Strata, ~/Strata, ~/strata, ~/src/Strata.
# One big engine at a time: stop halogen/gufo first (the wizard does that for you).
set -u
for d in "${STRATA_DIR:-}" "$HOME/coding/Strata" "$HOME/Strata" "$HOME/strata" "$HOME/src/Strata"; do
  [ -n "$d" ] && [ -f "$d/setup.py" ] && [ -f "$d/update.sh" ] && { DIR=$d; break; }
done
[ "${1:-}" = --print-dir ] && { echo "${DIR:-}"; exit 0; }  # the update check reads the version from this checkout
[ -n "${DIR:-}" ] || { echo "Strata not found (looked in \$STRATA_DIR, ~/coding/Strata, ~/Strata, ~/strata, ~/src/Strata): install it per its README, or set STRATA_DIR" >&2; exit 1; }
export BROWSER=true  # its --open would pop a browser tab on every start
cd "$DIR" || exit 1
RUN=$(ls -t "$DIR"/run-*.sh 2>/dev/null | head -1)
[ -n "$RUN" ] && exec sh "$RUN"
exec sh ./setup.sh --yes
