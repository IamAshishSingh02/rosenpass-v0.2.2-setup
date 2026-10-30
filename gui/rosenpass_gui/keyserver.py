"""Key-drop server client: upload/fetch public keys via the deployed server.

The server (repo ./server, deployed on Vercel) is a standalone public-key
drop-box: laptops POST their public keys, peers GET them. Secret keys never
leave the laptops; the rosenpass exchange itself runs peer-to-peer.
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request

from .profile import Profile


class KeyServerError(RuntimeError):
    pass


def _request(base: str, method: str, path: str, payload: dict | None = None,
             timeout: float = 30.0) -> dict:
    url = base.rstrip("/") + path
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"content-type": "application/json"} if data else {},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode()
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as err:
        detail = err.read().decode(errors="replace")[:300]
        raise KeyServerError(f"{method} {path} → HTTP {err.code}: {detail}")
    except (urllib.error.URLError, OSError) as err:
        reason = getattr(err, "reason", err)
        raise KeyServerError(f"cannot reach {base}: {reason}")


def upload_keys(profile: Profile, base_url: str) -> str:
    """Upload this profile's public keys; returns the server timestamp."""
    rp = profile.path("rosenpass.public-key").read_bytes()
    wg = profile.path("wg.pub").read_text().strip()
    out = _request(base_url, "POST", f"/api/keys?name={profile.name}", {
        "rpPublicKey": base64.b64encode(rp).decode(),
        "wgPub": wg,
    })
    return str(out.get("uploadedAt", "?"))


def fetch_peer_keys(base_url: str, peer_name: str) -> tuple[bytes, str]:
    """Fetch a peer's public keys; returns (rosenpass pubkey bytes, wg pubkey)."""
    out = _request(base_url, "GET", f"/api/keys?name={peer_name}")
    rp = base64.b64decode(out["rpPublicKey"], validate=True)
    wg = str(out["wgPub"]).strip()
    if not rp:
        raise KeyServerError(f"server returned an empty rosenpass key for {peer_name}")
    return rp, wg
