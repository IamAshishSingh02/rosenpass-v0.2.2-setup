"""WireGuard keygen, config generation, and elevated tunnel control.

Two platforms are supported:
- macOS: wireguard-tools (`wg`, `wg-quick`) via Homebrew; privileged
  operations run through an osascript administrator prompt.
- Windows: the official WireGuard app; tunnels are installed/uninstalled as
  services via wireguard.exe and privileged operations run through a UAC
  prompt (Start-Process -Verb RunAs).
"""

from __future__ import annotations

import os
import shlex
import subprocess
import tempfile
import time
from pathlib import Path

from . import runner
from .profile import Profile, derive, format_endpoint

WG_RUN_DIR = Path("/var/run/wireguard")


class WireGuardError(RuntimeError):
    pass


def install_hint() -> str:
    if runner.IS_WINDOWS:
        return "install the official WireGuard app: https://wireguard.com/install"
    return "run: brew install wireguard-tools"


def require_tools() -> list[str]:
    """Return a list of missing WireGuard tool problems (empty = all good)."""
    tools = ("wg", "wireguard") if runner.IS_WINDOWS else ("wg", "wg-quick")
    return [tool for tool in tools if not runner.which(tool)]


def gen_keypair(profile: Profile) -> None:
    """Generate a WireGuard keypair into the profile directory."""
    wg = runner.which("wg")
    if not wg:
        raise WireGuardError(f"wg not found — {install_hint()}")
    priv = subprocess.run([wg, "genkey"], capture_output=True, text=True, check=True)
    profile.path("wg.key").write_text(priv.stdout.strip() + "\n")
    pub = subprocess.run(
        [wg, "pubkey"], input=priv.stdout, capture_output=True, text=True, check=True
    )
    profile.path("wg.pub").write_text(pub.stdout.strip() + "\n")


def render_conf(profile: Profile, psk_b64: str) -> str:
    """Render a wg-quick config for this profile.

    The initiator gets the Endpoint (it must know where to send the first
    handshake); the responder's Endpoint is optional and only needed for
    roaming. The WireGuard app for Windows accepts this same format.
    """
    d = derive(profile.number)
    lines = [
        "[Interface]",
        f"PrivateKey = {profile.path('wg.key').read_text().strip()}",
        f"Address = {d['wg_ip']}",
        f"ListenPort = {d['wg_listen_port']}",
        "",
        "[Peer]",
        f"PublicKey = {profile.path('peer.wg.pub').read_text().strip()}",
        f"PresharedKey = {psk_b64}",
        f"AllowedIPs = {profile.peer_wg_ip}",
    ]
    if profile.is_initiator() and profile.endpoint_host:
        peer_number = 2 if profile.number == 1 else 1
        peer_wg_port = derive(peer_number)["wg_listen_port"]
        lines.append(
            f"Endpoint = {format_endpoint(profile.endpoint_host, peer_wg_port)}"
        )
    lines += ["PersistentKeepalive = 25", ""]
    return "\n".join(lines)


def write_conf(profile: Profile, psk_b64: str) -> Path:
    profile.conf_path.write_text(render_conf(profile, psk_b64))
    # The conf holds the tunnel private key and the PSK — wg-quick convention.
    os.chmod(profile.conf_path, 0o600)
    return profile.conf_path


# -- elevated control --------------------------------------------------------


