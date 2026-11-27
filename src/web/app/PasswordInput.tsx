"use client";

import { InputHTMLAttributes, useState } from "react";
import { useTranslation } from "react-i18next";

type Props = Omit<InputHTMLAttributes<HTMLInputElement>, "type">;

/** A password input with an eye button that shows or hides what was typed. Hidden again on every mount. */
export function PasswordInput(props: Props) {
  const { t } = useTranslation("common");
  const [shown, setShown] = useState(false);
  const label = shown ? t("hidePassword") : t("showPassword");
  return (
    <span className="password-field">
      <input {...props} type={shown ? "text" : "password"} />
      <button
        type="button"
        className="password-eye"
        aria-label={label}
        title={label}
        aria-pressed={shown}
        disabled={props.disabled}
        // Keep the caret in the input when the eye is clicked.
        onMouseDown={(e) => e.preventDefault()}
        onClick={() => setShown((v) => !v)}
      >
        <svg
          viewBox="0 0 24 24"
          width="18"
          height="18"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7S1 12 1 12z" />
          <circle cx="12" cy="12" r="3" />
          {shown && <path d="M3 3l18 18" />}
        </svg>
      </button>
    </span>
  );
}
