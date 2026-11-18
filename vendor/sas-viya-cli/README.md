# SAS Viya CLI (vendored)

SAS Viya Platform command-line interface **1.39.14**, as downloaded from SAS. Each archive contains only the `sas-viya` / `sas-viya.exe` binary.

| File | Platform |
|---|---|
| `sas-viya-cli-1.39.14-osx.zip` | macOS (x86_64; needs Rosetta 2 on Apple Silicon) |
| `sas-viya-cli-1.39.14-linux-amd64.tgz` | Linux x86_64 |
| `sas-viya-cli-1.39.14-windows.zip` | Windows x86_64 |

- The archives are stored with **Git LFS**. Run `git lfs pull` after cloning.
- Checksums are in `SHA256SUMS`.
- The batch plug-in is **not** included. It's installed from SAS's plug-in repository by `scripts/setup-sas-viya.sh`.

This is proprietary SAS software, subject to SAS's license terms. Keep this repository private, or confirm redistribution rights before sharing it.

Install and update instructions are in [docs/SETUP.md](../../docs/SETUP.md).
