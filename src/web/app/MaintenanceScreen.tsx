"use client";

import { useTranslation } from "react-i18next";

import { BrandLockup } from "./BrandLockup";
import { Me } from "./api";
import { formatDateTime } from "./format";
import { MaintenanceStatus } from "./types";

type Props = {
  status: MaintenanceStatus;
  /** Signed in as a non-admin (their session stays; they can sign out). Null when signed out. */
  me: Me | null;
  onAdminSignIn: () => void;
  onLogout: () => void;
};

/** Full-page alert for everyone the server is turning away. The page checks again by itself. */
export function MaintenanceScreen({ status, me, onAdminSignIn, onLogout }: Props) {
  const { t } = useTranslation(["maintenance", "common"]);
  return (
    <main>
      <BrandLockup lede={t("maintenance:screen.lede")} />
      <div className="card import maintenance-screen" role="alert">
        <h2>{t("maintenance:screen.title")}</h2>
        <p>{status.reason || t("maintenance:screen.noReason")}</p>
        {status.since && <p className="detail">{t("maintenance:since", { time: formatDateTime(status.since) })}</p>}
        <p className="detail import-progress">{t("maintenance:screen.retrying")}</p>
        <div className="button-row">
          {me ? (
            <button type="button" onClick={onLogout}>
              {t("common:session.signOut")}
            </button>
          ) : (
            <button type="button" onClick={onAdminSignIn}>
              {t("maintenance:screen.adminSignIn")}
            </button>
          )}
        </div>
      </div>
    </main>
  );
}
