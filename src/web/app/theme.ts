// Light / dark theme (doc/web-ui.md § Theme). A choice for this browser, not the account: a phone
// in the sun and a desktop at night want different themes. Dark is the default.

export type ThemeChoice = "dark" | "light" | "system";
export const THEME_CHOICES: readonly ThemeChoice[] = ["dark", "light", "system"];

export const THEME_KEY = "mosaicWave.theme";

/**
 * Runs in <head> before the first paint (layout.tsx), so a light-theme user never sees a dark flash.
 * Keep it in step with `resolveTheme` below.
 */
export const THEME_BOOT_SCRIPT = `try{var c=localStorage.getItem("${THEME_KEY}");var l=c==="light"||(c==="system"&&matchMedia("(prefers-color-scheme: light)").matches);document.documentElement.dataset.theme=l?"light":"dark"}catch(e){}`;

export function isThemeChoice(value: unknown): value is ThemeChoice {
  return typeof value === "string" && (THEME_CHOICES as readonly string[]).includes(value);
}

export function storedTheme(): ThemeChoice {
  try {
    const value = window.localStorage.getItem(THEME_KEY);
    return isThemeChoice(value) ? value : "dark";
  } catch {
    return "dark"; // storage blocked: the default
  }
}

/** The theme the page shows for a choice: "system" follows the device's light/dark setting. */
export function resolveTheme(choice: ThemeChoice, systemIsLight: boolean): "dark" | "light" {
  return choice === "light" || (choice === "system" && systemIsLight) ? "light" : "dark";
}

function systemQuery(): MediaQueryList | null {
  return typeof window.matchMedia === "function" ? window.matchMedia("(prefers-color-scheme: light)") : null;
}

/** Show the theme for `choice` now; remember it when `save` is set. */
export function applyTheme(choice: ThemeChoice, save = false): void {
  document.documentElement.dataset.theme = resolveTheme(choice, systemQuery()?.matches ?? false);
  if (!save) return;
  try {
    if (choice === "dark") window.localStorage.removeItem(THEME_KEY);
    else window.localStorage.setItem(THEME_KEY, choice);
  } catch {
    /* storage blocked: the choice lasts until the page closes */
  }
}

/** While the choice is "system", follow the device when it flips. Returns the stop function. */
export function followSystemTheme(): () => void {
  const query = systemQuery();
  if (!query) return () => {};
  const onChange = () => {
    if (storedTheme() === "system") applyTheme("system");
  };
  query.addEventListener("change", onChange);
  return () => query.removeEventListener("change", onChange);
}
