# Demo runbook — rosenpass GUI clients (macOS / Windows), peer-to-peer
# with a standalone Vercel "key server" distributing the public keys

What the demo shows: public keys exchanged via the deployed key server,
post-quantum key exchange via rosenpass (direct, laptop-to-laptop), the PSK
auto-refreshing every ~2 minutes (visible in the GUI log), file transfer
through the WireGuard tunnel.

```
Laptop-1 (GUI, initiator)  <--- rosenpass UDP, direct --->  Laptop-2 (GUI, responder)
        \                                                          /
         \____ both talk to the KEY SERVER (Vercel, HTTPS only) __/
               upload/fetch public keys — secrets never touch it
```

Key inventory: the server has ONE rosenpass + ONE WireGuard keypair; each
client has its own. `server.pk` (rosenpass public key) is the only file
copied to every client.

---

## 1. Key server deployment (standalone, once — before the demo)

The key server is the TypeScript API in `server/`, deployed to Vercel —
it is completely standalone and knows nothing about the laptops. Full
instructions in `server/README.md`; the short version:

```sh
npm install -g vercel
vercel login
cd server && npm install
vercel                    # accept defaults
vercel --prod             # note the printed URL, e.g. https://rosenpass-keys-you.vercel.app
```

Then in the dashboard: **Storage → Create Database → Blob → Connect** and
**Redeploy** once (makes uploaded keys survive cold starts). Verify:

```sh
curl https://rosenpass-keys-you.vercel.app/api/keys   # -> {"peers":[]}
```

---

## 2. Client setup (mac + windows — same GUI flow)

1. Build rosenpass (`cargo build --release`) — see `gui/README.md` per-OS
   prerequisites — and start the GUI (`python -m rosenpass_gui` / `py -m rosenpass_gui`).
2. **Key server card**: paste the deployed link (e.g.
   `https://rosenpass-keys-<you>.vercel.app`) — the GUI itself uploads and
   fetches keys from it. Deployment of the server is completely standalone
   (see `server/README.md`); do it before the demo.
3. **Generate keys** → **Upload my keys** (posts this laptop's public keys
   to the server under the profile name).
4. Set **Peer name** to the other laptop's profile name → **Fetch peer keys**
   (pulls their public keys and imports them — no file copying).
5. Set **Endpoint host** = the initiator needs the *responder's* IP (same
   WiFi: the responder's LAN IP; port `10002` for machine number 2) and
   **Peer WG IP** accordingly (`10.0.0.1/32` if peer is number 1).
6. **Start exchange** → within seconds the log shows
   `output-key peer … exchanged`, status turns green.
7. **Enable WireGuard** (one admin/UAC prompt) → **Apply refreshed key**
   whenever the log prints a new exchange (~every 2 min) — this is the
   "keys being exchanged" moment to narrate.

## 3. Prove the tunnel

Files travel between the laptops over the VPN. On **laptop-2** (responder),
serve a folder on its tunnel IP:

```sh
mkdir -p /tmp/demo && echo "hello from inside the VPN" > /tmp/demo/hello.txt
cd /tmp/demo
# macOS:                          # Windows (Git Bash / PowerShell):
python3 -m http.server 80 --bind 10.0.0.2
#   ^ Windows: py -m http.server 80 --bind 10.0.0.2
```

On **laptop-1** (initiator):

```
curl http://10.0.0.2/hello.txt     → hello from inside the VPN
wg show                            → handshake + transfer counters grow
```

The 2-minute refresh is visible in both GUI logs (`output-key … exchanged`
lines) and in `wg show` (latest handshake / transfer counters updating).
