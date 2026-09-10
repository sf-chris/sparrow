import { useState } from "react";
import { api } from "./api";
import { ErrorNote, Field, Section, useResource } from "./ui";
export default function SubtitleProviderSettings() {
  const resource = useResource(() =>
    api<{ configured: boolean; account_configured: boolean }>(
      "/admin/subtitles/provider",
    ),
  );
  const [draft, setDraft] = useState({
      api_key: "",
      username: "",
      password: "",
    }),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [saved, setSaved] = useState(false);
  async function save() {
    setBusy(true);
    setError("");
    try {
      await api("/admin/subtitles/provider", {
        method: "PUT",
        body: JSON.stringify(draft),
      });
      setDraft({ api_key: "", username: "", password: "" });
      setSaved(true);
      await resource.refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Section
      title="Subtitle discovery"
      description="Included tracks and local subtitle files work without an account. Connect OpenSubtitles to find additional tracks; provider download limits apply."
    >
      <div className="sp-panel sp-form">
        <ErrorNote error={error || resource.error} retry={resource.refresh} />
        <div className="sp-form-grid">
          <Field
            label="OpenSubtitles API key"
            hint={
              resource.data?.configured
                ? "Connected. Leave blank to keep your key."
                : "Get an API key from your OpenSubtitles account."
            }
          >
            <input
              type="password"
              autoComplete="off"
              value={draft.api_key}
              onChange={(e) => setDraft({ ...draft, api_key: e.target.value })}
            />
          </Field>
          <Field label="OpenSubtitles username">
            <input
              autoComplete="off"
              value={draft.username}
              onChange={(e) => setDraft({ ...draft, username: e.target.value })}
            />
          </Field>
          <Field
            label="OpenSubtitles password"
            hint={
              resource.data?.account_configured
                ? "Saved. Leave blank to keep it."
                : "Optional account access for downloads."
            }
          >
            <input
              type="password"
              autoComplete="off"
              value={draft.password}
              onChange={(e) => setDraft({ ...draft, password: e.target.value })}
            />
          </Field>
        </div>
        <div className="sp-savebar">
          <span role="status" className="sp-success">
            {saved ? "Subtitle provider saved." : ""}
          </span>
          <button
            className="sp-button secondary"
            disabled={busy}
            onClick={save}
          >
            {busy ? "Saving…" : "Save subtitle connection"}
          </button>
        </div>
      </div>
    </Section>
  );
}
