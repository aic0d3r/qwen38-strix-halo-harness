#!/usr/bin/env bash
# Run PowerShell on the test VM over SSH, with PATH re-read from the registry (an SSH session started before an install does not see it).
#   tests/windows-vm/vm-run.sh 'pi --version'
#   tests/windows-vm/vm-run.sh -p 'bash:echo hi'     one pi turn through stub-engine.py (see its docstring for the prompt forms)
# Env: VM_NAME (win11-test), VM_USER (test), VM_KEY (~/.ssh/win11-test), VM_IP (from libvirt's DHCP lease).
set -eu
NAME=${VM_NAME:-win11-test}; USER_=${VM_USER:-test}; KEY=${VM_KEY:-$HOME/.ssh/win11-test}
IP=${VM_IP:-$(virsh -c qemu:///system domifaddr "$NAME" 2>/dev/null | awk '/ipv4/{print $4}' | cut -d/ -f1 | head -1)}
[ -n "$IP" ] || { echo "no IP for $NAME: is it running (virsh -c qemu:///system start $NAME)?" >&2; exit 1; }
PRE='$env:Path=[Environment]::GetEnvironmentVariable("Path","Machine")+";"+[Environment]::GetEnvironmentVariable("Path","User"); '
if [ "${1:-}" = -p ]; then
  # a here-string keeps the prompt literal; the pi shim there is a .cmd, so quotes and unquoted | in the prompt would not survive
  CMD="cd C:\\Users\\$USER_; \$p = @'
$2
'@; pi -p \$p.Trim() --provider strata --model stub-model 2>&1 | Select-Object -First 15"
else
  CMD=${1:?usage: vm-run.sh '<powershell>' | -p '<stub prompt>'}
fi
exec ssh -i "$KEY" -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=10 -o LogLevel=ERROR "$USER_@$IP" "$PRE$CMD"
