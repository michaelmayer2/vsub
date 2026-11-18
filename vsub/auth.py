"""SSO token handling for the interactive (saspy) backend.

Mirrors saspy's own authcode flow (PKCE, public ``SASPy`` client that Viya
ships by default) but caches the resulting tokens so a fresh authcode is only
needed when the refresh token expires.

Tokens are cached in ``~/.config/vsub/tokens.json`` with 0600 permissions and
are never written into the saspy config file - they are passed to
``SASsession(authtoken=..., refreshtoken=...)`` at runtime.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path

from .config import ViyaConfig, config_dir

CLIENT_ID = "SASPy"
# Refresh a little before the access token actually expires.
EXPIRY_SKEW = 120


class AuthError(Exception):
    pass


@dataclass
class Tokens:
    url: str
    access_token: str
    refresh_token: str | None = None

    def access_expires_at(self) -> float | None:
        return jwt_exp(self.access_token)

    def access_valid(self, now: float | None = None) -> bool:
        exp = self.access_expires_at()
        now = time.time() if now is None else now
        return exp is None or exp - EXPIRY_SKEW > now


def tokens_path() -> Path:
    return config_dir() / "tokens.json"


def jwt_exp(token: str) -> float | None:
    """Return the ``exp`` claim of a JWT without verifying it (local use only)."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return float(json.loads(base64.urlsafe_b64decode(payload))["exp"])
    except Exception:
        return None


# --- PKCE --------------------------------------------------------------------

def new_pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) using S256, as saspy does."""
    verifier = secrets.token_urlsafe(32)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


def authorize_url(base_url: str, challenge: str) -> str:
    q = urllib.parse.urlencode(
        {
            "client_id": CLIENT_ID,
            "response_type": "code",
            "code_challenge_method": "S256",
            "code_challenge": challenge,
        }
    )
    return f"{base_url}/SASLogon/oauth/authorize?{q}"


# --- HTTP --------------------------------------------------------------------

def _ssl_context(viya: ViyaConfig) -> ssl.SSLContext | None:
    if not viya.url.startswith("https://"):
        return None
    if not viya.verify:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    return ssl.create_default_context(cafile=viya.cafile) if viya.cafile else ssl.create_default_context()


def _post_token(viya: ViyaConfig, form: dict, headers: dict | None = None) -> dict:
    data = urllib.parse.urlencode(form).encode()
    req = urllib.request.Request(
        f"{viya.url}/SASLogon/oauth/token",
        data=data,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json", **(headers or {})},
    )
    try:
        with urllib.request.urlopen(req, context=_ssl_context(viya), timeout=60) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:500]
        raise AuthError(f"SASLogon token request failed: HTTP {e.code}: {body}") from None
    except urllib.error.URLError as e:
        raise AuthError(f"Could not reach SASLogon at {viya.url}: {e.reason}") from None


def exchange_code(viya: ViyaConfig, code: str, verifier: str) -> Tokens:
    js = _post_token(
        viya,
        {
            "grant_type": "authorization_code",
            "code": code.strip(),
            "code_verifier": verifier,
            "client_id": CLIENT_ID,
            "client_secret": "",
        },
    )
    if "access_token" not in js:
        raise AuthError(f"No access_token in SASLogon response: {list(js)}")
    return Tokens(viya.url, js["access_token"], js.get("refresh_token"))


def refresh(viya: ViyaConfig, tokens: Tokens) -> Tokens:
    if not tokens.refresh_token:
        raise AuthError("No refresh token cached; run `vsub login`.")
    basic = base64.b64encode(f"{CLIENT_ID}:".encode()).decode()
    js = _post_token(
        viya,
        {"grant_type": "refresh_token", "refresh_token": tokens.refresh_token},
        headers={"Authorization": f"Basic {basic}"},
    )
    if "access_token" not in js:
        raise AuthError("Token refresh returned no access_token; run `vsub login`.")
    return Tokens(viya.url, js["access_token"], js.get("refresh_token") or tokens.refresh_token)


# --- cache -------------------------------------------------------------------

def save_tokens(tokens: Tokens, path: Path | None = None) -> None:
    path = path or tokens_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(asdict(tokens), fh)
    os.chmod(path, 0o600)


def load_tokens(path: Path | None = None) -> Tokens | None:
    path = path or tokens_path()
    try:
        with open(path) as fh:
            return Tokens(**json.load(fh))
    except (FileNotFoundError, json.JSONDecodeError, TypeError):
        return None


def clear_tokens(path: Path | None = None) -> None:
    (path or tokens_path()).unlink(missing_ok=True)


def get_tokens(viya: ViyaConfig) -> Tokens:
    """Return a valid token pair, refreshing if needed. Raises AuthError."""
    tokens = load_tokens()
    if tokens is None or tokens.url != viya.url:
        raise AuthError("Not logged in to this Viya; run `vsub login`.")
    if tokens.access_valid():
        return tokens
    try:
        tokens = refresh(viya, tokens)
    except AuthError as e:
        raise AuthError(f"Session expired ({e}). Run `vsub login`.") from None
    save_tokens(tokens)
    return tokens
