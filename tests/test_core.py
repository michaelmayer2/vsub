import base64
import json
import os
import stat
import time

import pytest

from vsub import auth
from vsub.config import ConfigError, load_config, parse_config
from vsub.logs import scan_log
from vsub.transfer import InvalidName, parse_ds_ref, resolve_remote_root, sas_quote, split_sas_file


# --- logs ---------------------------------------------------------------------

def test_scan_log_counts_errors_and_warnings():
    log = "\n".join([
        "1    data x; set y; run;",
        "ERROR: File WORK.Y.DATA does not exist.",
        "ERROR 22-322: Syntax error.",
        "WARNING: The data set WORK.X may be incomplete.",
        "WARNING 1-322: Assuming the symbol DATA was misspelled.",
        "12   put 'ERROR: not a real error';",
        "NOTE: done",
    ])
    s = scan_log(log)
    assert len(s.errors) == 2 and len(s.warnings) == 2
    assert s.exit_code == 2


def test_scan_log_exit_codes():
    assert scan_log("NOTE: fine").exit_code == 0
    assert scan_log("WARNING: hmm").exit_code == 1


# --- transfer -----------------------------------------------------------------

def test_split_sas_file_lowercases():
    assert split_sas_file("/x/Class.SAS7BDAT") == ("class", ".sas7bdat")
    assert split_sas_file("formats.sas7bcat") == ("formats", ".sas7bcat")


@pytest.mark.parametrize("bad", ["1abc.sas7bdat", "my-data.sas7bdat", "x.csv", ("a" * 33) + ".sas7bdat"])
def test_split_sas_file_rejects(bad):
    with pytest.raises(InvalidName):
        split_sas_file(bad)


def test_parse_ds_ref():
    assert parse_ds_ref("Class") == ("vsub", "class")
    assert parse_ds_ref("WORK.Tmp") == ("work", "tmp")
    for bad in ["a.b.c", "toolonglib.x", "lib.1x"]:
        with pytest.raises(InvalidName):
            parse_ds_ref(bad)


def test_resolve_remote_root():
    assert resolve_remote_root("~/vsub_data", "/home/u/") == "/home/u/vsub_data"
    assert resolve_remote_root("~", "/home/u") == "/home/u"
    assert resolve_remote_root("/data/x", "/home/u") == "/data/x"


def test_sas_quote():
    assert sas_quote("/a/o'b") == "'/a/o''b'"
    with pytest.raises(InvalidName):
        sas_quote("a\nb")


# --- config -------------------------------------------------------------------

def test_parse_config_defaults():
    c = parse_config({"viya": {"url": "https://v.example.com/"}})
    assert c.viya.url == "https://v.example.com"
    assert c.viya.context is None
    assert c.storage.remote_root == "~/vsub_data"
    assert c.batch.context == "default" and c.batch.home_mounted is False


@pytest.mark.parametrize("data", [
    {},
    {"viya": {"url": "viya.example.com"}},
    {"viya": {"url": "https://v"}, "storage": {"remote_root": "relative/path"}},
    {"viya": {"url": "https://v"}, "storage": {"remote_root": "/a'b"}},
])
def test_parse_config_errors(data):
    with pytest.raises(ConfigError):
        parse_config(data)


def test_load_config_file(tmp_path, monkeypatch):
    p = tmp_path / "c.toml"
    p.write_text('[viya]\nurl = "https://v.example.com"\ncontext = ""\n[batch]\nhome_mounted = true\n')
    monkeypatch.setenv("VSUB_CONFIG", str(p))
    c = load_config()
    assert c.viya.context is None and c.batch.home_mounted


def test_load_config_env_only(monkeypatch):
    monkeypatch.setenv("VSUB_URL", "https://env.example.com")
    assert load_config().viya.url == "https://env.example.com"


# --- auth ---------------------------------------------------------------------

def _jwt(exp: float) -> str:
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    return f"{enc({'alg': 'none'})}.{enc({'exp': exp})}.sig"


def test_pkce_pair_and_url():
    verifier, challenge = auth.new_pkce_pair()
    import hashlib
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    assert challenge == expected
    url = auth.authorize_url("https://v.example.com", challenge)
    assert url.startswith("https://v.example.com/SASLogon/oauth/authorize?client_id=SASPy&response_type=code")
    assert "code_challenge_method=S256" in url


def test_jwt_exp_and_validity():
    t = auth.Tokens("u", _jwt(time.time() + 3600))
    assert t.access_valid()
    assert not auth.Tokens("u", _jwt(time.time() + 10)).access_valid()
    assert auth.jwt_exp("not-a-jwt") is None


def test_token_cache_permissions(tokens):
    auth.save_tokens(tokens)
    mode = stat.S_IMODE(os.stat(auth.tokens_path()).st_mode)
    assert mode == 0o600
    assert auth.load_tokens() == tokens


def test_get_tokens_refreshes_expired(cfg, monkeypatch):
    auth.save_tokens(auth.Tokens(cfg.viya.url, _jwt(time.time() - 10), "r1"))
    fresh = _jwt(time.time() + 3600)
    calls = []

    def fake_post(viya, form, headers=None):
        calls.append((form, headers))
        return {"access_token": fresh, "refresh_token": "r2"}

    monkeypatch.setattr(auth, "_post_token", fake_post)
    t = auth.get_tokens(cfg.viya)
    assert t.access_token == fresh and t.refresh_token == "r2"
    assert calls[0][0] == {"grant_type": "refresh_token", "refresh_token": "r1"}
    assert calls[0][1]["Authorization"] == "Basic " + base64.b64encode(b"SASPy:").decode()
    assert auth.load_tokens().refresh_token == "r2"


def test_get_tokens_requires_login_for_other_url(cfg):
    auth.save_tokens(auth.Tokens("https://other.example.com", "a", "r"))
    with pytest.raises(auth.AuthError, match="vsub login"):
        auth.get_tokens(cfg.viya)


def test_exchange_code_sends_pkce_verifier(cfg, monkeypatch):
    seen = {}
    monkeypatch.setattr(auth, "_post_token", lambda v, form, headers=None: seen.update(form) or {"access_token": "a"})
    t = auth.exchange_code(cfg.viya, " code123 ", "verif")
    assert seen["code"] == "code123" and seen["code_verifier"] == "verif" and seen["client_id"] == "SASPy"
    assert t.refresh_token is None
