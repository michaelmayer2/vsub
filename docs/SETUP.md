# Setup

You need to set up two clients, each with its own SSO login:

| Used by | Client | Login | Tokens stored in |
|---|---|---|---|
| `vsub run/upload/download/ls/rm/check-home` | saspy, through the `SASPy` OAuth client | `vsub login` | `~/.config/vsub/tokens.json` |
| `vsub batch ...` | `sas-viya` CLI + batch plug-in, through the `sas.cli` OAuth client | `sas-viya auth loginCode` | `~/.sas/credentials.json` |

An authorization code is single-use and only works for the client that requested it. So you sign in once for each client, both times through the same SASLogon in the browser.

## 0. Prerequisites

- Python ≥ 3.10 and [uv](https://docs.astral.sh/uv/) (or pip)
- [Git LFS](https://git-lfs.com/). The SAS Viya CLI archives in `vendor/sas-viya-cli/` are stored with LFS:
  ```bash
  git lfs install          # once per machine
  git lfs pull             # fetch the CLI archives after cloning
  ```
- The Viya URL, e.g. `https://viya.example.com`, and its CA certificate if the site certificate isn't publicly trusted

## 1. Install vsub

```bash
uv venv && uv pip install -e .     # vsub in .venv/bin
# or, to put vsub on PATH (in ~/.local/bin):
uv tool install -e .
```

## 2. Configure and log in (interactive backend)

```bash
vsub init          # prompts for Viya URL + compute context and writes the config
vsub login         # prints a SASLogon URL: open it, sign in, paste the code
vsub check-home    # verifies that HOME on Viya persists across sessions
```

- **Config file:** `~/.config/vsub/config.toml` (`%USERPROFILE%\.config\vsub\config.toml` on Windows). Override it with `$VSUB_CONFIG`.
- **TLS:** for a private CA, set `cafile = "/path/to/viya-ca.pem"` under `[viya]`. Use `verify = false` only for throwaway test systems.
- **No client registration needed:** `vsub login` uses the `SASPy` client that Viya ships by default, with the authorization code + PKCE flow.
- **Token renewal:** tokens refresh automatically. Run `vsub login` again only when the refresh token has expired.
- **If `check-home` reports NOT PERSISTENT:** your Viya admin needs to mount persistent home directories into the compute pod template. Until then, `vsub upload`ed data disappears with the pod.

## 3. SAS Viya CLI + batch plug-in (batch backend)

### macOS / Linux (x86_64)

```bash
scripts/setup-sas-viya.sh                   # all steps below
scripts/setup-sas-viya.sh --url https://viya.example.com --profile prod
scripts/setup-sas-viya.sh --help            # --prefix, --skip-install/-plugin/-profile/-login
```

The script runs these steps:

1. **Install:** verifies the archive for your OS against `vendor/sas-viya-cli/SHA256SUMS` and installs `sas-viya` to `~/.local/bin` (change with `--prefix`). On Apple Silicon it warns if Rosetta 2 is missing, because the macOS build is x86_64.
2. **Plug-in:** `sas-viya plugins install --repo SAS batch`. This downloads from SAS's plug-in repository, so it needs internet access.
3. **Profile:** `sas-viya profile init --sas-endpoint <url> --output text`. The URL comes from `--url`, `$VSUB_URL` or the vsub config.
4. **Login:** `sas-viya auth loginCode`. Open the URL it prints, sign in, and paste the code.

Make sure `~/.local/bin` is on your `PATH`. Otherwise set `cli = "/full/path/to/sas-viya"` under `[batch]` in the vsub config. If you configured a profile other than `Default`, also set `profile = "<name>"` there.

### Windows (PowerShell)

```powershell
git lfs pull
$zip = "vendor\sas-viya-cli\sas-viya-cli-1.39.14-windows.zip"
(Get-FileHash $zip -Algorithm SHA256).Hash.ToLower()     # compare with vendor\sas-viya-cli\SHA256SUMS
New-Item -ItemType Directory -Force "$env:USERPROFILE\bin" | Out-Null
Expand-Archive $zip -DestinationPath "$env:USERPROFILE\bin" -Force
$env:PATH += ";$env:USERPROFILE\bin"                     # add permanently via System Settings

sas-viya plugins install --repo SAS batch
sas-viya profile init --sas-endpoint https://viya.example.com --output text
sas-viya auth loginCode
```

### TLS with a private CA

Point the CLI at the CA bundle with the `SSL_CERT_FILE` environment variable, e.g. `export SSL_CERT_FILE=/path/to/viya-ca.pem`. The global `--insecure` / `-k` option skips certificate validation and is for test systems only.

### Offline plug-in install

If the machine can't reach the SAS plug-in repository, download the plug-in on another machine. Then install it with `sas-viya plugins install --file <path-to-plugin>`.

### Using the `vsub` library in batch jobs

By default, batch programs only get `jobdir` (the job directory). Inputs come in with `-f`, and outputs come back with `vsub batch results`. To use the persistent `vsub` library in batch as well:

1. Check that batch pods can reach your home directory, using a small test job:
   ```sas
   libname vsub "/home/<user>/vsub_data";   /* the path `vsub check-home` printed */
   ```
   If the log shows `not in the list of accessible paths when SAS is in the lockdown state`, the admin has to allow the path first.
2. In the vsub config, set an **absolute** `remote_root` and `home_mounted = true`:
   ```toml
   [storage]
   remote_root = "/home/<user>/vsub_data"
   [batch]
   home_mounted = true
   ```
   Batch pods don't get your HOME (`%sysget(HOME)` is `/` there), so a `~` path can't work in batch. vsub refuses it.

With that in place, interactive and batch runs share the same datasets. You can `vsub upload` input data, run in batch, and `vsub download` the results.

## 4. Verify

```bash
sas-viya batch jobs list                              # CLI auth + plug-in OK
vsub run examples/analysis.sas --up class.sas7bdat --down vsub.class_bmi -o out/
vsub batch submit examples/long_job.sas -f class.sas7bdat --wait
```

## Viya-side prerequisites (admin)

| Needed for | Requirement |
|---|---|
| Persistent `vsub` library | Home directories mounted (NFS/PVC) in the **compute** pod template |
| `libname vsub` in batch (`home_mounted = true`) | Home mounted in the **batch** pod template **and** allowed in the batch server's LOCKDOWN path list (`submit-pgm` always runs in lockdown mode) |
| `vsub batch submit` | Nothing extra: `submit-pgm` is allowed by default. (`submit-cmd` / `run-saslm` are disabled by default, but vsub doesn't use them.) |
| `vsub login` | The default `SASPy` OAuth client, which every Viya deployment includes |

## Troubleshooting

| Symptom | Fix |
|---|---|
| `... is a Git LFS pointer` | `git lfs install && git lfs pull` |
| `Not logged in to this Viya` / `Session expired` | `vsub login` |
| `The user token is expired. Login again ...` (sas-viya) | `sas-viya auth loginCode` |
| `The current user is not authorized to perform this command` | The command is disabled by authorization rules; ask the Viya admin |
| `ERROR: ... lockdown ...` when assigning `vsub` | The admin must add the path to the lockdown allowlist |
| `ERROR: Libref VSUB is not assigned.` in a batch log | `home_mounted = false`: use `jobdir.` + `-f`, or enable the `vsub` library for batch (see above) |
| `vsub batch submit --wait` exits 1/2 | The job's SAS log has warnings or errors; the log is in the results dir |
| `'sas-viya' not found` | Put `~/.local/bin` on PATH or set `[batch] cli` |

## Updating the bundled CLI

1. Download the new CLI archives for all three platforms from SAS.
2. Replace the files in `vendor/sas-viya-cli/`, keeping the original file names, e.g. `sas-viya-cli-<ver>-osx.zip`, `-linux-amd64.tgz` and `-windows.zip`.
3. Run `cd vendor/sas-viya-cli && shasum -a 256 *.zip *.tgz > SHA256SUMS`.
4. Update the version in the Windows snippet above and in `vendor/sas-viya-cli/README.md`.
5. Commit. LFS tracks the archives through `.gitattributes`.
