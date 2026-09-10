import { useEffect, useState } from "react";
import { RefreshCw, Upload } from "lucide-react";
import { api, patch, post } from "./api";
import { ErrorNote, Field, Section, useResource } from "./ui";

export type Caption = {
  index: number;
  id?: string;
  language: string;
  title: string;
  url: string;
  offset?: number;
};
type Track = {
  audio_index?: number;
  original_url?: string;
  id: string;
  language: string;
  kind: string;
  state: string;
  url: string;
  offset: number;
};
type RepairState = {
  tracks: Track[];
  tasks: { id: string; state: string; message: string }[];
};
export function SubtitleRepair({
  assetId,
  audio,
  selected,
  onReady,
  onOffset,
}: {
  assetId: string;
  audio: number | null;
  selected?: Caption;
  onReady: (tracks: Caption[]) => void;
  onOffset: (id: string, seconds: number) => void;
}) {
  const resource = useResource(
    () => api<RepairState>(`/assets/${assetId}/subtitles`),
    [assetId],
  );
  const [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [language, setLanguage] = useState("en"),
    [kind, setKind] = useState("full");
  const latest = resource.data?.tasks[0];
  const running =
    !!latest &&
    ["queued", "finding", "aligning", "reviewing"].includes(latest.state);
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => void resource.refresh(), 3000);
    return () => clearInterval(timer);
  }, [running, assetId]);
  useEffect(() => {
    if (resource.data)
      onReady(
        resource.data.tracks
          .filter((t) => t.state === "ready" && t.audio_index === audio)
          .map((t, i) => ({
            ...t,
            index: 10000 + i,
            title: `${t.language} · checked ${t.kind}`,
          })),
      );
  }, [resource.data, audio]);
  async function repair(file?: File) {
    setBusy(true);
    setError("");
    try {
      if (file && file.size > 2 * 1024 * 1024)
        throw new Error("Choose a subtitle file smaller than 2 MB.");
      await post(`/assets/${assetId}/subtitles/repair`, {
        audio_index: audio,
        language,
        kind,
        ...(file
          ? {
              text: await file.text(),
              format: file.name.split(".").pop()?.toLowerCase(),
            }
          : {}),
      });
      await resource.refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function action(method: string, suffix = "") {
    setBusy(true);
    setError("");
    try {
      await api(`/subtitles/tasks/${latest!.id}${suffix}`, { method });
      await resource.refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function offset(seconds: number) {
    if (!selected?.id) return;
    try {
      await patch(`/subtitles/tracks/${selected.id}/offset`, { seconds });
      onOffset(selected.id, seconds);
      await resource.refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  }
  return (
    <Section
      title="Subtitle care"
      description="Find a suitable track, align it to this audio, then review dialogue samples. Your original files stay intact."
    >
      <div className="sp-panel sp-form">
        <ErrorNote error={error || resource.error} retry={resource.refresh} />
        {latest && (
          <div role="status">
            <strong>
              {latest.state === "ready"
                ? "Subtitles checked"
                : running
                  ? "Preparing your subtitles"
                  : latest.state === "review_pending"
                    ? "Ready for quality review"
                    : latest.state === "cancelled"
                      ? "Repair stopped"
                      : "Subtitles need attention"}
            </strong>
            <p className="sp-muted">{latest.message}</p>
          </div>
        )}
        <div className="sp-form-grid">
          <Field
            label="Repair language"
            hint="Automatic verification currently needs captions in the spoken language."
          >
            <select
              value={language}
              onChange={(e) => setLanguage(e.target.value)}
            >
              {[
                ["en", "English"],
                ["es", "Spanish"],
                ["fr", "French"],
                ["de", "German"],
                ["it", "Italian"],
                ["pt", "Portuguese"],
                ["ja", "Japanese"],
                ["ko", "Korean"],
                ["zh", "Chinese"],
              ].map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Subtitle style">
            <select value={kind} onChange={(e) => setKind(e.target.value)}>
              <option value="full">Full dialogue</option>
              <option value="sdh">Dialogue and sound descriptions</option>
              <option value="forced">Foreign dialogue only</option>
            </select>
          </Field>
        </div>
        <div className="sp-actions">
          <button
            className="sp-button secondary"
            disabled={busy || running}
            onClick={() => void repair()}
          >
            <RefreshCw size={15} />
            Find & repair subtitles
          </button>
          <Field label="Use your own subtitle file">
            <input
              type="file"
              accept=".srt,.vtt,.ass,.ssa"
              disabled={busy || running}
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) void repair(file);
                e.target.value = "";
              }}
            />
          </Field>
          {running && (
            <button
              className="sp-button quiet"
              disabled={busy}
              onClick={() => void action("DELETE")}
            >
              Stop repair
            </button>
          )}
          {latest?.state === "review_pending" && (
            <button
              className="sp-button secondary"
              disabled={busy}
              onClick={() => void action("POST", "/review")}
            >
              Retry quality review
            </button>
          )}
        </div>
        {resource.data?.tracks.some((t) => t.original_url) && (
          <details>
            <summary className="sp-button quiet">
              Original subtitle files
            </summary>
            <p className="sp-muted">
              These are preserved before alignment. Their original timing has
              not been approved.
            </p>
            {resource.data.tracks
              .filter((t) => t.original_url)
              .slice(0, 10)
              .map((t) => (
                <a
                  className="sp-button quiet"
                  key={t.id}
                  href={t.original_url}
                  download={`${t.language}-original.vtt`}
                >
                  Download original · {t.language}
                </a>
              ))}
          </details>
        )}
        {selected?.id && (
          <Field
            label="Personal subtitle delay (seconds)"
            hint="Positive values show captions later. This only changes your playback."
          >
            <input
              type="number"
              min={-30}
              max={30}
              step={0.25}
              value={selected.offset || 0}
              onChange={(e) => void offset(Number(e.target.value))}
            />
          </Field>
        )}
      </div>
    </Section>
  );
}
