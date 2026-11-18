# vsub — remote-submit SAS code to SAS Viya (no shared storage)

`vsub` runs SAS programs on SAS Viya 4 from a machine that shares no filesystem
with Viya. It moves SAS datasets back and forth and brings the SAS log home.

| Need | How |
|---|---|
| Run SAS code, get log/listing back | `vsub run prog.sas` – interactive compute session via **saspy** (HTTP access method) |
| Keep datasets on Viya between runs | `vsub upload / download / ls / rm` – files live in `remote_root` (default `~/vsub_data`), visible to programs as library **`vsub`** |
| Long-running / fire-and-forget jobs | `vsub batch submit` / `vsub batch results` – thin wrapper over the official **`sas-viya batch`** CLI |

### Why not just `sas-viya batch`?
`sas-viya batch` is great for detached jobs and `vsub batch` deliberately stays thin
on top of it. What batch alone does *not* give you:

* **No persistent data channel.** Files only travel *with a job* (`--job-file`,
  intended for small files) and `get-results` deletes the file set afterwards.
  `vsub upload/download` put datasets into your Viya home directory and leave them there.
* **No quick interactive round-trip** – `run-saslm` is disabled by default;
  saspy talks to the Compute Service directly.
* **Lockdown** – `submit-pgm` runs in SAS LOCKDOWN mode; your home dir may not be
  reachable from batch unless the admin allows it.

What `vsub batch` adds on top: lower-cases dataset file names (SAS on Linux
ignores `Class.sas7bdat`), assigns `libname jobdir` to the job directory (and
optionally `vsub`), flattens the results folder, and maps the log to an exit code.
For `list`, `kill`, contexts etc. use `sas-viya` directly.

## Install

```bash
uv venv && uv pip install -e .          # or: pip install -e .
```

For the batch commands also install the [SAS Viya CLI](https://support.sas.com/downloads/package.htm?pid=2512)
with the batch plug-in.

## Configure & log in

```bash
vsub init                       # writes ~/.config/vsub/config.toml (override: $VSUB_CONFIG)
vsub login                      # SSO: open the printed URL, paste the code
vsub check-home                 # is HOME on Viya actually persistent?
sas-viya auth loginCode         # only needed for `vsub batch`
```

`vsub login` uses the same authorization-code + PKCE flow and the built-in
`SASPy` OAuth client that saspy uses, so no admin client registration is
needed. Tokens are cached in `~/.config/vsub/tokens.json` (mode 0600) and refreshed
automatically; when the refresh token expires, run `vsub login` again.

See the docstring of [`vsub/config.py`](vsub/config.py) for all config keys
(`verify = false` / `cafile` for self-signed certificates).

### Is my home directory persistent?
Only if your Viya admin mounted persistent storage (NFS/PVC) at the home path in
the **compute** pod template — and, for batch, in the **batch** pod template and
in the batch server's LOCKDOWN path list. Otherwise `$HOME` lives in the pod and
disappears with it. `vsub check-home` writes a marker in one compute session and
looks for it from a fresh one.

## Usage

```bash
# interactive: upload input, run, download output, write prog.log/prog.lst
vsub run analysis.sas --up data/class.sas7bdat --down vsub.class_bmi -o out/

# manage persistent datasets
vsub upload data/*.sas7bdat
vsub ls
vsub download class_bmi --to out/        # = vsub.class_bmi
vsub rm class_bmi -y

# `run --down` accepts any libref assigned by the program (e.g. work.tmp),
# because it downloads before the session ends; standalone `download` starts a
# fresh session, so only vsub (persistent) datasets make sense there.

# batch (detached)
vsub batch submit long_job.sas -f data/class.sas7bdat           # prints job id
vsub batch results <job-id> --to out/                           # log + files written to jobdir.
vsub batch submit long_job.sas -f data/class.sas7bdat --wait    # submit + wait + results
```

Example programs:

```sas
/* analysis.sas — interactive: vsub = remote_root */
data vsub.class_bmi;
  set vsub.class;
  bmi = weight / height**2 * 703;
run;
```

```sas
/* long_job.sas — batch: inputs and outputs live in jobdir */
data jobdir.class_bmi;
  set jobdir.class;
  bmi = weight / height**2 * 703;
run;
```

### Exit codes
`0` clean log · `1` warnings · `2` errors in SAS log · `3` vsub/config/connection error.

## Notes & limitations
* Datasets are moved as raw `.sas7bdat` (plus `.sas7bcat` format catalogs), so
  labels, formats and lengths are preserved. A dataset created on Windows/macOS
  SAS is read on Linux via CEDA (read-only, slower); rewrite it once on Viya
  (`data vsub.x; set vsub.x; run;`) if that matters.
* The interactive session uses the compute context's own lockdown settings; if
  `libname vsub` fails with a lockdown error, the admin must add the path.
* `vsub batch results` (like `sas-viya batch jobs get-results`) deletes the job
  and its file set on the server after downloading.
* Large data: `--job-file` is meant for small files; use `vsub upload` + a
  mounted home directory (`home_mounted = true`) for big inputs.

## Development

```bash
uv pip install -e '.[test]' && pytest
```
