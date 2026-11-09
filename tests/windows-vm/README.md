# Windows VM test for `setup.ps1`

Tests the pi side on a clean Windows 11 (no GPU, so no engine): `setup.ps1`, `setup\adopt.py`, the extensions.
Engine start failures (DLL shadowing, Smart App Control with a real engine) need bare-metal Windows.

```bash
virsh -c qemu:///system net-start default           # once per boot unless autostart is set
tests/windows-vm/create-vm.sh ~/Downloads/Win11_x64.iso
virt-manager --connect qemu:///system               # open the console; "Press any key to boot from CD"
```

Windows setup: a local account without a network helps (Shift+F10, `start ms-cxh:localonly` on current builds). When the
desktop is up, **shut down and snapshot** (`virsh -c qemu:///system snapshot-create-as win11-test clean`): Smart App Control
cannot be re-armed once off, so every SAC run starts from that snapshot.

On this box (the guest sees it as `192.168.122.1`):

```bash
python3 tests/windows-vm/stub-engine.py            # fake Strata on 192.168.122.1:8080
```

## Checklist (in the guest)

1. Install pi: `irm https://pi.dev/install.ps1 | iex`; clone this repo (Git for Windows first, or let `setup.ps1` offer it).
2. `powershell -ExecutionPolicy Bypass -File .\setup.ps1 -Url http://192.168.122.1:8080` - without Git/Python installed it must
   offer winget; the `python` Store stub must not be taken for Python.
3. Second run changes nothing ("already current", "left untouched").
4. `pi` starts and loads every extension without errors; `!printf ok` works (Git Bash); `/harness-update` runs `setup.ps1`.
5. Open questions this run answers: what `pi.exec("head")`/`python3` do when missing; where rtk reads its `config.toml`;
   whether `%USERPROFILE%\.local\bin` on PATH makes `rtk` run; Smart App Control on vs `rtk.exe`/`pi`.
6. `python setup\strata_doctor.py check --url http://192.168.122.1:8080`
