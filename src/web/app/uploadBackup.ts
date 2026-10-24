import { apiMediaUrl, errorText } from "./api";
import i18n from "./i18n";
import { BackupItem } from "./types";

export type BackupUpload = {
  /** Resolves with the archive as listed. Rejects with an `AbortError` when cancelled. */
  done: Promise<BackupItem>;
  cancel: () => void;
};

/**
 * Send a site backup zip to `PUT /api/v1/backups/upload` as a raw body. `fetch` cannot report upload
 * progress, so this uses XMLHttpRequest. The server streams it to disk and checks the manifest.
 */
export function uploadBackup(file: File, onProgress: (sent: number, total: number) => void): BackupUpload {
  const xhr = new XMLHttpRequest();
  const done = new Promise<BackupItem>((resolve, reject) => {
    xhr.open("PUT", apiMediaUrl("/api/v1/backups/upload"));
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
      if (xhr.status >= 200 && xhr.status < 300) resolve(body as BackupItem);
      else reject(new Error(errorText(body, xhr.status)));
    };
    xhr.onerror = () => reject(new Error(i18n.t("common:networkError")));
    xhr.onabort = () => reject(Object.assign(new Error("aborted"), { name: "AbortError" }));
    xhr.send(file);
  });
  return { done, cancel: () => xhr.abort() };
}
