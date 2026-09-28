import InstallApp from "./InstallApp";
import { useState, type ReactNode } from "react";
import { ArrowRight } from "lucide-react";
import { Tick } from "./Brand";
import {
  api,
  patch,
  type Preferences as Values,
  type Effective,
  type User,
  type Policy,
} from "./api";
import { ErrorNote, Field, Loading, Page, useResource } from "./ui";

const quality = [
  ["480p", "Standard · 480p"],
  ["720p", "HD · 720p"],
  ["1080p", "Full HD · 1080p"],
  ["2160p", "4K · 2160p"],
];
export function PreferenceFields({
  values,
  onChange,
  sources,
  onReset,
}: {
  values: Values;
  onChange: (key: keyof Values, value: unknown) => void;
  sources?: Record<string, string>;
  onReset?: (key: keyof Values) => void;
}) {
  function field(
    key: keyof Values,
    label: string,
    control: ReactNode,
    hint?: string,
  ) {
    return (
      <div className="preference" key={key}>
        <Field label={label} hint={hint}>
          {control}
        </Field>
        {sources?.[key] === "personal" ? (
          <div className="source">
            <span>Yours</span>
            <button type="button" onClick={() => onReset?.(key)}>
              Use household setting
            </button>
          </div>
        ) : sources?.[key] === "policy" ? (
          <div className="source">
            <span>Server limit</span>
          </div>
        ) : null}
      </div>
    );
  }
  function select(key: keyof Values, options: string[][]) {
    return (
      <select
        value={String(values[key])}
        onChange={(e) => onChange(key, e.target.value)}
      >
        {options.map(([value, label]) => (
          <option value={value} key={value}>
            {label}
          </option>
        ))}
      </select>
    );
  }
  return (
    <div className="preference-groups">
      <fieldset className="preference-group">
        <legend>Sound and subtitles</legend>
        <div className="form-grid">
          {field(
            "audio_pref",
            "Preferred audio",
            select("audio_pref", [
              ["original", "Original language"],
              ["any", "Any audio"],
              ["en", "English"],
              ["es", "Spanish"],
              ["fr", "French"],
              ["de", "German"],
              ["ja", "Japanese"],
              ["ko", "Korean"],
              ["zh", "Chinese"],
              ["it", "Italian"],
              ["pt", "Portuguese"],
            ]),
          )}
          {field(
            "subtitle_languages",
            "Subtitle languages",
            <input
              key={values.subtitle_languages.join(",")}
              defaultValue={values.subtitle_languages.join(", ")}
              onBlur={(e) =>
                onChange(
                  "subtitle_languages",
                  e.target.value
                    .split(",")
                    .map((v) => v.trim())
                    .filter(Boolean),
                )
              }
            />,
            "Language codes in order, like en, es.",
          )}
          {field(
            "subtitle_mode",
            "Show subtitles",
            select("subtitle_mode", [
              ["auto", "In my languages"],
              ["always", "Always"],
              ["off", "Off"],
            ]),
          )}
          {field(
            "subtitle_kind",
            "Subtitle style",
            select("subtitle_kind", [
              ["full", "Full dialogue"],
              ["forced", "Foreign dialogue only"],
              ["sdh", "Dialogue and sounds"],
            ]),
          )}
          {field(
            "require_subtitles",
            "New arrivals",
            <span className="check">
              <input
                type="checkbox"
                checked={values.require_subtitles}
                onChange={(e) =>
                  onChange("require_subtitles", e.target.checked)
                }
              />
              Wait for subtitles
            </span>,
          )}
        </div>
      </fieldset>
      <fieldset className="preference-group">
        <legend>Picture and file size</legend>
        <div className="form-grid">
          {field(
            "preferred_quality",
            "Preferred quality",
            select("preferred_quality", quality),
          )}
          {field(
            "min_quality",
            "Lowest quality",
            select("min_quality", [["any", "Any"], ...quality]),
          )}
          {field(
            "max_file_size_gb",
            "File size limit (GB)",
            <input
              type="number"
              min={0}
              max={1000}
              step={0.5}
              value={values.max_file_size_gb}
              onChange={(e) =>
                onChange("max_file_size_gb", Number(e.target.value))
              }
            />,
            "Per file. 0 means no personal limit.",
          )}
          {field(
            "prefer_smaller_files",
            "Smaller files",
            <span className="check">
              <input
                type="checkbox"
                checked={values.prefer_smaller_files}
                onChange={(e) =>
                  onChange("prefer_smaller_files", e.target.checked)
                }
              />
              Prefer them at the same quality
            </span>,
          )}
        </div>
      </fieldset>
      <fieldset className="preference-group">
        <legend>Series</legend>
        <div className="form-grid">
          {field(
            "monitoring",
            "New episodes",
            select("monitoring", [
              ["exact", "Only what I request"],
              ["keep_current", "Add them as they air"],
            ]),
            "You can change this on each request.",
          )}
        </div>
      </fieldset>
    </div>
  );
}

