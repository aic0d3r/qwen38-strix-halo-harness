# Windows VM test for `setup.ps1`

Tests the pi side on a clean Windows 11 (no GPU, so no engine): `setup.ps1`, `setup\adopt.py`, the extensions and their
guards. Engine start failures (DLL shadowing, Smart App Control with a real engine) need bare-metal Windows.

## One-time setup (host)

```bash
sudo pacman -S --needed qemu-desktop libvirt virt-install virt-manager dnsmasq   # swtpm + edk2-ovmf too
sudo systemctl enable --now libvirtd
virsh -c qemu:///system net-start default && virsh -c qemu:///system net-autostart default
tests/windows-vm/create-vm.sh ~/Downloads/Win11_x64.iso        # 8 GB, 6 vCPU, 80 GB in ~/vms
virt-manager --connect qemu:///system                           # install Windows ("Press any key to boot from CD")
```

If the guest gets no address and you run **ufw**: its default drops DHCP, DNS and forwarding from the VM bridge.

```bash
sudo ufw allow in on virbr0 to any port 67 proto udp
sudo ufw allow in on virbr0 to any port 53
sudo ufw allow in on virbr0 to any port 8080 proto tcp        # the stub engine
sudo ufw route allow in on virbr0 && sudo ufw route allow out on virbr0
```

Pick a **Windows 11 Pro/Home** ISO if you can: the IoT LTSC evaluation image has no winget (so that branch of `setup.ps1`
cannot be tested) and probably no Smart App Control. Local account without Microsoft sign-in: Shift+F10, `start ms-cxh:localonly`.
At the desktop, shut down and snapshot: `virsh -c qemu:///system snapshot-create-as win11-test clean`
(restore: `snapshot-revert win11-test clean`, VM shut off).

## SSH into the guest (so tests are scriptable)

The console has no clipboard (no spice-vdagent), so serve the setup script and type one line in an **admin** PowerShell:

```bash
ssh-keygen -t ed25519 -N '' -f ~/.ssh/win11-test
mkdir -p /tmp/winvm-serve && sed "s|@PUBKEY@|$(cat ~/.ssh/win11-test.pub)|" tests/windows-vm/guest-ssh-setup.ps1 > /tmp/winvm-serve/s
python3 -m http.server 8080 --bind 192.168.122.1 --directory /tmp/winvm-serve        # guest: irm http://192.168.122.1:8080/s | iex
```

Then take a second snapshot (`ssh-ready`) with the VM shut off. `tests/windows-vm/vm-run.sh 'whoami'` runs PowerShell there.

## Running the tests

```bash
python3 tests/windows-vm/stub-engine.py          # fake Strata on 192.168.122.1:8080: /v1/models and scripted chat turns
# ship the committed repo (no git needed in the guest) and run the installer
git archive --format=tar HEAD -o /tmp/harness.tar
scp -i ~/.ssh/win11-test /tmp/harness.tar test@<guest-ip>:C:/Users/test/harness.tar
tests/windows-vm/vm-run.sh 'mkdir C:\Users\test\harness; tar -xf C:\Users\test\harness.tar -C C:\Users\test\harness'
tests/windows-vm/vm-run.sh 'cd C:\Users\test\harness; powershell -ExecutionPolicy Bypass -File .\setup.ps1 -Yes -Url http://192.168.122.1:8080'
```

Guards, through a real pi on Windows (the stub makes the "model" call pi's tools; `;;;` chains bash commands, `<NL>` is a newline):

```bash
vm-run.sh -p 'bash:sed -i s/a/b/ x.go'                                       # work-guard blocks it
vm-run.sh -p 'tool:write:path=C:\Users\test\p\src\app.css;;content=a{}'      # UI path with backslashes: demands the ui-ux skill
vm-run.sh -p 'tool:write:path=C:\Users\test\p\bad.py;;content=def (:<NL>'    # syntax-guard flags it (python, not python3)
vm-run.sh -p 'bash:curl -s http://192.168.122.1:8080/v1/models;;;rm -rf /c/Windows/Temp/zzz'   # taint-gate stops the second call
```

## Pitfalls found the hard way

- Do not download installers into the folder pi runs in (`C:\Users\test`). Windows resolves a bare `git` against the current directory first, so a `git.exe` Git installer there is what `pi.exec("git")` launches, and `pi -p` then never exits. Download to `$env:TEMP`.

- Windows PowerShell 5.1 strips double quotes from arguments to native commands, and pi there is a `.cmd` shim that treats an
  unquoted `|` as a pipe: keep prompts free of both (`vm-run.sh -p` already uses a here-string).
- A libvirt NAT network is "Public" to Windows; firewall rules scoped to Private (the OpenSSH default) silently drop.
- uv's installer refuses the default Restricted execution policy: run it as `powershell -ExecutionPolicy ByPass -c "irm ... | iex"`.
- pi's own installer needs Node.js 22.19+ and a terminal; over SSH install Node first (MSI, `/qn`).
