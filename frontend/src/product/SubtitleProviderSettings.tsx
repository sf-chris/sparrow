import { api } from "./api";
import { ErrorNote, Field, Section, useResource } from "./ui";

export type ProviderDraft = { api_key: string; username: string; password: string };
export const emptyProvider: ProviderDraft = { api_key: "", username: "", password: "" };

/** Saves only what was typed: blank fields keep what the server has. */
export async function saveProvider(draft: ProviderDraft) {
  if (!Object.values(draft).some(Boolean)) return;
  await api("/admin/subtitles/provider", {
    method: "PUT",
    body: JSON.stringify(draft),
  });
}

/** OpenSubtitles sign-in. The page's own Save keeps it, with everything else. */
export default function SubtitleProviderSettings({
  draft,
  onChange,
  version = 0,
}: {
  draft: ProviderDraft;
  onChange: (draft: ProviderDraft) => void;
  /** Bumped after a save, so "Saved" hints refresh. */
  version?: number;
}) {
  const resource = useResource(
    () =>
      api<{ configured: boolean; account_configured: boolean }>(
        "/admin/subtitles/provider",
      ),
    [version],
  );
  return (
    <Section
      title="Subtitles"
      description="Subtitles in or beside your files work without this. OpenSubtitles finds more, within its download limits."
    >
      <div className="form">
        <ErrorNote error={resource.error} retry={resource.refresh} />
        <div className="form-grid">
          <Field
            label="OpenSubtitles API key"
            hint={
              resource.data?.configured
                ? "Saved. Leave blank to keep it."
                : "From your OpenSubtitles account."
            }
          >
            <input
              type="password"
              autoComplete="off"
              value={draft.api_key}
              onChange={(e) => onChange({ ...draft, api_key: e.target.value })}
            />
          </Field>
          <Field label="OpenSubtitles username">
            <input
              autoComplete="off"
              value={draft.username}
              onChange={(e) => onChange({ ...draft, username: e.target.value })}
            />
          </Field>
          <Field
            label="OpenSubtitles password"
            hint={
              resource.data?.account_configured
                ? "Saved. Leave blank to keep it."
                : "Optional, for downloads."
            }
          >
            <input
              type="password"
              autoComplete="off"
              value={draft.password}
              onChange={(e) => onChange({ ...draft, password: e.target.value })}
            />
          </Field>
        </div>
      </div>
    </Section>
  );
}
