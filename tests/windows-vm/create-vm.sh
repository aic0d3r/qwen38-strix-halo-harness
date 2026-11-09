#!/usr/bin/env bash
# Windows 11 test VM for setup.ps1 (libvirt/QEMU/KVM, system connection). No GPU: it tests the pi side only, not engines.
#   tests/windows-vm/create-vm.sh /path/to/Win11_x64.iso [name]
# Needs: qemu-desktop libvirt virt-install dnsmasq (+ swtpm, edk2-ovmf), `virsh net-start default`, your user in group libvirt.
# Disk stays in ~/vms (the small / partition cannot hold it). SATA disk + e1000e NIC: Windows has in-box drivers for both,
# so no virtio driver ISO is needed. The guest reaches this host at 192.168.122.1 (see stub-engine.py).
set -eu
ISO=${1:?usage: create-vm.sh /path/to/Win11.iso [name]}
NAME=${2:-win11-test}
URI=qemu:///system
[ -f "$ISO" ] || { echo "no such ISO: $ISO" >&2; exit 1; }
virsh -c $URI net-info default 2>/dev/null | grep -q 'Active:.*yes' || { echo "start the network first: virsh -c $URI net-start default" >&2; exit 1; }
virsh -c $URI dominfo "$NAME" >/dev/null 2>&1 && { echo "VM $NAME exists (virsh -c $URI undefine $NAME --nvram --tpm --remove-all-storage to drop it)" >&2; exit 1; }
mkdir -p "$HOME/vms"
exec virt-install --connect $URI --name "$NAME" --os-variant win11 \
  --memory 8192 --vcpus 6 --cpu host-passthrough \
  --disk path="$HOME/vms/$NAME.qcow2",size=80,bus=sata,format=qcow2 \
  --cdrom "$ISO" \
  --network network=default,model=e1000e \
  --graphics spice --video model.type=vga \
  --tpm backend.type=emulator,backend.version=2.0,model=tpm-crb \
  --boot uefi,firmware.feature0.name=secure-boot,firmware.feature0.enabled=yes \
  --noautoconsole
