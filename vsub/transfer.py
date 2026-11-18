"""Dataset/file naming rules and SAS string quoting helpers."""

from __future__ import annotations

import re
from pathlib import Path

# SAS member files that make sense to move between machines.
SAS_FILE_EXTS = (".sas7bdat", ".sas7bcat", ".sas7bndx")

_SAS_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,31}$")
_LIBREF = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,7}$")

DEFAULT_LIBREF = "vsub"


class InvalidName(ValueError):
    pass


def sas_quote(value: str) -> str:
    """Return a single-quoted SAS string literal (no macro resolution)."""
    if any(c in value for c in "\n\r"):
        raise InvalidName(f"newline not allowed in SAS path: {value!r}")
    return "'" + value.replace("'", "''") + "'"


def split_sas_file(path: str | Path) -> tuple[str, str]:
    """Split a local SAS file path into (member, ext), member lower-cased.

    SAS on Linux only recognises lower-case member file names, so a file
    called ``Class.sas7bdat`` must land on the server as ``class.sas7bdat``.
    """
    p = Path(path)
    ext = p.suffix.lower()
    if ext not in SAS_FILE_EXTS:
        raise InvalidName(f"{p.name}: expected one of {', '.join(SAS_FILE_EXTS)}")
    member = p.stem
    if not _SAS_NAME.match(member):
        raise InvalidName(
            f"{p.name}: '{member}' is not a valid SAS member name "
            "(letters, digits, underscore; max 32 chars; not starting with a digit)"
        )
    return member.lower(), ext


def parse_ds_ref(ref: str, default_lib: str = DEFAULT_LIBREF) -> tuple[str, str]:
    """Parse ``lib.member`` or ``member`` into (libref, member), lower-cased."""
    parts = ref.split(".")
    if len(parts) == 1:
        lib, mem = default_lib, parts[0]
    elif len(parts) == 2:
        lib, mem = parts
    else:
        raise InvalidName(f"invalid dataset reference: {ref!r}")
    if not _LIBREF.match(lib):
        raise InvalidName(f"invalid libref: {lib!r}")
    if not _SAS_NAME.match(mem):
        raise InvalidName(f"invalid member name: {mem!r}")
    return lib.lower(), mem.lower()


def resolve_remote_root(remote_root: str, home: str) -> str:
    """Expand a leading ``~`` in the configured remote root against HOME."""
    home = home.rstrip("/") or "/"
    if remote_root == "~":
        return home
    if remote_root.startswith("~/"):
        return f"{home}/{remote_root[2:]}".replace("//", "/")
    return remote_root
