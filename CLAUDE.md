# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`vsub` is a Python CLI that runs SAS code on SAS Viya 4 from a machine with **no shared filesystem**. It moves `.sas7bdat` datasets both ways and brings logs home. Auth is SSO (OIDC). Persistent data lives in the user's HOME on the Viya side (`[storage] remote_root`, default `~/vsub_data`), exposed to SAS programs as libref `vsub`.

## Commands

```bash
uv venv && uv pip install -e '.[test]'   # dev install (.venv)
.venv/bin/python -m pytest -q            # all tests
.venv/bin/python -m pytest -q tests/test_backends.py::test_run_program_round_trip   # single test
.venv/bin/python -m pytest -q -k batch   # by keyword
.venv/bin/vsub --help
```

No linter/formatter is configured. Nothing can be tested against a real Viya here (no profile or login), so tests use fakes. The `sas-viya` CLI 1.39.14 and its batch plug-in are installed in `~/.local/bin` and `~/.sas/viya-plugins`, and `--help` output can be used to check flags.

`vendor/sas-viya-cli/` holds the proprietary SAS Viya CLI archives for all three OSes, stored via **Git LFS** (`.gitattributes`). `scripts/setup-sas-viya.sh` installs the CLI from there (after SHA256SUMS verification), plus the batch plug-in, profile and `auth loginCode`. It must stay bash 3.2-compatible (macOS `/bin/bash`), e.g. use the `${arr[@]+"${arr[@]}"}` idiom under `set -u`. Setup docs are in `docs/SETUP.md`.

## Architecture

Two backends share config (`config.py`), log scanning (`logs.py`) and naming rules (`transfer.py`):

- **Interactive** (`interactive.py`): saspy HTTP access method → Viya Compute Service.
  - One `InteractiveSession` = one compute session/pod.
  - saspy needs a config *module*, so `write_saspy_cfg()` generates `~/.config/vsub/sascfg_vsub.py` (no secrets). Tokens are passed at runtime as `SASsession(authtoken=, refreshtoken=)`.
  - Server-side values (HOME, `pathname(lib)`, hostname) are read via `macro_value()`: `%let` + `symget(outtype='str')`. Pass `'str'`, not `str`: saspy ignores the type object.
  - `ensure_root()` runs a DATA step that does `mkdir -p` via `dcreate`, then `libname vsub`. Every command that touches data calls it first.
  - `submit(check=True)` raises `SASError` when the returned LOG contains ERROR lines. Setup steps use it. The user's program runs with `check=False` and gets summarized instead.
  - On exit, tokens refreshed by saspy's background thread (`sas._io.sascfg._token/_refresh`) are written back to the cache.
- **Batch** (`batch.py`): deliberately thin wrapper over the official `sas-viya batch` CLI (subprocess). Only `submit` and `results` exist. Don't add passthroughs for list/kill/etc. (decided with the user).
  - It adds: lower-casing staged SAS file names (SAS on Linux ignores upper-case member files), a preamble with `libname jobdir "%sysget(BATCHJOBDIR)"` (plus `vsub` if `home_mounted`), flattening `get-results` output out of `<dir>/JOB_*/`, and log → exit code.
  - The job id is parsed from text output by regex. The program is written to a temp dir under its original name so the log is `<prog>.log`.
  - `SASBatch.*`/`redir.*` logs are batch-server debug logs and are excluded from scanning.
- **Auth** (`auth.py`): mirrors saspy's own flow: PKCE S256, public client `SASPy` with empty secret, refresh via `Basic SASPy:`. Tokens are cached in `~/.config/vsub/tokens.json` (0600) and keyed by Viya URL. The batch backend uses the `sas-viya` CLI's own credentials (`sas-viya auth loginCode`).
- **CLI** (`cli.py`, typer): exit codes 0 clean / 1 warnings / 2 SAS errors / 3 vsub/config/connection error. Escape rich markup (`escape()`) when printing log lines or messages. They contain `[...]`.

## Viya facts that drive the design

- `get-results` downloads the file set and then **deletes** the job and file set on the server.
- `submit-pgm` runs in SAS LOCKDOWN mode, so HOME may be unreachable from batch unless the admin allowlists it. `--job-file` is intended for small files.
- HOME is persistent only if the admin mounted storage into the compute (and batch) pod templates. `vsub check-home` writes a marker, opens a second session, and checks whether the marker is still there.
- A standalone `vsub download work.x` is meaningless: each command opens a fresh session. `run --down` can fetch any libref because it downloads before the session ends.

## Testing approach

- `tests/conftest.py::FakeSAS` replaces `saspy.SASsession`. It pattern-matches submitted SAS code (`%let`, `dcreate`, marker file write/read, `proc datasets`) and uses a local temp dir as the "remote" filesystem. If you change the SAS code generated in `interactive.py`, update FakeSAS's matching to match.
- An autouse fixture points `XDG_CONFIG_HOME` at tmp, so tests never touch the real `~/.config/vsub`.
- Batch functions take a `runner` callable, so tests inject fakes instead of calling `sas-viya`.
- In CLI tests, monkeypatch `cli._session` to inject FakeSAS.
