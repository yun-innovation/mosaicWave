"use client";

import { useTranslation } from "react-i18next";

import { apiMediaUrl } from "./api";
import { formatBytes } from "./format";

type PartedItem = {
  name: string;
  parts: number;
  part_files: { name: string; size: number }[];
  missing: number[];
};

const PART_NUMBER = /-(\d{3})\.zip$/;

/**
 * The files of a split backup in the list: one download link per part, and a note when parts are not
 * here yet (they can be uploaded; the backup cannot be restored until every part is). `url` is the
 * folder of the API the files are downloaded from.
 */
export function BackupParts({ item, url }: { item: PartedItem; url: string }) {
  const { t } = useTranslation("backup");
  if (item.parts <= 1) return null;
  return (
    <>
      <p className="detail backup-parts">
        {t("partsLabel")}:{" "}
        {item.part_files.map((part) => {
          const number = PART_NUMBER.exec(part.name)?.[1] ?? part.name;
          return (
            <a
              key={part.name}
              className="part-link"
              href={apiMediaUrl(`${url}/${encodeURIComponent(part.name)}`)}
              title={`${part.name} (${formatBytes(part.size)})`}
              download
            >
              {number}
            </a>
          );
        })}
      </p>
      {item.missing.length > 0 && (
        <p className="status fail">
          {t("incomplete", { missing: item.missing.join(", "), count: item.parts })}
        </p>
      )}
    </>
  );
}
