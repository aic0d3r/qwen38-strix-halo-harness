# Contributing

Bug reports with the diagnostics asked for in the issue form are the most useful contribution. Setup questions go in
[Discussions](https://github.com/aic0d3r/local-ai-harness/discussions).

For a pull request:

- Keep it small and say what you measured. Claims in the README and docs link to a logged run in
  [docs/BENCHMARKS.md](docs/BENCHMARKS.md); new claims should too.
- Add a line to `CHANGELOG.md` under Unreleased, in the existing Added/Changed/Fixed block.
- Run the tests before you push:

  ```bash
  for t in tests/test_*.py; do python3 "$t" || break; done   # each prints "ok"
  bun test tests/                                            # extension tests
  ```

- New hardware or engine paths: say what you tested on and what you didn't, as the Windows page does.