export default function Preferences({
  user,
  welcome = false,
  onChanged,
  continueSetup = false,
}: {
  user: User;
  welcome?: boolean;
  onChanged: () => void;
  continueSetup?: boolean;
}) {
  const ownerSetup = welcome && user.role === "admin";
  const resource = useResource(async () => {
    if (ownerSetup) {
      const data = await api<{ defaults: Values; policy: Policy }>(
        "/admin/defaults",
      );
      return {
        effective: {
          values: data.defaults,
          sources: {},
          policy: data.policy,
        } as Effective,
        overrides: {},
      };
    }
    return api<{ effective: Effective; overrides: Partial<Values> }>(
      "/preferences",
    );
  }, [ownerSetup]);
  const [draft, setDraft] = useState<Partial<Values>>({});
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);
  const values = resource.data
    ? { ...resource.data.effective.values, ...draft }
    : null;
  async function save() {
    setBusy(true);
    setError("");
    setSaved(false);
    try {
      if (ownerSetup)
        await api("/admin/defaults", {
          method: "PUT",
          body: JSON.stringify({
            defaults: values,
            policy: resource.data!.effective.policy,
          }),
        });
      await patch("/preferences", { values: ownerSetup ? {} : draft });
      setDraft({});
      setSaved(true);
      await resource.refresh();
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function reset(key: keyof Values) {
    try {
      await patch("/preferences", { values: { [key]: null } });
      setDraft((previous) => {
        const next = { ...previous };
        delete next[key];
        return next;
      });
      await resource.refresh();
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    }
  }
  return (
    <Page
      className={welcome ? "welcome" : ""}
      title={
        welcome
          ? ownerSetup
            ? "Household defaults"
            : `Welcome, ${user.name.split(" ")[0]}`
          : "Preferences"
      }
      lede={
        ownerSetup
          ? "Everyone starts with these and can change their own."
          : welcome
            ? "You start with the household’s settings."
            : "Anything you haven’t changed follows the household."
      }
    >
      <ErrorNote error={error || resource.error} retry={resource.refresh} />
      {resource.loading && !values ? (
        <Loading label="Loading preferences" />
      ) : (
        values && (
          <>
            <PreferenceFields
              values={values}
              onChange={(key, value) => {
                setDraft({ ...draft, [key]: value });
                setSaved(false);
              }}
              sources={
                ownerSetup
                  ? undefined
                  : {
                      ...resource.data!.effective.sources,
                      ...Object.fromEntries(
                        Object.keys(draft).map((k) => [k, "personal"]),
                      ),
                    }
              }
              onReset={reset}
            />
            <div className="savebar">
              <span role="status" className="done-note">
                {saved && (
                  <>
                    <Tick /> Saved
                  </>
                )}
              </span>
              <button className="btn primary" disabled={busy} onClick={save}>
                {busy
                  ? "Saving…"
                  : welcome
                    ? continueSetup
                      ? "Continue setup"
                      : "Continue"
                    : "Save"}
                {welcome && <ArrowRight size={18} strokeWidth={2.5} />}
              </button>
            </div>
          </>
        )
      )}
      {!welcome && <InstallApp />}
    </Page>
  );
}
