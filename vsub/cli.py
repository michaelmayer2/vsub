"""vsub command-line interface.

Exit codes: 0 = clean log, 1 = warnings, 2 = errors in SAS log,
3 = vsub/connection/config error.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import typer
from rich.console import Console
from rich.markup import escape

from . import auth, batch
from .config import TEMPLATE, ConfigError, config_path, load_config
from .interactive import InteractiveSession, SASError, check_home, run_program
from .logs import LogSummary
from .transfer import InvalidName

app = typer.Typer(no_args_is_help=True, add_completion=False, help=__doc__)
batch_app = typer.Typer(no_args_is_help=True, help="Thin wrapper over `sas-viya batch` (detached jobs).")
app.add_typer(batch_app, name="batch")

out = Console()
err = Console(stderr=True)

EXIT_TOOL_ERROR = 3


def _fail(msg: str, log: str = "") -> None:
    err.print(f"[bold red]error:[/] {escape(msg)}")
    if log:
        err.print("[dim]--- SAS log (tail) ---[/]")
        err.print("\n".join(log.splitlines()[-30:]), markup=False, highlight=False)
    raise typer.Exit(EXIT_TOOL_ERROR)


def _cfg():
    try:
        return load_config()
    except ConfigError as e:
        _fail(str(e))


def _report(summary: LogSummary) -> None:
    for line in summary.errors[:20]:
        err.print(f"[red]{escape(line)}[/]", highlight=False)
    for line in summary.warnings[:20]:
        err.print(f"[yellow]{escape(line)}[/]", highlight=False)
    style = {0: "green", 1: "yellow", 2: "red"}[summary.exit_code]
    out.print(f"[{style}]log: {len(summary.errors)} error(s), {len(summary.warnings)} warning(s)[/]")


def _session(cfg):
    return InteractiveSession(cfg)


# --- setup ---------------------------------------------------------------------

@app.command()
def init(
    url: str = typer.Option(..., prompt="Viya URL (https://...)"),
    context: str = typer.Option("SAS Studio compute context", prompt="Compute context"),
    force: bool = typer.Option(False, help="Overwrite an existing config."),
):
    """Write a starter config file."""
    path = config_path()
    if path.exists() and not force:
        _fail(f"{path} exists (use --force to overwrite)")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(TEMPLATE.format(url=url.rstrip("/"), context=context))
    out.print(f"wrote {path}")


@app.command()
def login():
    """SSO login (authcode + PKCE) for the interactive backend; caches tokens."""
    cfg = _cfg()
    verifier, challenge = auth.new_pkce_pair()
    out.print("Open this URL in a browser, sign in, and paste the code shown:\n")
    out.print(auth.authorize_url(cfg.viya.url, challenge), soft_wrap=True, highlight=False)
    code = typer.prompt("\nAuthorization code", hide_input=True)
    try:
        tokens = auth.exchange_code(cfg.viya, code, verifier)
    except auth.AuthError as e:
        _fail(str(e))
    auth.save_tokens(tokens)
    out.print(f"[green]logged in[/] (tokens cached in {auth.tokens_path()})")
    if not tokens.refresh_token:
        err.print("[yellow]no refresh token issued - you will need to log in again when the token expires[/]")
    out.print("[dim]For `vsub batch`, also run: sas-viya auth loginCode[/]")


@app.command()
def logout():
    """Remove cached tokens."""
    auth.clear_tokens()
    out.print("tokens removed")


@app.command("check-home")
def check_home_cmd():
    """Check whether HOME on Viya persists across compute sessions."""
    cfg = _cfg()
    try:
        r = check_home(cfg)
    except (auth.AuthError, SASError) as e:
        _fail(str(e), getattr(e, "log", ""))
    out.print(f"HOME on Viya : {r.home}")
    out.print(f"remote_root  : {r.root}")
    out.print(f"pods         : {r.host1} -> {r.host2}")
    verdict = "[green]PERSISTENT[/]" if r.persistent else "[red]NOT PERSISTENT[/]"
    out.print(f"result       : {verdict} - {r.detail}")
    if not r.persistent:
        out.print(
            "Ask your Viya admin to mount persistent home directories into compute pods\n"
            "(and batch pods / lockdown paths if you use `vsub batch` with home_mounted=true)."
        )
        raise typer.Exit(1)


# --- interactive ---------------------------------------------------------------

@app.command()
def run(
    program: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True, help="SAS program to run."),
    up: List[Path] = typer.Option([], "--up", "-u", exists=True, dir_okay=False, help="Local .sas7bdat to upload to the vsub library first (repeatable)."),
    down: List[str] = typer.Option([], "--down", "-d", help="Dataset to download afterwards, e.g. vsub.out or work.tmp (repeatable)."),
    out_dir: Path = typer.Option(Path("."), "--out-dir", "-o", file_okay=False, help="Where downloaded datasets, .log and .lst go."),
    full_log: bool = typer.Option(False, help="Write the whole session log (incl. vsub setup steps) instead of just the program's log."),
):
    """Run a SAS program in an interactive compute session.

    Inside the program, `vsub` is a library pointing at remote_root (persistent
    if HOME is). Uploaded datasets appear as vsub.<name>.
    """
    cfg = _cfg()
    code = program.read_text()
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        with _session(cfg) as s:
            r = run_program(s, code, up, down, out_dir)
    except (auth.AuthError, SASError, InvalidName) as e:
        _fail(str(e), getattr(e, "log", ""))

    log_path = out_dir / f"{program.stem}.log"
    log_path.write_text(r.session_log if full_log else r.log)
    out.print(f"log      -> {log_path}")
    if r.lst.strip():
        lst_path = out_dir / f"{program.stem}.lst"
        lst_path.write_text(r.lst)
        out.print(f"listing  -> {lst_path}")
    for p in r.uploaded:
        out.print(f"uploaded -> {p}")
    for p in r.downloaded:
        out.print(f"download -> {p}")
    _report(r.summary)
    raise typer.Exit(r.summary.exit_code)


@app.command()
def upload(
    files: List[Path] = typer.Argument(..., exists=True, dir_okay=False, help="Local .sas7bdat/.sas7bcat files."),
):
    """Upload SAS datasets to remote_root on Viya (the `vsub` library)."""
    cfg = _cfg()
    try:
        with _session(cfg) as s:
            s.ensure_root()
            for f in files:
                out.print(f"{f} -> {s.upload_dataset(f)}")
    except (auth.AuthError, SASError, InvalidName) as e:
        _fail(str(e), getattr(e, "log", ""))


@app.command()
def download(
    datasets: List[str] = typer.Argument(..., help="Datasets: name (= vsub.name) or lib.name."),
    to: Path = typer.Option(Path("."), "--to", file_okay=False, help="Local target directory."),
):
    """Download SAS datasets from Viya as .sas7bdat files."""
    cfg = _cfg()
    try:
        with _session(cfg) as s:
            s.ensure_root()
            for ds in datasets:
                out.print(f"{ds} -> {s.download_dataset(ds, to)}")
    except (auth.AuthError, SASError, InvalidName) as e:
        _fail(str(e), getattr(e, "log", ""))


@app.command("ls")
def ls_cmd():
    """List files in remote_root on Viya."""
    cfg = _cfg()
    try:
        with _session(cfg) as s:
            root = s.ensure_root()
            items = s.listdir(root)
    except (auth.AuthError, SASError) as e:
        _fail(str(e), getattr(e, "log", ""))
    out.print(f"[bold]{root}[/]")
    for name in items:
        out.print(f"  {name}", highlight=False)


@app.command("rm")
def rm_cmd(
    datasets: List[str] = typer.Argument(..., help="Datasets in the vsub library to delete."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Don't ask for confirmation."),
):
    """Delete datasets from remote_root on Viya."""
    cfg = _cfg()
    if not yes:
        typer.confirm(f"Delete {', '.join(datasets)} on Viya?", abort=True)
    try:
        with _session(cfg) as s:
            s.ensure_root()
            for ds in datasets:
                s.delete_dataset(ds)
                out.print(f"deleted {ds}")
    except (auth.AuthError, SASError, InvalidName) as e:
        _fail(str(e), getattr(e, "log", ""))


# --- batch ---------------------------------------------------------------------

def _print_results(res: batch.BatchResults) -> None:
    out.print(f"results -> {res.directory}")
    for f in res.files:
        out.print(f"  {f.name}", highlight=False)
    if not res.logs:
        err.print("[yellow]no SAS log found in results[/]")
    _report(res.summary)


@batch_app.command("submit")
def batch_submit(
    program: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True),
    job_file: List[Path] = typer.Option([], "--job-file", "-f", exists=True, dir_okay=False, help="File to ship with the job (repeatable). Datasets appear as jobdir.<name>."),
    wait: bool = typer.Option(False, help="Wait for completion, then download results (deletes the job on the server)."),
    results_dir: Optional[Path] = typer.Option(None, "--results-dir", file_okay=False, help="With --wait: where to put results (default ./<job-id>)."),
    context: Optional[str] = typer.Option(None, help="Batch context (default from config)."),
    name: Optional[str] = typer.Option(None, help="Job name (default: program name)."),
):
    """Submit a SAS program as a detached batch job.

    Write outputs to `jobdir.` (the job directory) to get them back with
    `vsub batch results`.
    """
    cfg = _cfg()
    if not cfg.batch.home_mounted and batch.uses_vsub_lib(program.read_text()):
        err.print(
            "[yellow]warning:[/] program references the vsub library, but it is not assigned in batch "
            "([batch] home_mounted = false). Use jobdir. with --job-file, or see docs/SETUP.md."
        )
    try:
        job_id = batch.submit(cfg, program, job_file, wait=wait, job_name=name, context=context)
        out.print(f"[green]job id:[/] {job_id}")
        if not wait:
            out.print(f"[dim]collect later with: vsub batch results {job_id}[/]")
            return
        res = batch.fetch_results(cfg, job_id, results_dir or Path(job_id))
    except batch.BatchError as e:
        _fail(str(e))
    _print_results(res)
    raise typer.Exit(res.summary.exit_code)


@batch_app.command("results")
def batch_results(
    job_id: str = typer.Argument(...),
    to: Optional[Path] = typer.Option(None, "--to", file_okay=False, help="Target directory (default ./<job-id>)."),
):
    """Download log + outputs of a finished batch job (removes the job on the server)."""
    cfg = _cfg()
    try:
        res = batch.fetch_results(cfg, job_id, to or Path(job_id))
    except batch.BatchError as e:
        _fail(str(e))
    _print_results(res)
    raise typer.Exit(res.summary.exit_code)


if __name__ == "__main__":  # pragma: no cover
    app()
