// Fails when zh-Hant / zh-Hans drift from the English source (doc/i18n.md).
// Also fails when a server error or Takeout warning code has no errors:{code} entry, and when the
// server's generated catalog copy (src/server/mosaicwave/locales) is stale.
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { stale as serverCopyStale } from "./sync-server-locales.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const localesDir = join(here, "..", "app", "locales");
const serverDir = join(here, "..", "..", "server", "mosaicwave");
const SOURCE = "en";
const LOCALES = ["zh-Hant", "zh-Hans"];
const PLURAL = /_(zero|one|two|few|many|other)$/;

const problems = [];

function load(locale, file) {
  try {
    return JSON.parse(readFileSync(join(localesDir, locale, file), "utf8"));
  } catch (err) {
    problems.push(`${locale}/${file}: ${err.code === "ENOENT" ? "missing file" : err.message}`);
    return null;
  }
}

function flatten(obj, prefix = "", out = new Map()) {
  for (const [key, value] of Object.entries(obj)) {
    const path = prefix ? `${prefix}.${key}` : key;
    if (value && typeof value === "object") flatten(value, path, out);
    else out.set(path, value);
  }
  return out;
}

function placeholders(text) {
  return [...String(text).matchAll(/\{\{\s*([\w.]+)[^}]*\}\}/g)].map((m) => m[1]).sort().join(",");
}

/** base key → value used for comparison (plural keys compare their `_other` form). */
function shape(entries) {
  const plain = new Map();
  const plural = new Map();
  for (const [key, value] of entries) {
    const m = key.match(PLURAL);
    if (m) {
      const base = key.slice(0, -m[0].length);
      if (!plural.has(base)) plural.set(base, new Map());
      plural.get(base).set(m[1], value);
    } else {
      plain.set(key, value);
    }
  }
  return { plain, plural };
}

const files = readdirSync(join(localesDir, SOURCE)).filter((f) => f.endsWith(".json"));
const sources = {};

for (const file of files) {
  const en = load(SOURCE, file);
  if (!en) continue;
  const ns = file.replace(/\.json$/, "");
  const enEntries = flatten(en);
  sources[ns] = enEntries;
  const ref = shape(enEntries);
  for (const [key, value] of enEntries) {
    if (typeof value !== "string" || !value.trim()) problems.push(`${SOURCE}/${file}: empty ${key}`);
  }
  for (const [base, forms] of ref.plural) {
    if (!forms.has("other")) problems.push(`${SOURCE}/${file}: ${base} has no _other form`);
  }

  for (const locale of LOCALES) {
    const data = load(locale, file);
    if (!data) continue;
    const entries = flatten(data);
    const got = shape(entries);
    const where = `${locale}/${file}`;
    for (const [key, value] of entries) {
      if (typeof value !== "string" || !value.trim()) problems.push(`${where}: empty ${key}`);
    }
    for (const [key, value] of ref.plain) {
      if (!got.plain.has(key)) {
        problems.push(`${where}: missing ${key}`);
      } else if (placeholders(value) !== placeholders(got.plain.get(key))) {
        problems.push(`${where}: ${key} placeholders differ from en`);
      }
    }
    for (const key of got.plain.keys()) {
      if (!ref.plain.has(key)) problems.push(`${where}: extra ${key}`);
    }
    for (const [base, forms] of ref.plural) {
      const theirs = got.plural.get(base);
      if (!theirs?.has("other")) {
        problems.push(`${where}: missing ${base}_other`);
      } else if (placeholders(forms.get("other")) !== placeholders(theirs.get("other"))) {
        problems.push(`${where}: ${base}_other placeholders differ from en`);
      }
    }
    for (const base of got.plural.keys()) {
      if (!ref.plural.has(base)) problems.push(`${where}: extra plural ${base}`);
    }
  }
}

function pythonFiles(dir) {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) return pythonFiles(path);
    return entry.name.endsWith(".py") ? [path] : [];
  });
}

const CODE_PATTERNS = [
  /ApiError\(\s*\d+\s*,\s*"([a-z_]+)"/g,
  /wrapped\(\s*\d+\s*,\s*"([a-z_]+)"/g,
  /\bcode="([a-z_]+)"/g,
  /code: str = "([a-z_]+)"/g,
  /(?:ImportBusy|AlbumConflict)\([^()]*,\s*"([a-z_]+)"\)/g,
  /^\s+"[^"\n]+":\s*"([a-z][a-z_]+)",?$/gm,
];

const errorKeys = new Set(
  [...(sources.errors?.keys() ?? [])].map((key) => key.replace(PLURAL, "")),
);
let serverCodes = 0;
try {
  for (const file of pythonFiles(serverDir)) {
    const text = readFileSync(file, "utf8");
    for (const pattern of CODE_PATTERNS) {
      for (const match of text.matchAll(pattern)) {
        if (pattern === CODE_PATTERNS[5] && !/_CODES = \{/.test(text)) continue;
        serverCodes += 1;
        if (!errorKeys.has(match[1])) {
          problems.push(`errors.json: no entry for server code ${match[1]} (${file.slice(serverDir.length + 1)})`);
        }
      }
    }
  }
} catch (err) {
  if (err.code !== "ENOENT") throw err;
}

for (const locale of serverCopyStale()) {
  problems.push(
    `server/mosaicwave/locales/errors.${locale}.json is stale or missing (run npm run sync-locales)`,
  );
}

if (problems.length) {
  console.error(`i18n-check: ${problems.length} problem(s)`);
  for (const line of [...new Set(problems)]) console.error(`  ${line}`);
  process.exit(1);
}
console.log(
  `i18n-check: ${files.length} namespaces × ${LOCALES.length + 1} locales OK; ${serverCodes} server codes covered`,
);
