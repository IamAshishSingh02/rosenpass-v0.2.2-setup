"""Profile model, key generation, and rosenpass command construction."""

from __future__ import annotations

import base64
import binascii
import json
import re
import shutil
import subprocess
from pathlib import Path

from .runner import IS_WINDOWS

BASE_DIR = Path.home() / ".rosenpass-gui"
PROFILES_DIR = BASE_DIR / "profiles"

# Files that make up one profile, inside ~/.rosenpass-gui/profiles/<name>/
KEY_FILES = (
    "rosenpass.secret-key",
    "rosenpass.public-key",
    "psk.out",
    "wg.key",
    "wg.pub",
    "peer.rosenpass.public-key",
    "peer.wg.pub",
)

ROLE_AUTO = "auto"
ROLE_INITIATOR = "initiator"
ROLE_RESPONDER = "responder"

# rosenpass's IPv6 wildcard bind ("[::]") fails on this macOS build
# ("All sockets returned errors"), so always bind IPv4. 0.0.0.0 serves both
# loopback demos and two-real-Mac setups.
LISTEN_HOST = "0.0.0.0"

_B64_32 = re.compile(r"^[A-Za-z0-9+/]{43}=$")


def default_rosenpass_binary() -> Path:
    """The release binary shipped by this repository, if it is built."""
    here = Path(__file__).resolve().parents[2]  # .../rosenpass/gui/rosenpass_gui
    exe = "rosenpass.exe" if IS_WINDOWS else "rosenpass"
    return here / "target" / "release" / exe


def profile_dir(name: str) -> Path:
    return PROFILES_DIR / name


