#!/usr/bin/env bash
# Sourced by the download scripts (needs $HF_BIN). `hf download` resumes from its .incomplete files, but a long
# download can die midway on an expired Xet token ("Cannot send a request, as the client has been closed", seen at 94%
# of a 50 GB fetch), so retry it a few times before giving up. A gated repo fails every attempt in a few seconds.
hf_dl() {
  local n
  for n in 1 2 3; do
    "$HF_BIN" download "$@" && return 0
    [ "$n" -lt 3 ] && { echo "  download interrupted (attempt $n of 3) - resuming ..." >&2; sleep 5; }
  done
  return 1
}
