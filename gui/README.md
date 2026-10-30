# Rosenpass macOS GUI

A Tkinter (standard-library-only) frontend for running [Rosenpass](../readme.md)
on macOS **and Windows**: generate keys, run the post-quantum key exchange
between two peers with live status, and hand the resulting preshared key to
WireGuard.

The rosenpass binary itself is built from this repository; the GUI just drives
it. (The `rp` frontend and the netlink broker are Linux-only and do not run on
macOS or Windows — that is why this GUI exists.)

## Prerequisites — macOS

```sh
brew install cmake wireguard-tools pkg-config libsodium
cargo build --release -p rosenpass   # rustup picks up the pinned toolchain
```

## Prerequisites — Windows

1. [Rust](https://rustup.rs) (MSVC toolchain) + **Visual Studio Build Tools**
   (C++ workload) + **CMake**
2. Build: `cargo build --release` → `target\release\rosenpass.exe`
   (libsodium and liboqs build from bundled source; no pkg-config needed)
3. Install the [official WireGuard app](https://wireguard.com/install) —
   it provides `wg.exe` and `wireguard.exe` (tunnel service manager)
4. Python 3.10+ with Tk (the standard python.org installer includes it)

Run: `py -m rosenpass_gui` from the `gui/` directory.

## Run (macOS)

Note: use a Python with a modern Tk. Apple's CommandLineTools Python 3.9
ships Tk 8.5, which renders **blank windows** on recent macOS — the venv here
is built from Homebrew's `python3.13` (`brew install python-tk@3.13`).

```sh
cd gui
/opt/homebrew/bin/python3.13 -m venv .venv   # venv optional — stdlib only
.venv/bin/python -m rosenpass_gui            # fresh window
.venv/bin/python -m rosenpass_gui laptop-1   # preselect profile "laptop-1"
```

One window drives one peer. For a loopback demo on a single Mac, run the GUI
twice (e.g. profiles `laptop-1` and `laptop-2`).

Profiles live in `~/.rosenpass-gui/profiles/<name>/` and contain the machine
number, endpoints, rosenpass keypair, WireGuard keypair, the exchanged
preshared key (`psk.out`), the peer's imported public keys, and the generated
`<name>.conf`.

Derived addressing (from the machine number *n*):

| what | value |
| --- | --- |
| rosenpass listen port | `10000 + n` |
| WireGuard listen port | `51820 + n` |
| WireGuard IP | `10.0.0.n/24` |

Number 1 is the initiator by default (it carries the `endpoint` token);
override with the role dropdown.

## Walkthrough — loopback on one Mac

1. **Window A**: name `laptop-1`, number `1` → **Generate keys**.
2. **Window B**: name `laptop-2`, number `2` → **Generate keys**.
3. Cross-import the peer keys — in each window, *Choose file…* next to
   *Peer rosenpass key* and *Peer WG key*, picking the *other* profile's
   `rosenpass.public-key` and `wg.pub`.
4. Set endpoint hosts: for loopback, `127.0.0.1` on both sides
   (endpoint ports default to the peer's rosenpass port).
5. **Start exchange** in both windows. Within a few seconds both logs print
   `output-key peer … key-file "…/psk.out" exchanged`, the status dot turns
   green (KEYS EXCHANGED), and both `psk.out` files are identical.
6. Rosenpass refreshes the PSK roughly every 2 minutes; each refresh prints a
   new `exchanged` line and updates the status detail. Use **Apply refreshed
   key** to push the newest PSK into the live tunnel (one admin prompt; no
   auto-apply, so you are not prompted every two minutes).
7. **Enable WireGuard** in both windows (one admin prompt each), then check
   `wg show` — handshakes and transfer counters on both peers are the
   authoritative success signal.
8. **Stop** ends the daemon (`pgrep rosenpass` should come back empty);
   **Disable WireGuard** tears the tunnel down.

Ping caveat: on a single Mac both configs route `10.0.0.0/24` through two
utun interfaces, so `ping 10.0.0.2` may be inconclusive. The `wg show`
handshake counters are the loopback success signal. On two real Macs, set the
endpoint to the peer's LAN IP and `ping 10.0.0.<peer>` must succeed.

## Walkthrough — two Macs

1. On each Mac: build rosenpass, install `wireguard-tools`, run the GUI.
2. Create profiles with distinct numbers (e.g. 1 and 2). Number 1 initiates.
3. Set each window's endpoint host to the *peer's* LAN IP (port defaults are
   already correct).
4. Exchange the four public files (rosenpass public key + wg.pub, both
   directions) — AirDrop, email, shared drive, whatever you trust.
5. Import them with *Choose file…*, **Generate keys** → **Start exchange** →
   **Enable WireGuard** on both, then `ping 10.0.0.<peer>`.

## Security notes

- Secret keys (`rosenpass.secret-key`, `wg.key`) sit unencrypted in
  `~/.rosenpass-gui/profiles/` — keep that directory private.
- Bringing the tunnel up/down and applying refreshed keys run through
  `osascript … with administrator privileges` (macOS) or a UAC prompt
  (Windows); the OS will prompt each time.
- `psk.out` is a live WireGuard preshared key. Treat it like a secret.
