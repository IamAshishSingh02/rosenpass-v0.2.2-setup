/**
 * main.ts — demo helper: exchange public keys with the rosenpass key server.
 *
 * Run this on each demo laptop (needs Node.js; run with `npx tsx` or compile):
 *
 *   npx tsx main.ts upload laptop-1     # upload this laptop's public keys
 *   npx tsx main.ts fetch laptop-2      # fetch the peer's keys for the GUI
 *   npx tsx main.ts list                # who has registered
 *   npx tsx main.ts wipe                # fresh start (wipes every entry)
 *
 * The server base URL comes from --server, ROSENPASS_SERVER, or is asked for
 * interactively on first use (then remembered in .rosenpass-server).
 */
import * as fs from "node:fs";
import * as path from "node:path";
import * as readline from "node:readline";
import { homedir } from "node:os";

const HERE = path.dirname(path.resolve(import.meta.path ?? __filename));
const SERVER_FILE = path.join(HERE, ".rosenpass-server");
// where the GUI keeps its key files
const PROFILE_ROOT = path.join(homedir(), ".rosenpass-gui", "profiles");

interface StoredKeys {
  rpPublicKey: string; // base64 of the whole rosenpass.public-key file
  wgPub: string; // 44-char base64 WireGuard public key
  uploadedAt?: string;
}

// CORS on the server allows any origin, so browser clients work too —
// this CLI just uses plain fetch.
async function request(
  base: string,
  method: string,
  apiPath: string,
  payload?: unknown,
): Promise<Record<string, unknown>> {
  const res = await fetch(base + apiPath, {
    method,
    headers: payload ? { "content-type": "application/json" } : {},
    body: payload ? JSON.stringify(payload) : undefined,
  });
  const text = await res.text();
  if (!res.ok) {
    throw new Error(`server error ${res.status}: ${text || res.statusText}`);
  }
  return text ? (JSON.parse(text) as Record<string, unknown>) : {};
}

async function getBase(args: { server?: string }): Promise<string> {
  let base = (args.server ?? process.env.ROSENPASS_SERVER ?? "").trim();
  if (!base && fs.existsSync(SERVER_FILE)) {
    base = fs.readFileSync(SERVER_FILE, "utf8").trim();
  }
  if (!base) {
    const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
    const answer = await new Promise<string>((resolve) =>
      rl.question("Key server base URL (e.g. https://rosenpass-keys.vercel.app): ", resolve),
    );
    rl.close();
    base = answer.trim();
  }
  base = base.replace(/\/+$/, "");
  if (base && !base.startsWith("http://") && !base.startsWith("https://")) {
    base = "https://" + base;
  }
  fs.writeFileSync(SERVER_FILE, base + "\n");
  return base;
}

async function upload(base: string, name: string): Promise<void> {
  const prof = path.join(PROFILE_ROOT, name);
  const rp = path.join(prof, "rosenpass.public-key");
  const wg = path.join(prof, "wg.pub");
  for (const f of [rp, wg]) {
    if (!fs.existsSync(f)) {
      throw new Error(
        `${f} not found — open the GUI, set profile name '${name}', Generate keys first.`,
      );
    }
  }
  const rpB64 = fs.readFileSync(rp).toString("base64");
  const wgB64 = fs.readFileSync(wg, "utf8").trim();
  const out = await request(base, "POST", `/api/keys?name=${name}`, {
    rpPublicKey: rpB64,
    wgPub: wgB64,
  });
  console.log(`uploaded keys for '${name}' (server allows CORS *, so browsers work too)`);
  console.log(`  uploaded at: ${out.uploadedAt ?? "?"}`);
}

async function fetchPeer(base: string, name: string): Promise<void> {
  const out = (await request(base, "GET", `/api/keys?name=${name}`)) as StoredKeys;
  const outDir = path.join(HERE, `fetched-${name}`);
  fs.mkdirSync(outDir, { recursive: true });
  const rp = path.join(outDir, `${name}.rosenpass.public-key`);
  const wg = path.join(outDir, `${name}.wg.pub`);
  fs.writeFileSync(rp, Buffer.from(out.rpPublicKey, "base64"));
  fs.writeFileSync(wg, out.wgPub.trim() + "\n");
  console.log(`peer '${name}' keys saved — import these in the GUI (Choose file…):`);
  console.log(`  Peer rosenpass key : ${rp}`);
  console.log(`  Peer WireGuard key : ${wg}`);
}

async function listPeers(base: string): Promise<void> {
  const out = await request(base, "GET", "/api/keys");
  const peers = (out.peers as string[]) ?? [];
  console.log(peers.length ? peers.join("\n") : "no peers registered yet");
}

async function wipe(base: string): Promise<void> {
  const out = await request(base, "GET", "/api/keys");
  const peers = (out.peers as string[]) ?? [];
  for (const p of peers) {
    await request(base, "DELETE", `/api/keys?name=${p}`);
    console.log(`deleted ${p}`);
  }
  console.log(peers.length ? "server clean" : "nothing to delete");
}

async function main(): Promise<void> {
  const argv = process.argv.slice(2);
  let cmd: string | undefined;
  let name: string | undefined;
  let server: string | undefined;
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === "--server") server = argv[++i];
    else if (!cmd) cmd = argv[i];
    else if (!name) name = argv[i];
  }

  switch (cmd) {
    case "upload":
    case "fetch":
      if (!name) throw new Error(`usage: main.ts ${cmd} <peer-name> [--server <url>]`);
      break;
    case "list":
    case "wipe":
      break;
    default:
      console.error(
        "usage: npx tsx main.ts <upload|fetch|list|wipe> [peer-name] [--server <url>]\n" +
          "  upload <name>  upload this laptop's public keys (after GUI 'Generate keys')\n" +
          "  fetch <name>   fetch the peer's keys, ready for the GUI's Choose file…\n" +
          "  list           show registered peers\n" +
          "  wipe           delete every registered peer (fresh demo run)",
      );
      process.exit(1);
  }

  const base = await getBase({ server });
  console.log(`server: ${base}`);
  if (cmd === "upload") await upload(base, name!);
  else if (cmd === "fetch") await fetchPeer(base, name!);
  else if (cmd === "list") await listPeers(base);
  else if (cmd === "wipe") await wipe(base);
}

main().catch((err: unknown) => {
  console.error(String(err instanceof Error ? err.message : err));
  process.exit(1);
});