def sanitize_name(name: str) -> str:
    """Reduce a machine name to something safe for file/directory names."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "-", name.strip())
    return cleaned.strip("-") or "peer"


def derive(number: int) -> dict:
    """Ports and addresses implied by the machine number."""
    return {
        "listen_port": 10000 + number,        # rosenpass UDP port
        "wg_listen_port": 51820 + number,     # WireGuard UDP port
        "wg_ip": f"10.0.0.{number}/24",
        "peer_wg_ip": f"10.0.0.{2 if number == 1 else 1}/32",
    }


class Profile:
    """One machine's configuration and key material."""

    def __init__(self, name: str, number: int):
        self.name = sanitize_name(name)
        self.number = number
        self.endpoint_host = ""
        self.endpoint_port = 10000 + (2 if number == 1 else 1)
        self.peer_name = ""
        self.peer_wg_ip = derive(number)["peer_wg_ip"]
        self.role = ROLE_AUTO
        self.rosenpass_binary = str(default_rosenpass_binary())
        self.server_url = ""
        self._dir = profile_dir(self.name)

    # -- paths ---------------------------------------------------------------

    @property
    def dir(self) -> Path:
        return self._dir

    def path(self, filename: str) -> Path:
        return self._dir / filename

    @property
    def conf_path(self) -> Path:
        return self._dir / f"{self.name}.conf"

    # -- persistence ---------------------------------------------------------

    def save(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        data = {
            "name": self.name,
            "number": self.number,
            "endpoint_host": self.endpoint_host,
            "endpoint_port": self.endpoint_port,
            "peer_name": self.peer_name,
            "peer_wg_ip": self.peer_wg_ip,
            "role": self.role,
            "rosenpass_binary": self.rosenpass_binary,
            "server_url": self.server_url,
        }
        (self._dir / "profile.json").write_text(json.dumps(data, indent=2) + "\n")

    @classmethod
    def load(cls, name: str) -> "Profile":
        d = profile_dir(name)
        data = json.loads((d / "profile.json").read_text())
        prof = cls(data["name"], data["number"])
        prof.endpoint_host = data.get("endpoint_host", "")
        prof.endpoint_port = data.get("endpoint_port", prof.endpoint_port)
        prof.peer_name = data.get("peer_name", "")
        prof.peer_wg_ip = data.get("peer_wg_ip", prof.peer_wg_ip)
        prof.role = data.get("role", ROLE_AUTO)
        prof.rosenpass_binary = data.get(
            "rosenpass_binary", str(default_rosenpass_binary())
        )
        prof.server_url = data.get("server_url", "")
        prof._dir = d
        return prof

    @staticmethod
    def list_profiles() -> list[str]:
        if not PROFILES_DIR.is_dir():
            return []
        return sorted(
            p.parent.name
            for p in PROFILES_DIR.glob("*/profile.json")
            if p.is_file()
        )

    # -- derived state -------------------------------------------------------

    def has_rosenpass_keys(self) -> bool:
        return (
            self.path("rosenpass.secret-key").is_file()
            and self.path("rosenpass.public-key").is_file()
        )

    def has_peer_rosenpass_key(self) -> bool:
        f = self.path("peer.rosenpass.public-key")
        expected = self.expected_rosenpass_key_size()
        return f.is_file() and (expected is None or f.stat().st_size == expected)

    def has_psk(self) -> bool:
        return self.path("psk.out").is_file()

    def has_wg_keypair(self) -> bool:
        return self.path("wg.key").is_file() and self.path("wg.pub").is_file()

    def has_peer_wg_key(self) -> bool:
        return self.path("peer.wg.pub").is_file()

    def effective_role(self) -> str:
        """Resolve the role dropdown: auto means number 1 initiates."""
        if self.role in (ROLE_INITIATOR, ROLE_RESPONDER):
            return self.role
        return ROLE_INITIATOR if self.number == 1 else ROLE_RESPONDER

    def is_initiator(self) -> bool:
        return self.effective_role() == ROLE_INITIATOR

    def number_changed(self, new_number: int) -> None:
        """Re-derive ports/IPs when the machine number changes."""
        self.number = new_number
        d = derive(new_number)
        self.peer_wg_ip = d["peer_wg_ip"]
        if not self.endpoint_port:
            self.endpoint_port = 10000 + (2 if new_number == 1 else 1)

    # -- key generation ------------------------------------------------------

    def generate_rosenpass_keys(self) -> None:
        """Run `rosenpass gen-keys` (overwrites any existing pair).

        Note: rosenpass writes both key files as raw 32-byte binary
        (cli.rs generate_and_save_keypair -> spk.store), not base64.
        """
        self._dir.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                self.rosenpass_binary,
                "gen-keys",
                "--secret-key", str(self.path("rosenpass.secret-key")),
                "--public-key", str(self.path("rosenpass.public-key")),
                "--force",
            ],
            check=True,
            capture_output=True,
            text=True,
        )

    # -- key import ----------------------------------------------------------

    def import_peer_rosenpass_key_from_file(self, src: str) -> None:
        """Copy the peer's raw public-key file into the profile.

        Key files are binary blobs written by the daemon's secret-memory
        store (524160 bytes on this build — the whole slab, not just the
        key). The daemon loads them with read_exact_to_end, so the size
        must match our own public-key file exactly.
        """
        data = Path(src).read_bytes()
        expected = self.expected_rosenpass_key_size()
        if expected and len(data) != expected:
            raise ValueError(
                f"{src} is {len(data)} bytes; a rosenpass public key file on "
                f"this build is {expected} bytes (copy the peer's "
                "rosenpass.public-key file as-is)"
            )
        if not data:
            raise ValueError(f"{src} is empty")
        shutil.copy(src, self.path("peer.rosenpass.public-key"))

    def import_peer_rosenpass_key_from_b64(self, text: str) -> None:
        """Accept base64 of the peer's raw public-key file, store as bytes."""
        import base64 as _b64

        text = "".join(text.split())  # tolerate whitespace/newlines
        try:
            data = _b64.b64decode(text, validate=True)
        except (_b64.binascii.Error, ValueError) as err:
            raise ValueError(f"not valid base64: {err}")
        expected = self.expected_rosenpass_key_size()
        if expected and len(data) != expected:
            raise ValueError(
                f"decoded key is {len(data)} bytes; expected {expected} "
                "(paste the base64 of the peer's whole rosenpass.public-key file)"
            )
        self.path("peer.rosenpass.public-key").write_bytes(data)

    def expected_rosenpass_key_size(self) -> int | None:
        """File size of rosenpass key files on this build, if we know ours."""
        own = self.path("rosenpass.public-key")
        return own.stat().st_size if own.is_file() else None

    # -- rosenpass command ---------------------------------------------------

    def exchange_cmd(self) -> list[str]:
        """Build the `rosenpass exchange` argv.

        Grammar (rosenpass/src/config.rs::parse_args):
          initiator: ... peer public-key <PK> endpoint <HOST>:<PORT> outfile <PSK>
          responder: identical, without the endpoint token.
        """
        cmd = [
            self.rosenpass_binary,
            "exchange",
            "public-key", str(self.path("rosenpass.public-key")),
            "secret-key", str(self.path("rosenpass.secret-key")),
            "listen", f"{LISTEN_HOST}:{derive(self.number)['listen_port']}",
            "verbose",
            "peer",
            "public-key", str(self.path("peer.rosenpass.public-key")),
        ]
        if self.is_initiator():
            if not self.endpoint_host:
                raise ValueError("initiator needs a peer endpoint host")
            cmd += ["endpoint", format_endpoint(self.endpoint_host, self.endpoint_port)]
        cmd += ["outfile", str(self.path("psk.out"))]
        return cmd


def format_endpoint(host: str, port: int) -> str:
    """host:port, bracketing bare IPv6 literals so 2001:db8::1:10002 can't happen."""
    host = host.strip()
    if ":" in host and not host.startswith("["):
        return f"[{host}]:{port}"
    return f"{host}:{port}"


def validate_b64_32(text: str) -> str:
    """Validate a 32-byte base64 key (44 chars, padded) and return it stripped."""
    text = text.strip()
    if len(text) != 44 or not _B64_32.match(text):
        raise ValueError(
            "expected 44-character base64 (a 32-byte key), "
            f"got {len(text)} characters"
        )
    base64.b64decode(text, validate=True)  # rejects trailing garbage/padding errors
    return text


def read_b64_file(path: Path) -> str:
    return validate_b64_32(path.read_text())


def read_psk(path: Path) -> str:
    """Read the exchanged PSK, tolerating a missing trailing newline."""
    text = path.read_text().strip()
    try:
        validate_b64_32(text)
    except (ValueError, binascii.Error) as err:
        raise ValueError(f"{path} does not contain a valid 32-byte base64 key: {err}")
    return text
