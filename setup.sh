#!/usr/bin/env bash
# setup.sh — interactive, end-to-end environment setup for running the
# rosenpass GUI on Windows (run from Git Bash, which ships with Git for
# Windows). Also works on macOS (brew path) for convenience.
#
# What it does, in order (each step asks before acting):
#   1. checks/install Rust (rustup + MSVC toolchain)
#   2. checks/install Visual Studio Build Tools with the C++ workload
#   3. checks/install CMake
#   4. checks/install Python 3 with Tk
#   5. checks/install the WireGuard app (wg.exe / wireguard.exe)
#   6. builds rosenpass (cargo build --release)
#   7. verifies everything and tells you how to launch the GUI
#
# Usage:  ./setup.sh            (from the repo root, in Git Bash)
set -u

bold()  { printf '\033[1m%s\033[0m\n' "$*"; }
ok()    { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn()  { printf '  \033[33m!\033[0m %s\n' "$*"; }
fail()  { printf '  \033[31m✗\033[0m %s\n' "$*"; }

ask() { # ask "question" -> returns 0 on y/Y
  local answer
  read -r -p "  $1 [y/N] " answer
  [[ "$answer" =~ ^[Yy]$ ]]
}

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

OS="$(uname -s)"
IS_WINDOWS=0
[[ "$OS" =~ ^MINGW|^MSYS|^CYGWIN ]] && IS_WINDOWS=1

bold "=== rosenpass setup ($([[ $IS_WINDOWS -eq 1 ]] && echo Windows/Git-Bash || echo macOS)) ==="
echo "repo: $REPO_ROOT"
echo

MISSING=0   # track anything we could not fix

# -- helper: run an installer only if the user agrees -------------------------
maybe_install() { # maybe_install "label" "question" "command..."
  local label="$1" question="$2"; shift 2
  if ask "$question"; then
    bold "  installing $label…"
    "$@" || { fail "$label installation failed — install it manually, then re-run this script"; MISSING=1; return 1; }
    ok "$label installed"
    NEED_RESTART=1
  else
    warn "skipped $label — if it was missing, things will break later"
    MISSING=1
  fi
}

# -- 1. Rust ------------------------------------------------------------------
bold "1/7 Rust (rustup + cargo)"
if command -v cargo >/dev/null 2>&1; then
  ok "cargo found: $(cargo --version)"
else
  if [[ $IS_WINDOWS -eq 1 ]]; then
    maybe_install "Rust (rustup)" \
      "Rust is missing. Install via winget now?" \
      winget install --id Rustlang.Rustup -e --accept-source-agreements --accept-package-agreements
  else
    maybe_install "Rust (rustup)" \
      "Rust is missing. Install rustup now?" \
      bash -c 'curl --proto "=https" --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y'
  fi
  # rustup installs don't extend the current PATH
  export PATH="$HOME/.cargo/bin:$HOME/.rustup/toolchains/stable-x86_64-pc-windows-msvc/bin:$PATH"
  command -v cargo >/dev/null 2>&1 && ok "cargo available after PATH refresh" || { fail "cargo still not found — open a NEW shell and re-run"; MISSING=1; }
fi
echo

# -- 2. MSVC Build Tools (Windows only) ---------------------------------------
if [[ $IS_WINDOWS -eq 1 ]]; then
  bold "2/7 Visual Studio Build Tools (C++ toolchain)"
  # vswhere ships with Build Tools; cl.exe only exists on VS native prompts.
  VSWHERE="/c/Program Files (x86)/Microsoft Visual Studio/Installer/vswhere.exe"
  if [[ -f "$VSWHERE" ]] && "$VSWHERE" -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath 2>/dev/null | grep -q .; then
    ok "MSVC C++ toolchain found"
  else
    warn "Visual Studio Build Tools with the C++ workload not found"
    echo "    (~2 GB download; required to link rosenpass)"
    maybe_install "VS Build Tools (C++)" \
      "Install Visual Studio Build Tools with the C++ workload now?" \
      winget install --id Microsoft.VisualStudio.2022.BuildTools -e \
        --accept-source-agreements --accept-package-agreements \
        --override "--wait --quiet --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"
  fi
  echo
fi

# -- 3. CMake -----------------------------------------------------------------
bold "3/7 CMake"
if [[ $IS_WINDOWS -eq 1 ]]; then
  if command -v cmake >/dev/null 2>&1 || [[ -f "/c/Program Files/CMake/bin/cmake.exe" ]]; then
    ok "cmake found"
  else
    maybe_install "CMake" "Install CMake now?" \
      winget install --id Kitware.CMake -e --accept-source-agreements --accept-package-agreements
  fi
else
  if command -v cmake >/dev/null 2>&1; then ok "cmake found"
  else maybe_install "CMake" "Install CMake via brew now?" brew install cmake; fi
fi
echo

# -- 4. Python with Tk ----------------------------------------------------------
bold "4/7 Python 3 (with Tk for the GUI)"
PY=python
command -v py >/dev/null 2>&1 && PY=py
if command -v "$PY" >/dev/null 2>&1 || command -v python3 >/dev/null 2>&1; then
  if [[ $PY == py ]]; then "$PY" -c "import tkinter" 2>/dev/null && ok "python + tkinter found" || warn "python found but tkinter missing — reinstall python with tcl/tk"
  else python3 -c "import tkinter" 2>/dev/null && ok "python3 + tkinter found" || warn "python3 found but tkinter missing"
  fi
else
  if [[ $IS_WINDOWS -eq 1 ]]; then
    maybe_install "Python 3" "Install Python 3 now?" \
      winget install --id Python.Python.3.12 -e --accept-source-agreements --accept-package-agreements
  else
    maybe_install "Python 3" "Install python via brew now?" brew install python-tk@3.13
  fi
fi
echo

# -- 5. WireGuard ---------------------------------------------------------------
bold "5/7 WireGuard"
if [[ $IS_WINDOWS -eq 1 ]]; then
  if [[ -f "/c/Program Files/WireGuard/wg.exe" ]] || command -v wg >/dev/null 2>&1; then
    ok "WireGuard found (wg.exe)"
  else
    maybe_install "WireGuard" "Install the WireGuard app now?" \
      winget install --id WireGuard.WireGuard -e --accept-source-agreements --accept-package-agreements
    warn "reboot (or at least log out/in) after a fresh WireGuard install"
  fi
else
  if command -v wg >/dev/null 2>&1; then ok "wg found"
  else maybe_install "wireguard-tools" "Install wireguard-tools via brew now?" brew install wireguard-tools; fi
fi
echo

# -- 6. Build -------------------------------------------------------------------
bold "6/7 Building rosenpass (release)"
if command -v cargo >/dev/null 2>&1; then
  if [[ $IS_WINDOWS -eq 1 ]]; then
    warn "if this fails with 'link.exe not found', run it from the"
    warn "  'x64 Native Tools Command Prompt for VS' instead, or add"
    warn "  the MSVC bin dir to PATH and re-run."
  fi
  ask "Run 'cargo build --release' now? This compiles libsodium and liboqs (slow the first time)" && {
    cargo build --release || { fail "build failed — paste the error for the code fix"; MISSING=1; }
  }
else
  fail "cargo not available — cannot build (fix step 1, then re-run)"
  MISSING=1
fi
echo

# -- 7. Verify ------------------------------------------------------------------
bold "7/7 Verification"
BIN="target/release/rosenpass"
[[ $IS_WINDOWS -eq 1 ]] && BIN="target/release/rosenpass.exe"
if [[ -f "$BIN" ]]; then
  ok "rosenpass binary present: $BIN"
else
  fail "rosenpass binary missing — the build did not run/succeed"
  MISSING=1
fi

if [[ $IS_WINDOWS -eq 1 ]]; then
  PY_LAUNCH="py -m rosenpass_gui"
  command -v py >/dev/null 2>&1 || PY_LAUNCH="python -m rosenpass_gui"
else
  PY_LAUNCH=".venv/bin/python -m rosenpass_gui   # or: python3 -m rosenpass_gui"
fi

echo
if [[ $MISSING -eq 0 ]]; then
  bold "=== ALL SET — demo run ==="
  echo "  1. cd gui && $PY_LAUNCH"
  echo "  2. profile 'laptop-1' or 'laptop-2' -> Generate keys"
  echo "  3. import the peer's public keys -> Start exchange"
  echo "  4. Enable WireGuard (UAC/admin prompt) -> curl the peer over 10.0.0.x"
else
  bold "=== setup incomplete ==="
  echo "  fix the items marked ✗ above, then re-run: ./setup.sh"
  exit 1
fi
