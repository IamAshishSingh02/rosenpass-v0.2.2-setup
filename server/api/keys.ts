/**
 * rosenpass key-drop server — the "exchange keys via a server" step of the demo.
 *
 * Laptops POST their *public* keys here; peers GET them. Secret keys never
 * touch this server. The actual rosenpass key exchange happens directly
 * between the two laptops over UDP — this service only distributes the
 * public key files (rosenpass public-key + WireGuard wg.pub).
 *
 * Routes (all under /api/keys):
 *   POST /api/keys?name=laptop-1     body: JSON { rpPublicKey, wgPub }
 *                                    (base64 strings; the rosenpass public
 *                                    key is ~524 KB — send it as base64 of
 *                                    the whole file, exactly what the GUI's
 *                                    "paste" path accepts)
 *   GET  /api/keys                   -> { peers: ["laptop-1", ...] }
 *   GET  /api/keys?name=laptop-1     -> { rpPublicKey, wgPub, uploadedAt }
 *   DELETE /api/keys?name=laptop-1   -> removes the entry (re-runs of demo)
 *
 * Storage: Vercel Blob when BLOB_READ_WRITE_TOKEN is configured (survives
 * cold starts); otherwise an in-memory map, which works for a single warm
 * demo session but resets on cold start — configure the token for anything
 * you care about.
 */
import type { VercelRequest, VercelResponse } from "@vercel/node";
import { put, list, head } from "@vercel/blob";

interface StoredKeys {
  rpPublicKey: string; // base64 of the peer's whole rosenpass.public-key file
  wgPub: string; // base64 WireGuard public key (44 chars)
  uploadedAt: string;
}

const MAX_BODY = 2 * 1024 * 1024; // 2 MB — plenty for a 524 KB key in base64

// In-memory fallback (single warm instance only).
const memory = new Map<string, StoredKeys>();

const blobEnabled = () => !!process.env.BLOB_READ_WRITE_TOKEN;
const key = (name: string) => `peers/${name}.json`;

export default async function handler(req: VercelRequest, res: VercelResponse) {
  // CORS: allow any origin/method so the demo laptops (and any tool) can
  // call this API — from browsers too. Vercel.json sets the headers on
  // normal responses; preflights must be answered here.
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader(
    "Access-Control-Allow-Methods",
    "GET, POST, DELETE, OPTIONS",
  );
  res.setHeader(
    "Access-Control-Allow-Headers",
    "content-type, authorization",
  );
  if (req.method === "OPTIONS") {
    return res.status(204).end();
  }
  try {
    switch (req.method) {
      case "POST":
        return await handlePost(req, res);
      case "GET":
        return await handleGet(req, res);
      case "DELETE":
        return await handleDelete(req, res);
      default:
        res.setHeader("Allow", "GET, POST, DELETE, OPTIONS");
        return res.status(405).json({ error: "method not allowed" });
    }
  } catch (err) {
    return res.status(500).json({ error: String(err) });
  }
}

// -- POST ---------------------------------------------------------------------

async function handlePost(req: VercelRequest, res: VercelResponse) {
  const name = String(req.query.name ?? "").trim();
  if (!name || !/^[A-Za-z0-9._-]{1,64}$/.test(name)) {
    return res
      .status(400)
      .json({ error: "missing/invalid ?name= (letters, digits, . _ - only)" });
  }
  const body = req.body ?? {};
  const { rpPublicKey, wgPub } = body as Partial<StoredKeys>;
  if (typeof rpPublicKey !== "string" || rpPublicKey.length === 0) {
    return res.status(400).json({ error: "rpPublicKey (base64) required" });
  }
  if (typeof wgPub !== "string" || wgPub.length > 128) {
    return res.status(400).json({ error: "wgPub (44-char base64) required" });
  }
  const contentLength = Number(req.headers["content-length"] ?? 0);
  if (contentLength > MAX_BODY) {
    return res.status(413).json({ error: "body too large" });
  }

  const record: StoredKeys = {
    rpPublicKey,
    wgPub,
    uploadedAt: new Date().toISOString(),
  };

  if (blobEnabled()) {
    await put(key(name), JSON.stringify(record), {
      access: "public",
      addRandomSuffix: false,
    });
  } else {
    memory.set(name, record);
  }
  return res.status(201).json({ ok: true, name, uploadedAt: record.uploadedAt });
}

// -- GET ----------------------------------------------------------------------

async function handleGet(req: VercelRequest, res: VercelResponse) {
  const name = req.query.name ? String(req.query.name).trim() : "";
  if (!name) {
    // list who has registered
    if (blobEnabled()) {
      const { blobs } = await list({ prefix: "peers/" });
      const peers = blobs.map((b) => b.pathname.replace("peers/", "").replace(/\.json$/, ""));
      return res.json({ peers });
    }
    return res.json({ peers: [...memory.keys()].sort() });
  }

  const record = blobEnabled()
    ? await readBlob(name)
    : (memory.get(name) ?? null);
  if (!record) {
    return res.status(404).json({ error: `no keys uploaded for "${name}"` });
  }
  return res.json({ name, ...record });
}

async function readBlob(name: string): Promise<StoredKeys | null> {
  try {
    const meta = await head(key(name));
    const r = await fetch(meta.url);
    return (await r.json()) as StoredKeys;
  } catch {
    return null; // head() throws BlobAccessError/BlobNotFoundError when absent
  }
}

// -- DELETE -------------------------------------------------------------------

async function handleDelete(req: VercelRequest, res: VercelResponse) {
  const name = String(req.query.name ?? "").trim();
  if (!name) return res.status(400).json({ error: "missing ?name=" });
  if (blobEnabled()) {
    const { del } = await import("@vercel/blob");
    try {
      await del(key(name));
    } catch {
      /* deleting a missing blob is fine */
    }
  }
  memory.delete(name);
  return res.json({ ok: true });
}
