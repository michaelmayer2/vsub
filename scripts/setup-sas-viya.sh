#!/usr/bin/env bash
# Install the SAS Viya CLI from vendor/sas-viya-cli (Git LFS), install the
# batch plug-in, create a CLI profile and log in. macOS and Linux (x86_64).
# Windows: see docs/SETUP.md.
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/setup-sas-viya.sh [options]

  --url URL        Viya endpoint, e.g. https://viya.example.com
                   (default: $VSUB_URL, else url from ~/.config/vsub/config.toml, else prompt)
  --prefix DIR     Where to install the sas-viya binary (default: ~/.local/bin)
  --profile NAME   sas-viya profile to configure (default: Default)
  --skip-install   Don't (re)install the binary, use the one on PATH/prefix
  --skip-plugin    Don't install the batch plug-in
  --skip-profile   Don't touch the CLI profile
  --skip-login     Don't run 'sas-viya auth loginCode'
  -h, --help       Show this help
EOF
}

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENDOR="$REPO_ROOT/vendor/sas-viya-cli"
PREFIX="$HOME/.local/bin"
URL="${VSUB_URL:-}"
PROFILE=""
DO_INSTALL=1 DO_PLUGIN=1 DO_PROFILE=1 DO_LOGIN=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --url) URL="$2"; shift 2 ;;
    --prefix) PREFIX="$2"; shift 2 ;;
    --profile) PROFILE="$2"; shift 2 ;;
    --skip-install) DO_INSTALL=0; shift ;;
    --skip-plugin) DO_PLUGIN=0; shift ;;
    --skip-profile) DO_PROFILE=0; shift ;;
    --skip-login) DO_LOGIN=0; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

say()  { printf '\033[1m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[33mwarning:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }

sha256() { if command -v sha256sum >/dev/null; then sha256sum "$1"; else shasum -a 256 "$1"; fi | awk '{print $1}'; }

# --- 1. install the binary ------------------------------------------------------
install_cli() {
  local os arch archive tmp
  os="$(uname -s)"; arch="$(uname -m)"
  case "$os" in
    Darwin) archive="$(ls "$VENDOR"/sas-viya-cli-*-osx.zip 2>/dev/null | tail -1)" ;;
    Linux)
      [[ "$arch" == "x86_64" ]] || die "no SAS Viya CLI build for Linux/$arch (only x86_64)"
      archive="$(ls "$VENDOR"/sas-viya-cli-*-linux-amd64.tgz 2>/dev/null | tail -1)" ;;
    *) die "unsupported OS $os - see docs/SETUP.md for Windows" ;;
  esac
  [[ -n "$archive" ]] || die "no CLI archive for $os in $VENDOR"

  if head -c 40 "$archive" | grep -q "git-lfs"; then
    die "$(basename "$archive") is a Git LFS pointer. Run: git lfs install && git lfs pull"
  fi

  local expected actual
  expected="$(awk -v f="$(basename "$archive")" '$2 == f {print $1}' "$VENDOR/SHA256SUMS")"
  actual="$(sha256 "$archive")"
  [[ -n "$expected" && "$expected" == "$actual" ]] || die "checksum mismatch for $(basename "$archive")"

  say "Installing $(basename "$archive") -> $PREFIX/sas-viya"
  tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' RETURN
  case "$archive" in
    *.zip) unzip -q "$archive" -d "$tmp" ;;
    *.tgz) tar -xzf "$archive" -C "$tmp" ;;
  esac
  mkdir -p "$PREFIX"
  install -m 0755 "$tmp/sas-viya" "$PREFIX/sas-viya"

  # The macOS build is x86_64; Apple Silicon needs Rosetta 2.
  if [[ "$os" == "Darwin" && "$arch" == "arm64" ]] && ! arch -x86_64 /usr/bin/true 2>/dev/null; then
    warn "Rosetta 2 is not installed; run: softwareupdate --install-rosetta --agree-to-license"
  fi
}

if [[ $DO_INSTALL == 1 ]]; then install_cli; fi

if [[ -x "$PREFIX/sas-viya" ]]; then
  CLI="$PREFIX/sas-viya"
elif command -v sas-viya >/dev/null; then
  CLI="$(command -v sas-viya)"
else
  die "sas-viya not found (drop --skip-install?)"
fi
say "Using $("$CLI" --version)"

case ":$PATH:" in *":$PREFIX:"*) ;; *) warn "$PREFIX is not on PATH - add it, or set [batch] cli = \"$CLI\" in the vsub config" ;; esac

PROFILE_ARGS=()
[[ -n "$PROFILE" ]] && PROFILE_ARGS=(--profile "$PROFILE")

# --- 2. batch plug-in -----------------------------------------------------------
if [[ $DO_PLUGIN == 1 ]]; then
  if "$CLI" plugins list 2>/dev/null | grep -qw batch; then
    say "batch plug-in already installed"
  else
    say "Installing batch plug-in from the SAS repository"
    "$CLI" plugins install --repo SAS batch
  fi
fi

# --- 3. profile -----------------------------------------------------------------
if [[ $DO_PROFILE == 1 ]]; then
  if [[ -z "$URL" ]]; then
    cfg="${VSUB_CONFIG:-${XDG_CONFIG_HOME:-$HOME/.config}/vsub/config.toml}"
    [[ -f "$cfg" ]] && URL="$(sed -nE 's/^[[:space:]]*url[[:space:]]*=[[:space:]]*"([^"]+)".*/\1/p' "$cfg" | head -1)"
  fi
  if [[ -z "$URL" ]]; then read -r -p "Viya URL (https://...): " URL; fi
  [[ "$URL" =~ ^https?:// ]] || die "invalid URL: $URL"
  say "Configuring sas-viya profile ${PROFILE:-Default} -> $URL"
  "$CLI" ${PROFILE_ARGS[@]+"${PROFILE_ARGS[@]}"} profile init --sas-endpoint "${URL%/}" --output text --colors-enabled=true
fi

# --- 4. login -------------------------------------------------------------------
if [[ $DO_LOGIN == 1 ]]; then
  say "Logging in (open the URL shown, sign in, paste the code)"
  "$CLI" ${PROFILE_ARGS[@]+"${PROFILE_ARGS[@]}"} auth loginCode
fi

say "Done. Test with: $CLI ${PROFILE_ARGS[*]+${PROFILE_ARGS[*]}} batch jobs list"
