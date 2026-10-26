// Writes the server's error catalog from the web's errors.json files (doc/i18n.md § API language).
//   node scripts/sync-server-locales.mjs          write src/server/mosaicwave/locales/errors.<locale>.json
//   node scripts/sync-server-locales.mjs --check  exit 1 when a committed copy is stale (i18n-check)
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const localesDir = join(here, "..", "app", "locales");
const outDir = join(here, "..", "..", "server", "mosaicwave", "locales");

export const LOCALES = ["en", "zh-Hant", "zh-Hans"];

function flatten(obj, prefix = "", out = {}) {
  for (const [key, value] of Object.entries(obj)) {
    const path = prefix ? `${prefix}.${key}` : key;
    if (value && typeof value === "object") flatten(value, path, out);
    else out[path] = value;
  }
  return out;
}

/** locale → the text the server writes for that locale (keys sorted, LF, trailing newline). */
export function render(locale) {
  const source = JSON.parse(readFileSync(join(localesDir, locale, "errors.json"), "utf8"));
  const flat = flatten(source);
  const sorted = Object.fromEntries(Object.keys(flat).sort().map((key) => [key, flat[key]]));
  return `${JSON.stringify(sorted, null, 2)}\n`;
}

export function outPath(locale) {
  return join(outDir, `errors.${locale}.json`);
}

/** Names of the locales whose committed server copy differs from the web source. */
export function stale() {
  return LOCALES.filter((locale) => {
    try {
      return readFileSync(outPath(locale), "utf8").replace(/\r\n/g, "\n") !== render(locale);
    } catch {
      return true;
    }
  });
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  if (process.argv.includes("--check")) {
    const names = stale();
    if (names.length) {
      console.error(`server locales stale: ${names.join(", ")} (run npm run sync-locales)`);
      process.exit(1);
    }
    console.log("server locales up to date");
  } else {
    mkdirSync(outDir, { recursive: true });
    for (const locale of LOCALES) writeFileSync(outPath(locale), render(locale), "utf8");
    console.log(`wrote ${LOCALES.length} catalogs to ${outDir}`);
  }
}
