// Photos per load in the gallery (doc/web-ui.md § Page size). A choice for this browser, not the
// account, like the theme and the same choices as the mobile app. The server allows 1 to 200 per page.

export const PAGE_SIZES: readonly number[] = [50, 100, 200];
export const DEFAULT_PAGE_SIZE = 100;

export const PAGE_SIZE_KEY = "mosaicWave.pageSize";

export function isPageSize(value: unknown): value is number {
  return typeof value === "number" && PAGE_SIZES.includes(value);
}

/** The saved size, or the default when nothing valid is saved (or storage is blocked). */
export function storedPageSize(): number {
  try {
    const value = Number(window.localStorage.getItem(PAGE_SIZE_KEY));
    return isPageSize(value) ? value : DEFAULT_PAGE_SIZE;
  } catch {
    return DEFAULT_PAGE_SIZE;
  }
}

/** Remember `size` in this browser; the default is stored as no value. */
export function savePageSize(size: number): void {
  if (!isPageSize(size)) return;
  try {
    if (size === DEFAULT_PAGE_SIZE) window.localStorage.removeItem(PAGE_SIZE_KEY);
    else window.localStorage.setItem(PAGE_SIZE_KEY, String(size));
  } catch {
    /* storage blocked: the choice lasts until the page closes */
  }
}
