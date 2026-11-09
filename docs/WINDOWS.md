# Run a local AI coding agent on Windows with Strata

This harness sets up the [pi](https://github.com/earendil-works/pi) coding agent against a local engine. On Windows
the engine is [Strata](https://github.com/Niko1221/Strata) (or gufo): you install and run it with its own tools, and
`setup.ps1` wires pi to it.

## Steps

1. Install Strata with its own installer (Terminal or desktop app) and start it. Check it answers:
   `curl.exe -s http://127.0.0.1:8080/v1/models`.
2. Install pi: `irm https://pi.dev/install.ps1 | iex`. pi needs Node.js 22.19+, which its installer asks for.
3. In a PowerShell in this repo:

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\setup.ps1
   ```

   Other engine or address: `.\setup.ps1 -Engine gufo -Url http://192.168.1.20:8080`. `-Yes` answers every
   prerequisite prompt with yes.
4. Start pi in your project and pick the Strata model.

## What `setup.ps1` does

- Finds a real Python 3 (the Microsoft Store stub does not count) and Git for Windows, because pi's `bash` tool and
  every guard in `extensions\` need Git Bash. Missing pieces are offered through winget, with download links where
  winget is missing (for example Windows 11 IoT LTSC).
- Offers uv (runs the semble and ddg search tools) and Serena (symbol-level code navigation).
- Runs `setup\adopt.py`, the same code `setup.sh` uses on Linux: extensions, the provider read from `/v1/models`,
  skills, packages, the pinned `rtk`, MCP servers, then the Strata checks.
- Offers to add `~\.local\bin` to your user PATH so `rtk` is found.

It never installs, starts or upgrades an engine. Use Strata's own tools for that.

## Not covered on Windows yet

- The `./harness` wizard, engine launchers and engine switching are bash and Linux only.
- The ling-tiny sidecar stays off (it is Linux/systemd), so the main model writes commits and summaries.
- The NPU tools need halogen on Strix Halo.

## What is tested

`setup.ps1` is verified on a clean Windows 11 VM against a stub engine: install, guards and MCP
(`tests/windows-vm/`). It is not yet verified on Windows hardware with a real Strata, so engine start failures
(DLL shadowing, Smart App Control) are untested here. Tell us what you hit in
[Discussions](https://github.com/aic0d3r/local-ai-harness/discussions) or an issue, with the output of
`.\setup.ps1` and your Windows build.

More on engines and hardware: [ENGINES.md](ENGINES.md).
