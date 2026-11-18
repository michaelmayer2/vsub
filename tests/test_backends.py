from pathlib import Path

import pytest
from typer.testing import CliRunner

from vsub import batch, cli
from vsub.config import parse_config
from vsub.interactive import InteractiveSession, SASError, check_home, run_program, write_saspy_cfg

from .conftest import FakeSAS


# --- interactive --------------------------------------------------------------

def _session(cfg, tokens, fake):
    return InteractiveSession(cfg, factory=lambda c, t: fake, tokens=tokens)


def test_run_program_round_trip(cfg, tokens, tmp_path):
    home = tmp_path / "home"
    fake = FakeSAS(home)
    local = tmp_path / "Class.sas7bdat"
    local.write_bytes(b"CLASS")
    out = tmp_path / "out"
    out.mkdir()

    with _session(cfg, tokens, fake) as s:
        r = run_program(s, "data vsub.x; set vsub.class; run;", [local], ["work.result"], out)

    assert (home / "vsub_data" / "class.sas7bdat").read_bytes() == b"CLASS"  # lower-cased, under HOME
    assert r.uploaded == [str(home / "vsub_data" / "class.sas7bdat")]
    assert r.downloaded == [out / "result.sas7bdat"] and (out / "result.sas7bdat").read_bytes() == b"RESULT"
    assert r.summary.exit_code == 0
    assert fake.ended
    assert any("libname vsub '" + str(home / "vsub_data") + "';" in c for c in fake.submitted)


def test_run_program_missing_download_is_reported(cfg, tokens, tmp_path):
    fake = FakeSAS(tmp_path / "home")
    with _session(cfg, tokens, fake) as s:
        r = run_program(s, "proc print; run;", [], ["vsub.nope"], tmp_path)
    assert r.summary.exit_code == 2
    assert "vsub.nope" in r.summary.errors[0] or "nope" in r.summary.errors[0]


def test_submit_raises_on_setup_error(cfg, tokens, tmp_path):
    fake = FakeSAS(tmp_path / "home")
    fake.submit = lambda code, results="TEXT": {"LOG": "ERROR: Path not in lockdown list.", "LST": ""}
    with _session(cfg, tokens, fake) as s:
        with pytest.raises(SASError, match="lockdown"):
            s.ensure_root()


def test_upload_ls_rm(cfg, tokens, tmp_path):
    fake = FakeSAS(tmp_path / "home")
    f = tmp_path / "a.sas7bdat"
    f.write_bytes(b"A")
    with _session(cfg, tokens, fake) as s:
        root = s.ensure_root()
        s.upload_dataset(f)
        assert s.listdir(root) == ["a.sas7bdat"]
        s.delete_dataset("a")
        assert s.listdir(root) == []


def test_check_home_persistent(cfg, tokens, tmp_path, monkeypatch):
    monkeypatch.setattr("vsub.auth.get_tokens", lambda viya: tokens)
    home = tmp_path / "home"
    fakes = iter([FakeSAS(home, "pod-1"), FakeSAS(home, "pod-2")])
    r = check_home(cfg, factory=lambda c, t: next(fakes))
    assert r.persistent and (r.host1, r.host2) == ("pod-1", "pod-2")
    assert not (home / ".vsub_persist_check").exists()  # marker cleaned up


def test_check_home_ephemeral(cfg, tokens, tmp_path, monkeypatch):
    monkeypatch.setattr("vsub.auth.get_tokens", lambda viya: tokens)
    home = tmp_path / "home"
    calls = []

    def factory(c, t):
        calls.append(t)
        if len(calls) == 2:  # new pod: the pod-local HOME from session 1 is gone
            (home / ".vsub_persist_check").unlink()
        return FakeSAS(home, f"pod-{len(calls)}")

    r = check_home(cfg, factory=factory)
    assert not r.persistent and "ephemeral" in r.detail


def test_saspy_cfg_has_no_secrets(cfg):
    text = write_saspy_cfg(cfg).read_text()
    assert "'url': 'https://viya.example.com'" in text and "'context': 'ctx'" in text
    assert "token" not in text.lower()


# --- batch --------------------------------------------------------------------

def test_preamble_home_mounted():
    c = parse_config({"viya": {"url": "https://v"}, "storage": {"remote_root": "/home/u/vsub_data"},
                      "batch": {"home_mounted": True}})
    p = batch.preamble(c)
    assert 'libname jobdir "%sysget(BATCHJOBDIR)";' in p
    assert "libname vsub '/home/u/vsub_data';" in p
    assert "libname vsub" not in batch.preamble(parse_config({"viya": {"url": "https://v"}}))


def test_preamble_home_mounted_rejects_tilde_root():
    # batch pods have HOME=/ so "~" cannot be resolved there
    c = parse_config({"viya": {"url": "https://v"}, "batch": {"home_mounted": True}})
    with pytest.raises(batch.BatchError, match="absolute"):
        batch.preamble(c)


@pytest.mark.parametrize("code,expected", [
    ("data vsub.out; set sashelp.class; run;", True),
    ("proc print data=VSUB . class; run;", True),
    ("data jobdir.out; x = 1; run; /* vsubtle */", False),
])
def test_uses_vsub_lib(code, expected):
    assert batch.uses_vsub_lib(code) is expected


