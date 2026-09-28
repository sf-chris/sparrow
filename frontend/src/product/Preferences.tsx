import InstallApp from "./InstallApp";
import { useState, type ReactNode } from "react";
import { ArrowRight, Check } from "lucide-react";
import {
  api,
  patch,
  type Preferences as Values,
  type Effective,
  type User,
  type Policy,
} from "./api";
import { ErrorNote, Field, Loading, Page, Section, useResource } from "./ui";

const quality = [
  ["480p", "Standard definition"],
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
      <div className="sp-preference" key={key}>
        <Field label={label} hint={hint}>
          {control}
        </Field>
        {(sources?.[key] === "personal" || sources?.[key] === "policy") && (
          <div className="sp-source">
            <span>
              {sources?.[key] === "personal"
                ? "Your preference"
                : "Limited by server policy"}
            </span>
            {sources?.[key] === "personal" && (
              <button type="button" onClick={() => onReset?.(key)}>
                Use default
              </button>
            )}
          </div>
        )}
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
    <div className="sp-preference-groups">
      <fieldset className="sp-preference-group">
        <legend>Sound & subtitles</legend>
        <p className="sp-preference-description">
          Hear every word. Follow every story.
        </p>
        <div className="sp-form-grid">
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
            "Language codes, in preference order. For example: en, es.",
          )}
          {field(
            "subtitle_mode",
            "Show subtitles",
            select("subtitle_mode", [
              ["auto", "When useful"],
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
              ["sdh", "Dialogue and sound descriptions"],
            ]),
          )}
          {field(
            "require_subtitles",
            "Ready-to-watch requirements",
            <span className="sp-checkbox">
              <input
                type="checkbox"
                checked={values.require_subtitles}
                onChange={(e) =>
                  onChange("require_subtitles", e.target.checked)
                }
              />
              Subtitles must be ready too
            </span>,
          )}
        </div>
      </fieldset>
      <fieldset className="sp-preference-group">
        <legend>Picture & storage</legend>
        <p className="sp-preference-description">
          The right balance of a great picture and room for more.
        </p>
        <div className="sp-form-grid">
          {field(
            "preferred_quality",
            "Preferred picture quality",
            select("preferred_quality", quality),
          )}
          {field(
            "min_quality",
            "Lowest acceptable quality",
            select("min_quality", [["any", "Any verified video"], ...quality]),
          )}
          {field(
            "max_file_size_gb",
            "File size limit",
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
            "GB per file. Zero means no personal limit; server limits still apply.",
          )}
          {field(
            "prefer_smaller_files",
            "Storage preference",
            <span className="sp-checkbox">
              <input
                type="checkbox"
                checked={values.prefer_smaller_files}
                onChange={(e) =>
                  onChange("prefer_smaller_files", e.target.checked)
                }
              />
              Prefer smaller suitable files
            </span>,
          )}
        </div>
      </fieldset>
      <fieldset className="sp-preference-group">
        <legend>Your collection, on autopilot</legend>
        <p className="sp-preference-description">
          Decide what happens after the credits.
        </p>
        <div className="sp-form-grid">
          {field(
            "monitoring",
            "Future episodes",
            select("monitoring", [
              ["exact", "Only what I request"],
              ["keep_current", "Keep new episodes coming"],
            ]),
            "You’ll see this choice again before starting a request.",
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
      title={
        welcome
          ? ownerSetup
            ? "Set your household defaults."
            : "Make it your own."
          : "Your settings"
      }
      description={
        ownerSetup
          ? "These are the starting preferences for everyone on this server. Each person can make their own changes later."
          : welcome
            ? "Here are the household defaults. Keep them as they are, or choose what works for you."
            : "Your preferences follow you. Anything you haven’t changed follows the household defaults."
      }
    >
      <ErrorNote error={error || resource.error} retry={resource.refresh} />
      {resource.loading && !values ? (
        <Loading label="Loading preferences…" />
      ) : (
        values && (
          <Section
            title={
              ownerSetup ? "Household preferences" : "Watching preferences"
            }
          >
            <div className="sp-panel">
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
              <div className="sp-savebar">
                <span role="status" className="sp-success">
                  {saved && (
                    <>
                      <Check size={14} aria-hidden="true" />
                      Preferences saved
                    </>
                  )}
                </span>
                <button
                  className="sp-button primary"
                  disabled={busy}
                  onClick={save}
                >
                  {busy
                    ? "Saving…"
                    : welcome
                      ? continueSetup
                        ? "Continue setup"
                        : "Continue to Sparrow"
                      : "Save preferences"}
                  {welcome && <ArrowRight size={16} />}
                </button>
              </div>
            </div>
          </Section>
        )
      )}
      {!welcome && <InstallApp />}
    </Page>
  );
}