def _osascript_admin(script_cmd: str) -> str:
    """Run *script_cmd* as root via an osascript administrator prompt."""
    inner = shlex.quote(script_cmd)
    result = subprocess.run(
        [
            "osascript",
            "-e",
            f'do shell script "{inner}" with administrator privileges',
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        err = result.stderr.strip() or result.stdout.strip() or "unknown error"
        if "User canceled" in err or "error -128" in err:
            raise WireGuardError("administrator prompt cancelled")
        raise WireGuardError(err)
    return result.stdout.strip()


def _uac_admin(command: str) -> str:
    """Run *command* elevated via a Windows UAC prompt; return its output.

    The command is wrapped in a temporary .bat that redirects output to a
    file, because Start-Process -Verb RunAs runs in a separate elevated
    process whose streams cannot be piped.
    """
    stamp = f"{os.getpid()}-{time.time_ns()}"
    tmp = Path(tempfile.gettempdir())
    bat = tmp / f"rosenpass-admin-{stamp}.bat"
    out = tmp / f"rosenpass-admin-{stamp}.txt"
    bat.write_text(
        f'@echo off\r\n{command} > "{out}" 2>&1\r\nexit /b %ERRORLEVEL%\r\n',
        encoding="mbcs",
    )
    ps = (
        "$p = Start-Process -FilePath cmd.exe -ArgumentList '/c',"
        f" '\"{bat}\"' -Verb RunAs -Wait -PassThru -WindowStyle Hidden;"
        " exit $p.ExitCode"
    )
    try:
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                ps,
            ],
            capture_output=True,
            text=True,
        )
    finally:
        output = ""
        try:
            output = out.read_text(errors="replace")
        except OSError:
            pass
        bat.unlink(missing_ok=True)
        out.unlink(missing_ok=True)
    if result.returncode != 0:
        combined = (result.stderr + result.stdout).lower()
        if "canceled by the user" in combined or "cancelled by the user" in combined:
            raise WireGuardError("administrator prompt cancelled")
        raise WireGuardError(
            output.strip()
            or result.stderr.strip()
            or result.stdout.strip()
            or "unknown error"
        )
    return output.strip()


def _elevated(command: str) -> str:
    """Run a shell command line as root (macOS) or admin (Windows)."""
    if runner.IS_WINDOWS:
        return _uac_admin(command)
    return _osascript_admin(command)


def _win_tool(name: str) -> str:
    found = runner.which(name)
    if not found:
        raise WireGuardError(f"{name}.exe not found — {install_hint()}")
    return found


def _q(path) -> str:
    """Quote a path for a cmd.exe command line."""
    return f'"{path}"'


def wg_quick_up(conf: Path) -> str:
    if runner.IS_WINDOWS:
        # The WireGuard app installs the conf as a tunnel service; the
        # interface is named after the conf file stem.
        app = _win_tool("wireguard")
        return _elevated(f"{_q(app)} /installtunnelservice {_q(conf)}")
    wgq = runner.which("wg-quick")
    if not wgq:
        raise WireGuardError(f"wg-quick not found — {install_hint()}")
    return _osascript_admin(f"{wgq} up {conf}")


def wg_quick_down(conf: Path) -> str:
    if runner.IS_WINDOWS:
        app = _win_tool("wireguard")
        return _elevated(f"{_q(app)} /uninstalltunnelservice {conf.stem}")
    wgq = runner.which("wg-quick")
    if not wgq:
        raise WireGuardError(f"wg-quick not found — {install_hint()}")
    return _osascript_admin(f"{wgq} down {conf}")


def apply_psk(profile: Profile, iface: str) -> str:
    """Push a refreshed PSK into the live tunnel (no tunnel restart)."""
    peer_pub = profile.path("peer.wg.pub").read_text().strip()
    psk_file = profile.path("psk.out")
    if runner.IS_WINDOWS:
        wg = _win_tool("wg")
        return _elevated(
            f"{_q(wg)} set {iface} peer {peer_pub} preshared-key {_q(psk_file)}"
        )
    wg = runner.which("wg")
    return _osascript_admin(f"{wg} set {iface} peer {peer_pub} preshared-key {psk_file}")


# -- status ------------------------------------------------------------------


def resolve_iface(profile: Profile) -> str | None:
    """The interface backing this config, if up.

    macOS reads the name file wg-quick leaves in /var/run/wireguard; on
    Windows the tunnel interface is named after the conf file stem.
    """
    if runner.IS_WINDOWS:
        name = profile.conf_path.stem
        wg = runner.which("wg")
        if not wg:
            return None
        result = runner.run([wg, "show", name])
        return name if result.returncode == 0 else None
    name_file = WG_RUN_DIR / f"{profile.name}.name"
    try:
        iface = name_file.read_text().strip()
    except OSError:
        return None
    return iface or None


def show() -> str:
    wg = runner.which("wg")
    if not wg:
        raise WireGuardError(f"wg not found — {install_hint()}")
    result = subprocess.run([wg, "show"], capture_output=True, text=True)
    if result.returncode != 0:
        raise WireGuardError(result.stderr.strip() or "wg show failed")
    return result.stdout