def test_stage_job_files(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "Class.sas7bdat").write_bytes(b"x")
    (src / "Input.CSV").write_text("a,b")
    staging = tmp_path / "stage"
    staging.mkdir()
    staged = batch.stage_job_files([src / "Class.sas7bdat", src / "Input.CSV"], staging)
    assert [p.name for p in staged] == ["class.sas7bdat", "Input.CSV"]
    with pytest.raises(batch.BatchError, match="same name"):
        (src / "class.sas7bdat").write_bytes(b"y")
        (tmp_path / "stage2").mkdir()
        batch.stage_job_files([src / "Class.sas7bdat", src / "class.sas7bdat"], tmp_path / "stage2")


def test_build_submit_cmd():
    c = parse_config({"viya": {"url": "https://v"}, "batch": {"profile": "prod", "context": "bctx"}})
    cmd = batch.build_submit_cmd(c, Path("p.sas"), [Path("a"), Path("b")], "job", wait=True)
    assert cmd == ["sas-viya", "--profile", "prod", "batch", "jobs", "submit-pgm", "--pgm-path", "p.sas",
                   "--context", "bctx", "--job-name", "job", "--job-file", "a", "--job-file", "b", "--wait"]


@pytest.mark.parametrize("text", [
    '>>> Job submitted, ID="40f55940-f387-4a17-a1fd-1aaf2a960b19", Workload Job ID="613".',
    '>>> The job was submitted. ID: "40f55940-f387-4a17-a1fd-1aaf2a960b19"  Workload Orchestrator job ID: "5"',
])
def test_parse_job_id(text):
    assert batch.parse_job_id(text) == "40f55940-f387-4a17-a1fd-1aaf2a960b19"


def test_submit_writes_preamble_and_uses_program_name(cfg, tmp_path):
    prog = tmp_path / "myjob.sas"
    prog.write_text("data jobdir.out; x=1; run;")
    seen = {}

    def runner(cmd):
        pgm = Path(cmd[cmd.index("--pgm-path") + 1])
        seen["name"] = pgm.name
        seen["code"] = pgm.read_text()
        seen["cmd"] = cmd
        return 0, '>>> Job submitted, ID="11111111-2222-3333-4444-555555555555"'

    job = batch.submit(cfg, prog, [], runner=runner)
    assert job == "11111111-2222-3333-4444-555555555555"
    assert seen["name"] == "myjob.sas"
    assert seen["code"].startswith("/* --- vsub preamble") and seen["code"].endswith("data jobdir.out; x=1; run;")
    assert "--wait" not in seen["cmd"]


def test_submit_wait_nonzero_job_rc_still_returns_id(cfg, tmp_path):
    # `submit-pgm --wait` exits with the job's return code (2 = SAS errors)
    prog = tmp_path / "p.sas"
    prog.write_text("x")
    out = '>>> The job was submitted. ID: "6819f2e7-286a-4f28-8be1-493b6e8c8379"  Workload Orchestrator job ID: "3071"\n<<< Job ended.'
    assert batch.submit(cfg, prog, [], wait=True, runner=lambda cmd: (2, out)) == "6819f2e7-286a-4f28-8be1-493b6e8c8379"


def test_submit_failure(cfg, tmp_path):
    prog = tmp_path / "p.sas"
    prog.write_text("x")
    with pytest.raises(batch.BatchError):
        batch.submit(cfg, prog, [], runner=lambda cmd: (1, "The current user is not authorized"))


def test_fetch_results_flattens_and_scans(cfg, tmp_path):
    def runner(cmd):
        d = Path(cmd[cmd.index("--results-dir") + 1]) / "JOB_20260101_000000_1"
        d.mkdir(parents=True)
        (d / "myjob.log").write_text("NOTE: hi\nWARNING: careful\n")
        (d / "SASBatch.u.x.log").write_text("ERROR: debug noise\n")
        (d / "out.sas7bdat").write_bytes(b"o")
        return 0, ""

    res = batch.fetch_results(cfg, "jid", tmp_path / "res", runner=runner)
    assert sorted(p.name for p in res.files) == ["SASBatch.u.x.log", "myjob.log", "out.sas7bdat"]
    assert [p.name for p in res.logs] == ["myjob.log"]
    assert res.summary.exit_code == 1


# --- CLI ----------------------------------------------------------------------

def test_cli_run_exit_code_and_files(tmp_path, monkeypatch, tokens):
    cfgfile = tmp_path / "c.toml"
    cfgfile.write_text('[viya]\nurl = "https://viya.example.com"\ncontext = "ctx"\n')
    monkeypatch.setenv("VSUB_CONFIG", str(cfgfile))
    fake = FakeSAS(tmp_path / "home", program_log="ERROR: boom\n")
    monkeypatch.setattr(cli, "_session", lambda c: _session(c, tokens, fake))
    prog = tmp_path / "bad.sas"
    prog.write_text("data;")
    out = tmp_path / "out"

    res = CliRunner().invoke(cli.app, ["run", str(prog), "-o", str(out)])
    assert res.exit_code == 2, res.output
    assert (out / "bad.log").read_text() == "ERROR: boom\n"
    assert (out / "bad.lst").exists()


def test_cli_missing_config(tmp_path, monkeypatch):
    monkeypatch.setenv("VSUB_CONFIG", str(tmp_path / "none.toml"))
    prog = tmp_path / "p.sas"
    prog.write_text("x")
    res = CliRunner().invoke(cli.app, ["run", str(prog)])
    assert res.exit_code == 3
