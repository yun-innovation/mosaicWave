"use client";

import { useState } from "react";
import { useTranslation } from "react-i18next";

import { apiFetch, errorText } from "./api";
import i18n from "./i18n";

type Props = {
  jobId: string;
  kind: string | null;
  /** The server already has a stop request for this job (from another tab or an admin). */
  cancelling?: boolean;
  /** Called after the server accepted the request, so the caller can look at its jobs again. */
  onRequested?: () => void;
};

type Keeps =
  | "takeout_import"
  | "thumb_batch"
  | "site_backup"
  | "site_restore"
  | "library_backup"
  | "library_restore"
  | "export"
  | "other";

function keepsKey(kind: string | null): Keeps {
  return kind === "takeout_import" ||
    kind === "thumb_batch" ||
    kind === "site_backup" ||
    kind === "site_restore" ||
    kind === "library_backup" ||
    kind === "library_restore" ||
    kind === "export"
    ? kind
    : "other";
}

/**
 * Cancel a running job, after one confirmation that says what stays. The job stops at its next
 * check, so the button then shows "Cancelling…" until the job ends and this component goes away.
 */
export function CancelJobButton({ jobId, kind, cancelling = false, onRequested }: Props) {
  const { t } = useTranslation(["jobbar"]);
  const [confirming, setConfirming] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function send() {
    setConfirming(false);
    setSent(true);
    setError(null);
    try {
      const res = await apiFetch(`/api/v1/jobs/${encodeURIComponent(jobId)}/cancel`, { method: "POST" });
      if (!res.ok) {
        const body: unknown = await res.json().catch(() => null);
        setSent(false);
        setError(errorText(body, res.status));
      }
      onRequested?.();
    } catch (err: unknown) {
      setSent(false);
      setError(err instanceof Error ? err.message : i18n.t("common:networkError"));
    }
  }

  if (sent || cancelling) {
    return (
      <button type="button" disabled>
        {t("jobbar:cancel.working")}
      </button>
    );
  }
  if (confirming) {
    return (
      <span className="cancel-job">
        <span className="detail">
          {t("jobbar:cancel.confirm")} {t(`jobbar:cancel.keeps.${keepsKey(kind)}`)}
        </span>
        <button type="button" onClick={() => void send()}>
          {t("jobbar:cancel.yes")}
        </button>
        <button type="button" onClick={() => setConfirming(false)}>
          {t("jobbar:cancel.no")}
        </button>
      </span>
    );
  }
  return (
    <span className="cancel-job">
      <button type="button" onClick={() => setConfirming(true)}>
        {t("jobbar:cancel.button")}
      </button>
      {error && <span className="status fail">{error}</span>}
    </span>
  );
}
