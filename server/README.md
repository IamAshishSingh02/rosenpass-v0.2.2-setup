# rosenpass key-drop server (TypeScript, Vercel)

The "make a request to the server to exchange keys" step of the demo: each
laptop POSTs its **public** keys to this service and GETs the peer's. Secrets
never leave the laptops — the rosenpass exchange itself still runs directly
between the two laptops over UDP.

## Deploy — Option A: Vercel CLI (fastest, ~3 min)

```sh
npm install -g vercel        # once per machine
vercel login                 # opens the browser; log in (create a free account if needed)

cd server
npm install
vercel                       # first run: creates the project — answer the prompts:
                             #   "Set up and deploy?"        -> Y
                             #   "Which scope?"              -> your account
                             #   "Link to existing project?" -> N
                             #   "Project name?"             -> rosenpass-keys
                             #   "In which directory?"       -> ./  (default, just Enter)
                             #   (it detects Vercel Node.js automatically)
vercel --prod                # promotes to production
```

Note the printed **Production URL** — `https://rosenpass-keys-<you>.vercel.app`.

## Deploy — Option B: GitHub + dashboard

1. Push the repo to GitHub
2. https://vercel.com/new → **Import** the repo
3. **Framework preset**: Vercel auto-detects Node.js; set **Root Directory** to `server`
4. **Deploy**

## Attach Blob storage (makes keys survive cold starts — do this either way)

1. Vercel dashboard → your project → **Storage** tab
2. **Create Database → Blob** → name it anything → **Create and Connect** (or "Connect Store" if one exists)
3. Accept when it says it will set `BLOB_READ_WRITE_TOKEN` → **Redeploy** the project once (Deployments → ⋯ → Redeploy) so the token takes effect

## Verify the deployment

```sh
BASE=https://rosenpass-keys-<you>.vercel.app

curl -i "$BASE/api/keys"                                   # -> {"peers":[]} + CORS header
curl -s -i -X OPTIONS "$BASE/api/keys" | head -n 5         # -> HTTP 204 + access-control-allow-origin: *
curl -X POST "$BASE/api/keys?name=test" \
  -H "content-type: application/json" \
  -d '{"rpPublicKey":"abc","wgPub":"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="}'
curl "$BASE/api/keys?name=test"                            # -> stored keys
curl -X DELETE "$BASE/api/keys?name=test"                  # clean up
```

The OPTIONS check is the CORS proof — any origin can call it.

## Troubleshooting

| symptom | fix |
|---|---|
| `BLOB_ERROR_ACCESS_DENIED` on POST | Blob store not connected — redo the Blob step, then **Redeploy** |
| keys vanish between requests | no Blob token set (memory fallback) — connect Blob and redeploy |
| `FUNCTION_PAYLOAD_TOO_LARGE` | shouldn't happen (2 MB cap ≫ 700 KB key) — check you're uploading the right file |
| wrong URL remembered by `main.ts` | delete `server/.rosenpass-server` and re-run with `--server <url>` |

## The demo flow (on each laptop, needs Node.js)

```sh
cd server
npx tsx main.ts upload laptop-1    # laptop-1: upload its public keys
npx tsx main.ts upload laptop-2    # laptop-2: same
npx tsx main.ts list               # verify both are registered
npx tsx main.ts fetch laptop-2     # on laptop-1: saves peer keys, ready for GUI "Choose file…"
```

`fetch` writes the decoded keys to `server/fetched-<name>/` and prints the
exact paths to import in the GUI. The base URL is `--server` (or `ROSENPASS_SERVER`
env var), asked once interactively and remembered in `.rosenpass-server`.

The same flow with plain curl (macOS Git-Bash-style):

```sh
BASE=https://<project>.vercel.app

curl -X POST "$BASE/api/keys?name=laptop-1" \
  -H "content-type: application/json" \
  -d "{\"rpPublicKey\":\"$(base64 -i rosenpass.public-key)\",\"wgPub\":\"$(cat wg.pub)\"}"

curl "$BASE/api/keys?name=laptop-2" | jq -r .rpPublicKey | base64 -d > laptop-2.rosenpass.public-key
curl "$BASE/api/keys?name=laptop-2" | jq -r .wgPub > laptop-2.wg.pub
```

## API

| route | what it does |
|---|---|
| `POST /api/keys?name=<n>` | store `{rpPublicKey, wgPub}` (base64) |
| `GET /api/keys` | list registered peers |
| `GET /api/keys?name=<n>` | fetch that peer's public keys |
| `DELETE /api/keys?name=<n>` | remove an entry (fresh demo run) |

## Local test

```sh
cd server && npm install && npx vercel dev
```

Setup for the demo laptops: install Node.js (`winget install OpenJS.NodeJS.LTS`
on Windows) — `main.ts` itself needs nothing else.
