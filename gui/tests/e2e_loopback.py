#!/usr/bin/env python3
"""Automated end-to-end loopback test for the rosenpass GUI package.

Exercises the real rosenpass binary in a two-peer loopback exchange,
driving everything through the GUI package's own helpers:

  * ``rosenpass_gui.profile``   - Profile model, key generation, argv building
  * ``rosenpass_gui.exchange``  - ExchangeProcess daemon orchestration
  * ``rosenpass_gui.wireguard`` - wg keygen + wg-quick config rendering

Modes:
  * full (default)      - spawns two ``rosenpass exchange`` daemons and
                          compares the exchanged PSKs.
  * --dry-run           - profile creation, command construction and conf
                          rendering only; no daemons are spawned.  Works
                          before the binary is built (fake key material
                          is fabricated if the binary is absent).
  * --stub-binary PATH  - full mode pointed at a stand-in binary (used to
                          self-test the daemon-wait / PSK-comparison logic
                          before the real binary exists).

Exit codes: 0 = PASS, 1 = FAIL, 2 = binary missing (full mode only).

Stdlib only; runs under macOS system Python 3.9.
"""

from __future__ import annotations

import argparse
import atexit
import base64
import hashlib
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]  # .../rosenpass
GUI_DIR = REPO_ROOT / "gui"
if str(GUI_DIR) not in sys.path:
    sys.path.insert(0, str(GUI_DIR))

from rosenpass_gui import profile as profile_mod  # noqa: E402
from rosenpass_gui import wireguard as wg_mod  # noqa: E402
from rosenpass_gui.exchange import ExchangeProcess  # noqa: E402

EXCHANGE_TIMEOUT_S = 30.0
INITIATOR_NAME, INITIATOR_NUM = "laptop-1", 1
RESPONDER_NAME, RESPONDER_NUM = "laptop-2", 2
INITIATOR_HOST = "127.0.0.1"
INITIATOR_ENDPOINT_PORT = 10002  # responder's rosenpass listen port


class TestError(Exception):
    """Unrecoverable test-flow failure (as opposed to a failed check)."""


