// Emit precompressed .gz + .br siblings for every compressible dist asset, so
// the Python server (pycanvas/server.py: _FrontendStatic) can serve the
// already-compressed bytes with Content-Encoding instead of shipping the raw
// ~7 MB bundle uncompressed. Runs as part of `npm run build`, so the variants
// can never drift from the assets they mirror.
//
// Both encodings are kept on purpose: browsers only advertise `br` over HTTPS
// (the tunnel), but send `gzip` over plain HTTP (the local/LAN bind, the common
// case) — so gzip covers local serving and brotli wins on the tunnel, where
// bandwidth matters most. Compression is build-time only; serving a .gz/.br
// file at runtime needs no Python dependency.
import { readdirSync, statSync, readFileSync, writeFileSync } from "node:fs";
import { join, extname } from "node:path";
import { fileURLToPath } from "node:url";
import { gzipSync, brotliCompressSync, constants } from "node:zlib";

const DIST = fileURLToPath(new URL("../dist/", import.meta.url));
const COMPRESSIBLE = new Set([".js", ".css", ".html", ".svg", ".json", ".map", ".ttf"]);
const MIN_BYTES = 1024; // below this the separate file isn't worth the request

function walk(dir) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    const s = statSync(p);
    if (s.isDirectory()) { walk(p); continue; }
    if (name.endsWith(".gz") || name.endsWith(".br")) continue;
    if (!COMPRESSIBLE.has(extname(name)) || s.size < MIN_BYTES) continue;
    const buf = readFileSync(p);
    writeFileSync(p + ".gz", gzipSync(buf, { level: 9 }));
    writeFileSync(p + ".br", brotliCompressSync(buf, {
      params: {
        [constants.BROTLI_PARAM_QUALITY]: 11,
        [constants.BROTLI_PARAM_SIZE_HINT]: buf.length,
      },
    }));
  }
}

walk(DIST);
console.log("precompressed dist assets (.gz + .br)");
