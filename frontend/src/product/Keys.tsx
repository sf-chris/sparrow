import { useId } from "react";
import { Tick } from "./Brand";

export type KeyName = "tmdb_api_key" | "glm_api_key";

export const KEYS: {
  name: KeyName;
  label: string;
  purpose: string;
  from: string;
  href: string;
  required: boolean;
}[] = [
  {
    name: "tmdb_api_key",
    label: "TMDB API key",
    purpose: "Film, series and episode details.",
    from: "TMDB",
    href: "https://www.themoviedb.org/settings/api",
    required: true,
  },
  {
    name: "glm_api_key",
    label: "Z.ai API key",
    purpose: "Finds what you ask for and checks subtitles. Z.ai bills for use.",
    from: "Z.ai",
    href: "https://z.ai/manage-apikey/apikey-list",
    required: true,
  },
];

/** Required keys the server does not have yet. */
export function missingKeys(saved: Record<string, unknown>): KeyName[] {
  return KEYS.filter((key) => key.required && !saved[`${key.name}_configured`]).map((key) => key.name);
}

/** A secret the browser never sees again: say plainly whether the server has one. */
export function KeyField({
  spec,
  saved,
  value,
  error,
  onChange,
}: {
  spec: (typeof KEYS)[number];
  saved: boolean;
  value: string;
  error?: string;
  onChange: (value: string) => void;
}) {
  const id = useId();
  return (
    <div className={`field key-field ${error ? "invalid" : ""}`}>
      <div className="key-label">
        <label htmlFor={id}>{spec.label}</label>
        <span className={`key-state ${saved ? "saved" : ""}`} id={id + "-state"}>
          {saved ? (
            <>
              <Tick /> On server
            </>
          ) : spec.required ? (
            "Not set"
          ) : (
            "Optional"
          )}
        </span>
      </div>
      <input
        id={id}
        type="password"
        autoComplete="off"
        spellCheck={false}
        value={value}
        placeholder={saved ? "Leave blank to keep it" : "Paste key"}
        aria-invalid={error ? true : undefined}
        aria-describedby={`${id}-state ${id}-hint`}
        onChange={(e) => onChange(e.target.value)}
      />
      <small id={id + "-hint"} role={error ? "alert" : undefined}>
        {error || (
          <>
            {spec.purpose}{" "}
            <a href={spec.href} target="_blank" rel="noreferrer">
              Get one from {spec.from}
            </a>
          </>
        )}
      </small>
    </div>
  );
}
