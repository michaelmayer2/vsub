from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

from vsub.auth import Tokens
from vsub.config import parse_config


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    """Never touch the real ~/.config/vsub."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("VSUB_CONFIG", raising=False)
    monkeypatch.delenv("VSUB_URL", raising=False)


@pytest.fixture
def cfg():
    return parse_config({"viya": {"url": "https://viya.example.com", "context": "ctx"}})


class FakeSAS:
    """Stands in for saspy.SASsession; the 'remote' filesystem is a local dir."""

    def __init__(self, home: Path, host: str = "pod-1", program_log: str = "NOTE: ok\n"):
        self.home = home
        home.mkdir(parents=True, exist_ok=True)
        self.work = home.parent / f"work-{host}"
        self.work.mkdir(parents=True, exist_ok=True)
        self.host = host
        self.program_log = program_log
        self.macros: dict[str, str] = {}
        self.log = ""
        self.submitted: list[str] = []
        self.ended = False

    def _eval(self, expr: str) -> str:
        table = {
            "%sysget(HOME)": str(self.home),
            "%sysget(HOSTNAME)": self.host,
            "%sysfunc(pathname(work))": str(self.work),
            "%sysfunc(pathname(vsub))": self.macros.get("_libvsub", ""),
        }
        return table.get(expr, "")

    def submit(self, code: str, results: str = "TEXT") -> dict:
        self.submitted.append(code)
        log = ""
        m = re.fullmatch(r"%let (\w+)=(.*);", code.strip())
        if m:
            self.macros[m.group(1)] = self._eval(m.group(2))
        elif "dcreate" in code:
            root = re.search(r"root = '([^']*)';", code).group(1)
            Path(root).mkdir(parents=True, exist_ok=True)
            self.macros["_libvsub"] = root
        elif "file '" in code and "put '" in code:  # check-home marker write
            path, token = re.search(r"file '([^']*)'; put '([^']*)'", code).groups()
            Path(path).write_text(token + "\n")
        elif "infile '" in code:  # check-home marker read
            path = re.search(r"infile '([^']*)'", code).group(1)
            if Path(path).exists():
                self.macros["_vsubtok"] = Path(path).read_text().strip()
        elif "fdelete" in code:
            path = re.search(r"filename\('_vsubm', '([^']*)'\)", code).group(1)
            Path(path).unlink(missing_ok=True)
        elif "proc datasets" in code:
            mem = re.search(r"delete (\w+);", code).group(1)
            (Path(self.macros["_libvsub"]) / f"{mem}.sas7bdat").unlink(missing_ok=True)
        else:  # the user's program
            log = self.program_log
            out = self.work / "result.sas7bdat"
            out.write_bytes(b"RESULT")
        self.log += log
        return {"LOG": log, "LST": "listing" if log else ""}

    def symget(self, name, outtype=None):
        return self.macros.get(name)

    def upload(self, localfile, remotefile, overwrite=True):
        Path(remotefile).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(localfile, remotefile)
        return {"Success": True, "LOG": ""}

    def download(self, localfile, remotefile, overwrite=True):
        if not Path(remotefile).exists():
            return {"Success": False, "LOG": f"File {remotefile} does not exist."}
        shutil.copyfile(remotefile, localfile)
        return {"Success": True, "LOG": ""}

    def dirlist(self, path):
        p = Path(path)
        return [c.name for c in p.iterdir()] if p.is_dir() else None

    def saslog(self):
        return self.log

    def endsas(self):
        self.ended = True


@pytest.fixture
def tokens():
    return Tokens("https://viya.example.com", "access", "refresh")
