import { apiMediaUrl, errorText } from "./api";
import i18n from "./i18n";
import { BackupItem } from "./types";

export type BackupUpload<T = BackupItem> = {
  /** Resolves with the archive as listed. Rejects with an `AbortError` when cancelled. */
  done: Promise<T>;
  cancel: () => void;
};

/**
 * Send a backup zip as a raw body: a site backup to `PUT /api/v1/backups/upload` (admin), or a
 * library backup to `PUT /api/v1/me/backups/upload`. `fetch` cannot report upload progress, so this
 * uses XMLHttpRequest. The server streams it to disk and checks the manifest.
 */
export function uploadBackup<T = BackupItem>(
  file: File,
  onProgress: (sent: number, total: number) => void,
  url = "/api/v1/backups/upload",
): BackupUpload<T> {
  const xhr = new XMLHttpRequest();
  const done = new Promise<T>((resolve, reject) => {
    // The name matters for a big file of a split backup (…-NNN.big): the server keeps it as it is.
    xhr.open("PUT", apiMediaUrl(`${url}?name=${encodeURIComponent(file.name)}`));
    xhr.withCredentials = true;
    xhr.setRequestHeader("Content-Type", "application/octet-stream");
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(event.loaded, event.total);
    };
    xhr.onload = () => {
      let body: unknown = null;
      try {
        body = JSON.parse(xhr.responseText);
      } catch {
        body = null;
      }
      if (xhr.status >= 200 && xhr.status < 300) resolve(body as T);
      else reject(new Error(errorText(body, xhr.status)));
    };
    xhr.onerror = () => reject(new Error(i18n.t("common:networkError")));
    xhr.onabort = () => reject(Object.assign(new Error("aborted"), { name: "AbortError" }));
    xhr.send(file);
  });
  return { done, cancel: () => xhr.abort() };
}
