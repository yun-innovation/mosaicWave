import i18n from "i18next";
import { initReactI18next } from "react-i18next";

import enAuth from "./locales/en/auth.json";
import enBackup from "./locales/en/backup.json";
import enCommon from "./locales/en/common.json";
import enErrors from "./locales/en/errors.json";
import enGallery from "./locales/en/gallery.json";
import enImport from "./locales/en/import.json";
import enSettings from "./locales/en/settings.json";
import hansAuth from "./locales/zh-Hans/auth.json";
import hansBackup from "./locales/zh-Hans/backup.json";
import hansCommon from "./locales/zh-Hans/common.json";
import hansErrors from "./locales/zh-Hans/errors.json";
import hansGallery from "./locales/zh-Hans/gallery.json";
import hansImport from "./locales/zh-Hans/import.json";
import hansSettings from "./locales/zh-Hans/settings.json";
import hantAuth from "./locales/zh-Hant/auth.json";
import hantBackup from "./locales/zh-Hant/backup.json";
import hantCommon from "./locales/zh-Hant/common.json";
import hantErrors from "./locales/zh-Hant/errors.json";
import hantGallery from "./locales/zh-Hant/gallery.json";
import hantImport from "./locales/zh-Hant/import.json";
import hantSettings from "./locales/zh-Hant/settings.json";

export const LOCALES = ["en", "zh-Hant", "zh-Hans"] as const;
export type Locale = (typeof LOCALES)[number];

/** Native names: shown the same in every UI language. */
export const LOCALE_NAMES: Record<Locale, string> = {
  en: "English",
  "zh-Hant": "繁體中文",
  "zh-Hans": "简体中文",
};

export const defaultNS = "common";

export const resources = {
  en: {
    common: enCommon,
    auth: enAuth,
    backup: enBackup,
    gallery: enGallery,
    import: enImport,
    settings: enSettings,
    errors: enErrors,
  },
  "zh-Hant": {
    common: hantCommon,
    auth: hantAuth,
    backup: hantBackup,
    gallery: hantGallery,
    import: hantImport,
    settings: hantSettings,
    errors: hantErrors,
  },
  "zh-Hans": {
    common: hansCommon,
    auth: hansAuth,
    backup: hansBackup,
    gallery: hansGallery,
    import: hansImport,
    settings: hansSettings,
    errors: hansErrors,
  },
} as const;

const STORAGE_KEY = "mosaicWave.locale";

void i18n.use(initReactI18next).init({
  resources,
  // The static export is rendered in English; LocaleBoot switches after hydration.
  lng: "en",
  fallbackLng: "en",
  supportedLngs: [...LOCALES],
  ns: Object.keys(resources.en),
  defaultNS,
  interpolation: { escapeValue: false },
  initAsync: false,
});

export function isLocale(value: unknown): value is Locale {
  return typeof value === "string" && (LOCALES as readonly string[]).includes(value);
}

/** Map a BCP 47 browser tag to a supported locale (see doc/i18n.md). */
export function matchLocale(tag: string): Locale | null {
  const lower = tag.trim().toLowerCase();
  if (lower === "en" || lower.startsWith("en-")) return "en";
  if (lower !== "zh" && !lower.startsWith("zh-")) return null;
  if (lower.includes("-hant") || /^zh-(hk|tw|mo)(-|$)/.test(lower)) return "zh-Hant";
  return "zh-Hans";
}

export function browserLocale(): Locale {
  if (typeof navigator === "undefined") return "en";
  const tags = navigator.languages?.length ? navigator.languages : [navigator.language];
  for (const tag of tags) {
    const match = tag ? matchLocale(tag) : null;
    if (match) return match;
  }
  return "en";
}

export function storedLocale(): Locale | null {
  try {
    const value = window.localStorage.getItem(STORAGE_KEY);
    return isLocale(value) ? value : null;
  } catch {
    return null;
  }
}

/** Remember an explicit choice in this browser; `null` means Automatic. */
export function rememberLocale(choice: Locale | null): void {
  try {
    if (choice) window.localStorage.setItem(STORAGE_KEY, choice);
    else window.localStorage.removeItem(STORAGE_KEY);
  } catch {
    // Private mode or storage disabled: the server copy still applies after sign-in.
  }
}

/** user.locale, then this browser's last choice, then navigator.languages, then en. */
export function resolveLocale(saved?: string | null): Locale {
  if (isLocale(saved)) return saved;
  return storedLocale() ?? browserLocale();
}

export async function applyLocale(locale: Locale): Promise<void> {
  if (i18n.language !== locale) await i18n.changeLanguage(locale);
  document.documentElement.lang = locale;
}

export function activeLocale(): Locale {
  return isLocale(i18n.language) ? i18n.language : "en";
}

export default i18n;
