"""Thin convenience layer over the official ``sas-viya batch`` CLI.

Only adds what the CLI doesn't do itself:
  * stages ``.sas7bdat`` job files under lower-case names (SAS on Linux
    ignores member files with upper-case names),
  * prepends a preamble assigning ``libname jobdir`` (the job directory) and,
    optionally, ``libname vsub`` (persistent HOME storage),
  * flattens ``get-results`` output and maps the log to an exit code.

Everything else (``list``, ``kill``, ``auth loginCode`` ...) - use ``sas-viya``
directly.

Reference: "Using the batch Plug-In for the SAS Viya Platform CLI".
Note that ``get-results`` downloads the file set and then DELETES the job and
its file set.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from .config import Config
from .logs import LogSummary, scan_log
from .transfer import SAS_FILE_EXTS, InvalidName, split_sas_file

_JOB_ID = re.compile(r'\bID[:=]\s*"([0-9a-fA-F-]{36})"')


class BatchError(Exception):
    pass


def base_cmd(cfg: Config) -> list[str]:
    cmd = [cfg.batch.cli]
    if cfg.batch.profile:
        cmd += ["--profile", cfg.batch.profile]
    return cmd


def preamble(cfg: Config) -> str:
    lines = [
        "/* --- vsub preamble --- */",
        'libname jobdir "%sysget(BATCHJOBDIR)";',
    ]
    if cfg.batch.home_mounted:
        root = cfg.storage.remote_root
        if root == "~":
            path = "%sysget(HOME)"
        elif root.startswith("~/"):
            path = "%sysget(HOME)/" + root[2:]
        else:
            path = root
        lines.append(f'libname vsub "{path}";')
    lines.append("/* --- end vsub preamble --- */")
    return "\n".join(lines) + "\n"


def stage_job_files(files: Iterable[Path], staging: Path) -> list[Path]:
    """Copy job files into ``staging``; SAS member files get lower-case names."""
    staged: list[Path] = []
    seen: set[str] = set()
    for f in files:
        if not f.is_file():
            raise BatchError(f"job file not found: {f}")
        if f.suffix.lower() in SAS_FILE_EXTS:
            try:
                member, ext = split_sas_file(f)
            except InvalidName as e:
                raise BatchError(str(e)) from None
            name = member + ext
        else:
            name = f.name
        if name in seen:
            raise BatchError(f"two job files map to the same name: {name}")
        seen.add(name)
        dest = staging / name
        shutil.copy2(f, dest)
        staged.append(dest)
    return staged


def build_submit_cmd(
    cfg: Config, pgm: Path, job_files: list[Path], job_name: str | None, wait: bool, context: str | None = None
) -> list[str]:
    cmd = base_cmd(cfg) + [
        "batch", "jobs", "submit-pgm",
        "--pgm-path", str(pgm),
        "--context", context or cfg.batch.context,
    ]
    if job_name:
        cmd += ["--job-name", job_name]
    for f in job_files:
        cmd += ["--job-file", str(f)]
    if wait:
        cmd.append("--wait")
    return cmd


def parse_job_id(output: str) -> str | None:
    m = _JOB_ID.search(output)
    return m.group(1) if m else None


def _run_echo(cmd: list[str]) -> tuple[int, str]:
    """Run ``cmd``, echoing its output live, and return (rc, captured output)."""
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    except FileNotFoundError:
        raise BatchError(f"'{cmd[0]}' not found - install the SAS Viya CLI or set [batch] cli") from None
    out = []
    assert proc.stdout is not None
    for line in proc.stdout:
        sys.stdout.write(line)
        out.append(line)
    return proc.wait(), "".join(out)


Runner = Callable[[list[str]], tuple[int, str]]


def submit(
    cfg: Config,
    program: Path,
    job_files: list[Path],
    wait: bool = False,
    job_name: str | None = None,
    context: str | None = None,
    runner: Runner = _run_echo,
) -> str:
    """Submit ``program`` with the vsub preamble; return the batch job id."""
    code = program.read_text()
    with tempfile.TemporaryDirectory(prefix="vsub-batch-") as tmp:
        tmpdir = Path(tmp)
        files_dir = tmpdir / "files"
        files_dir.mkdir()
        staged = stage_job_files(job_files, files_dir)
        # keep the user's program name so the log is named <prog>.log
        pgm = tmpdir / program.name
        pgm.write_text(preamble(cfg) + code)
        rc, out = runner(build_submit_cmd(cfg, pgm, staged, job_name or program.stem, wait, context))
    job_id = parse_job_id(out)
    if rc != 0 or not job_id:
        raise BatchError(f"sas-viya batch submit failed (rc={rc})")
    return job_id


@dataclass
class BatchResults:
    directory: Path
    files: list[Path]
    logs: list[Path]
    summary: LogSummary


def _flatten(results_dir: Path) -> None:
    """get-results writes into <dir>/<fileset-id>/ - move files up one level."""
    for sub in [p for p in results_dir.iterdir() if p.is_dir() and p.name.startswith("JOB_")]:
        for f in sub.iterdir():
            target = results_dir / f.name
            if target.exists():
                if target.is_dir():
                    shutil.rmtree(target)
                else:
                    target.unlink()
            shutil.move(str(f), target)
        sub.rmdir()


def fetch_results(cfg: Config, job_id: str, to_dir: Path, runner: Runner = _run_echo) -> BatchResults:
    created = not to_dir.exists()
    to_dir.mkdir(parents=True, exist_ok=True)
    cmd = base_cmd(cfg) + ["batch", "jobs", "get-results", "--job-id", job_id, "--results-dir", str(to_dir)]
    try:
        rc, _ = runner(cmd)
        if rc != 0:
            raise BatchError(f"sas-viya batch get-results failed (rc={rc}); is the job finished?")
    except BatchError:
        if created and not any(to_dir.iterdir()):
            to_dir.rmdir()
        raise
    _flatten(to_dir)
    files = sorted(p for p in to_dir.iterdir() if p.is_file())
    # SASBatch.*.log / redir.*.log are batch-server debug logs, not the SAS log
    logs = [p for p in files if p.suffix == ".log" and not p.name.startswith(("SASBatch.", "redir."))]
    summary = LogSummary()
    for log in logs:
        summary = summary.merge(scan_log(log.read_text(errors="replace")))
    return BatchResults(to_dir, files, logs, summary)