def log(msg: str) -> None:
    print("[%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


class Results:
    """Collects named pass/fail checks and prints a summary at the end."""

    def __init__(self):
        self.passed = 0
        self.failures = []

    def check(self, name, condition, detail=""):
        if condition:
            self.passed += 1
            log("  ok   - %s" % name)
        else:
            self.failures.append((name, detail))
            log("  FAIL - %s%s" % (name, (": " + detail) if detail else ""))
        return bool(condition)

    def summary(self, mode):
        total = self.passed + len(self.failures)
        print("\n%s: %d/%d checks passed" % (mode, self.passed, total))
        for name, detail in self.failures:
            print("  FAILED: %s%s" % (name, (": " + detail) if detail else ""))
        return not self.failures


# -- child-process bookkeeping ------------------------------------------------

_PROCS = []  # every ExchangeProcess started this run


def _cleanup_procs():
    for ep in _PROCS:
        try:
            ep.stop()
        except Exception:
            pass


atexit.register(_cleanup_procs)


def fake_b64_32(seed: str) -> str:
    """Deterministic, valid 32-byte base64 key (44 chars) for offline modes."""
    digest = hashlib.sha256(("e2e-loopback:" + seed).encode()).digest()
    return base64.b64encode(digest).decode()


def write_fake_rosenpass_keys(prof: profile_mod.Profile) -> None:
    """Fabricate key files when no binary exists (dry-run only).

    Real rosenpass key files are raw 32-byte binary (cli.rs spk.store), so
    the fabricated files must be too — has_peer_rosenpass_key() and
    import_peer_rosenpass_key_from_file() both check the size.
    """
    prof.dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(("secret:" + prof.name).encode()).digest()
    prof.path("rosenpass.secret-key").write_bytes(digest[:32])
    digest = hashlib.sha256(("public:" + prof.name).encode()).digest()
    prof.path("rosenpass.public-key").write_bytes(digest[:32])


def build_profile(name, number, binary):
    """Create one Profile in the redirected profiles dir and seed its keys."""
    prof = profile_mod.Profile(name, number)
    prof.rosenpass_binary = binary
    if Path(binary).is_file():
        prof.generate_rosenpass_keys()  # real binary (or stub): use the real path
        log("  %s: rosenpass keypair generated via binary" % prof.name)
    else:
        write_fake_rosenpass_keys(prof)
        log("  %s: binary absent, fabricated rosenpass key files" % prof.name)

    # WireGuard keypair via the wireguard module (real `wg genkey`).
    wg_mod.gen_keypair(prof)
    return prof


def cross_import(prof_a, prof_b):
    """Each peer imports the other's public keys (rosenpass + WireGuard)."""
    prof_a.import_peer_rosenpass_key_from_file(str(prof_b.path("rosenpass.public-key")))
    prof_b.import_peer_rosenpass_key_from_file(str(prof_a.path("rosenpass.public-key")))
    shutil.copy(prof_a.path("wg.pub"), prof_b.path("peer.wg.pub"))
    shutil.copy(prof_b.path("wg.pub"), prof_a.path("peer.wg.pub"))


def set_roles(prof_init, prof_resp):
    """Point the initiator at the responder's loopback endpoint."""
    prof_init.role = profile_mod.ROLE_INITIATOR
    prof_resp.role = profile_mod.ROLE_RESPONDER
    prof_init.endpoint_host = INITIATOR_HOST
    prof_init.endpoint_port = INITIATOR_ENDPOINT_PORT
    prof_init.peer_name = prof_resp.name
    prof_resp.peer_name = prof_init.name


# -- check phases -------------------------------------------------------------


def check_profiles(res, prof_init, prof_resp):
    log("profile layout / derivation")
    d1 = profile_mod.derive(prof_init.number)
    d2 = profile_mod.derive(prof_resp.number)
    res.check("profiles live under the redirected PROFILES_DIR",
              prof_init.dir == profile_mod.PROFILES_DIR / prof_init.name
              and prof_resp.dir == profile_mod.PROFILES_DIR / prof_resp.name)
    res.check("initiator is number 1 and resolves role initiator",
              prof_init.number == 1 and prof_init.is_initiator())
    res.check("responder is number 2 and resolves role responder",
              prof_resp.number == 2 and not prof_resp.is_initiator())
    res.check("derived listen ports 10001 / 10002",
              d1["listen_port"] == 10001 and d2["listen_port"] == 10002,
              "%s / %s" % (d1["listen_port"], d2["listen_port"]))
    res.check("derived wg ips mirror 10.0.0.1 / 10.0.0.2",
              d1["wg_ip"] == "10.0.0.1/24" and d1["peer_wg_ip"] == "10.0.0.2/32"
              and d2["wg_ip"] == "10.0.0.2/24" and d2["peer_wg_ip"] == "10.0.0.1/32")
    res.check("initiator endpoint is 127.0.0.1:10002",
              prof_init.endpoint_host == INITIATOR_HOST
              and prof_init.endpoint_port == INITIATOR_ENDPOINT_PORT)


def check_commands(res, prof_init, prof_resp):
    log("rosenpass exchange command construction")
    cmd_i = prof_init.exchange_cmd()
    cmd_r = prof_resp.exchange_cmd()
    res.check("argv starts with the binary and 'exchange public-key'",
              cmd_i[1:3] == ["exchange", "public-key"]
              and cmd_r[1:3] == ["exchange", "public-key"])
    res.check("both daemons listen on 0.0.0.0:1000N",
              "listen" in cmd_i and "0.0.0.0:10001" in cmd_i
              and "listen" in cmd_r and "0.0.0.0:10002" in cmd_r)
    res.check("only the initiator has an endpoint 127.0.0.1:10002",
              "endpoint" in cmd_i and "127.0.0.1:10002" in cmd_i
              and "endpoint" not in cmd_r)
    res.check("both cmds end with 'outfile <psk.out>'",
              cmd_i[-2:] == ["outfile", str(prof_init.path("psk.out"))]
              and cmd_r[-2:] == ["outfile", str(prof_resp.path("psk.out"))])
    print("    initiator: %s" % " ".join(cmd_i))
    print("    responder: %s" % " ".join(cmd_r))


def check_confs(res, prof_init, prof_resp, psk_b64):
    log("wireguard conf rendering (render only; no tunnel touched)")
    conf_i = wg_mod.render_conf(prof_init, psk_b64)
    conf_r = wg_mod.render_conf(prof_resp, psk_b64)

    def field(conf, key):
        for line in conf.splitlines():
            if line.startswith(key + " = "):
                return line.split(" = ", 1)[1]
        return None

    res.check("initiator conf carries Endpoint 127.0.0.1:10002",
              "Endpoint = %s:%d" % (INITIATOR_HOST, INITIATOR_ENDPOINT_PORT) in conf_i)
    res.check("responder conf carries no Endpoint", "Endpoint" not in conf_r)
    res.check("PresharedKey identical on both sides and matches the exchanged PSK",
              field(conf_i, "PresharedKey") == psk_b64
              and field(conf_r, "PresharedKey") == psk_b64)
    res.check("Addresses mirror 10.0.0.1 / 10.0.0.2",
              field(conf_i, "Address") == "10.0.0.1/24"
              and field(conf_r, "Address") == "10.0.0.2/24")
    res.check("AllowedIPs mirror 10.0.0.2 / 10.0.0.1",
              field(conf_i, "AllowedIPs") == "10.0.0.2/32"
              and field(conf_r, "AllowedIPs") == "10.0.0.1/32")
    res.check("each conf's PublicKey is the other peer's wg public key",
              field(conf_i, "PublicKey") == prof_resp.path("wg.pub").read_text().strip()
              and field(conf_r, "PublicKey") == prof_init.path("wg.pub").read_text().strip())
    res.check("each conf's PrivateKey is its own wg private key",
              field(conf_i, "PrivateKey") == prof_init.path("wg.key").read_text().strip()
              and field(conf_r, "PrivateKey") == prof_resp.path("wg.key").read_text().strip())


def wait_for_exchange(procs, tags, timeout_s):
    """Drain both daemons until each has produced one 'exchanged' key.

    Returns (tag -> psk Path) for the daemons that succeeded.  Daemon
    stdout/stderr is echoed to the console as it arrives.
    """
    deadline = time.monotonic() + timeout_s
    got = {tag: None for tag in tags}
    while time.monotonic() < deadline:
        for tag, ep in zip(tags, procs):
            for ev in ep.drain():
                kind = ev[0]
                if kind == "output-key":
                    _, path, why = ev
                    log("  [%s] output-key %s (%s)" % (tag, path, why))
                    if why == "exchanged" and got[tag] is None:
                        got[tag] = path
                elif kind == "exited":
                    if got[tag] is None and ep.is_alive() is False:
                        log("  [%s] daemon exited before producing a key" % tag)
                        return got  # fail fast; caller reports the stderr tail
                else:
                    log("  [%s] %s: %s" % (tag, kind, ev[1]))
        if all(got.values()):
            return got
        time.sleep(0.05)
    return got


def full_exchange(res, prof_init, prof_resp):
    """Start both daemons, wait for exchanged PSKs, compare, stop, check leaks."""
    log("starting daemons")
    procs = []
    try:
        # Responder first so it is listening when the initiator knocks.
        for tag, prof in (("B/responder", prof_resp), ("A/initiator", prof_init)):
            ep = ExchangeProcess(prof.exchange_cmd())
            ep.start()
            _PROCS.append(ep)
            procs.append(ep)
            log("  started %s (pid %s)" % (tag, ep.proc.pid))

        log("waiting up to %.0f s for exchanged keys on both sides" % EXCHANGE_TIMEOUT_S)
        got = wait_for_exchange(procs, ("A/initiator", "B/responder"), EXCHANGE_TIMEOUT_S)

        for tag, prof in (("A/initiator", prof_init), ("B/responder", prof_resp)):
            if got[tag] is None:
                idx = 0 if tag.startswith("A") else 1
                tail = procs[idx].stderr_tail().strip()
                raise TestError(
                    "%s produced no exchanged key within %.0f s%s"
                    % (tag, EXCHANGE_TIMEOUT_S,
                       ("; stderr tail:\n" + tail) if tail else "")
                )

        log("reading and comparing PSKs")
        psk_a = profile_mod.read_psk(prof_init.path("psk.out"))
        psk_b = profile_mod.read_psk(prof_resp.path("psk.out"))
        res.check("both peers exchanged the identical PSK", psk_a == psk_b,
                  "%s != %s" % (psk_a, psk_b))
    finally:
        log("stopping daemons")
        _cleanup_procs()

    # Leak check: every child must be gone, and no stray rosenpass either.
    time.sleep(0.3)  # let the OS reap
    res.check("all ExchangeProcess children stopped",
              all(not ep.is_alive() for ep in procs))
    pgrep = subprocess.run(
        ["pgrep", "-f", "rosenpass exchange"], capture_output=True, text=True
    )
    res.check("no rosenpass exchange processes leaked (pgrep)",
              pgrep.returncode == 1,  # 1 = no processes matched
              "pgrep found: %s" % pgrep.stdout.strip())
    return psk_a


# -- modes --------------------------------------------------------------------


def run_dry_run(res, prof_init, prof_resp, binary_exists):
    log("DRY-RUN: profiles, commands and confs only (no daemons)")
    if not binary_exists:
        log("  (binary not built; key material fabricated offline)")
    check_profiles(res, prof_init, prof_resp)
    check_commands(res, prof_init, prof_resp)
    check_confs(res, prof_init, prof_resp, fake_b64_32("psk"))
    res.check("conf files can be written next to the profiles",
              wg_mod.write_conf(prof_init, fake_b64_32("psk")).is_file()
              and wg_mod.write_conf(prof_resp, fake_b64_32("psk")).is_file())


def run_full(res, prof_init, prof_resp, binary_exists):
    if not binary_exists:
        print(
            "\nERROR: rosenpass binary not found at:\n  %s\n"
            "Build it first (cargo build --release) or pass --binary / --stub-binary."
            % prof_init.rosenpass_binary,
            file=sys.stderr,
        )
        return 2
    psk = full_exchange(res, prof_init, prof_resp)
    check_profiles(res, prof_init, prof_resp)
    check_commands(res, prof_init, prof_resp)
    check_confs(res, prof_init, prof_resp, psk)
    return 0


def main(argv=None):
    global EXCHANGE_TIMEOUT_S
    parser = argparse.ArgumentParser(
        description="Two-peer rosenpass loopback end-to-end test (GUI package)."
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="no daemons; profiles + command + conf checks only")
    parser.add_argument("--binary", default=None,
                        help="path to the rosenpass binary (default: target/release/rosenpass)")
    parser.add_argument("--stub-binary", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--timeout", type=float, default=EXCHANGE_TIMEOUT_S,
                        help="seconds to wait for each exchanged key (default 30)")
    args = parser.parse_args(argv)
    EXCHANGE_TIMEOUT_S = args.timeout

    binary = args.stub_binary or args.binary or str(profile_mod.default_rosenpass_binary())
    binary_exists = Path(binary).is_file()

    tmp = Path(tempfile.mkdtemp(prefix="e2e-loopback-"))
    # Redirect the profile module's globals BEFORE any Profile is built.
    profile_mod.BASE_DIR = tmp
    profile_mod.PROFILES_DIR = tmp / "profiles"

    res = Results()
    exit_code = 1
    log("profiles dir: %s" % profile_mod.PROFILES_DIR)
    log("binary: %s (%s)" % (binary, "present" if binary_exists else "MISSING"))
    try:
        prof_init = build_profile(INITIATOR_NAME, INITIATOR_NUM, binary)
        prof_resp = build_profile(RESPONDER_NAME, RESPONDER_NUM, binary)
        cross_import(prof_init, prof_resp)
        set_roles(prof_init, prof_resp)

        # Persistence round-trip through profile.json.
        prof_init.save()
        reloaded = profile_mod.Profile.load(prof_init.name)
        res.check("profile.json round-trips name/number/endpoint/role",
                  reloaded.name == prof_init.name and reloaded.number == 1
                  and reloaded.endpoint_host == INITIATOR_HOST
                  and reloaded.endpoint_port == INITIATOR_ENDPOINT_PORT
                  and reloaded.is_initiator())

        if args.dry_run:
            run_dry_run(res, prof_init, prof_resp, binary_exists)
            exit_code = 0 if res.summary("DRY-RUN") else 1
        else:
            code = run_full(res, prof_init, prof_resp, binary_exists)
            if code:
                exit_code = code
            else:
                exit_code = 0 if res.summary("FULL") else 1
    except TestError as err:
        log("TEST ERROR: %s" % err)
        res.summary("FAILED")
        exit_code = 1
    except Exception:
        traceback.print_exc()
        res.summary("FAILED")
        exit_code = 1
    finally:
        _cleanup_procs()
        if exit_code == 0:
            shutil.rmtree(tmp, ignore_errors=True)
        else:
            log("artifacts kept in %s" % tmp)
    return exit_code


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        _cleanup_procs()
        sys.exit(1)
