import { useEffect, useState } from "react";
import { RefreshCw } from "lucide-react";
import { api, patch, post } from "./api";
import { ErrorNote, Field, useResource } from "./ui";

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
  const status =
    latest &&
    (latest.state === "ready"
      ? "Subtitles checked"
      : running
        ? "Preparing subtitles"
        : latest.state === "review_pending"
          ? "Ready for review"
          : latest.state === "cancelled"
            ? "Repair stopped"
            : "Subtitles need attention");
  return (
    <details
      className="disclosure help subtitle-care"
      open={!!latest && latest.state !== "ready"}
    >
      <summary>
        Subtitle care
        {status && <span className="meta"> · {status}</span>}
      </summary>
      <div className="help-body form">
        <ErrorNote error={error || resource.error} retry={resource.refresh} />
        {latest && (
          <div role="status" className="care-status">
            <strong>{status}</strong>
            <p className="muted">{latest.message}</p>
          </div>
        )}
        <p className="muted">
          Finds a matching track, aligns it to this audio and checks samples of
          dialogue. Original files stay as they are.
        </p>
        <div className="form-grid">
          <Field
            label="Language"
            hint="Automatic checks need captions in the spoken language."
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
          <Field label="Style">
            <select value={kind} onChange={(e) => setKind(e.target.value)}>
              <option value="full">Full dialogue</option>
              <option value="sdh">Dialogue and sounds</option>
              <option value="forced">Foreign dialogue only</option>
            </select>
          </Field>
        </div>
        <div className="actions">
          <button
            className="btn"
            disabled={busy || running}
            onClick={() => void repair()}
          >
            <RefreshCw size={16} strokeWidth={2.5} />
            Find and repair
          </button>
          {running && (
            <button
              className="btn quiet"
              disabled={busy}
              onClick={() => void action("DELETE")}
            >
              Stop repair
            </button>
          )}
          {latest?.state === "review_pending" && (
            <button
              className="btn"
              disabled={busy}
              onClick={() => void action("POST", "/review")}
            >
              Retry review
            </button>
          )}
        </div>
        <Field
          label="Use your own subtitle file"
          hint=".srt, .vtt, .ass or .ssa, under 2 MB."
        >
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
        {selected?.id && (
          <Field
            label="Your subtitle delay (seconds)"
            hint="Positive shows captions later. Only changes your playback."
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
        {resource.data?.tracks.some((t) => t.original_url) && (
          <details className="disclosure">
            <summary>Original subtitle files</summary>
            <p className="muted">
              Kept before alignment; their timing is unchecked.
            </p>
            <div className="actions">
              {resource.data.tracks
                .filter((t) => t.original_url)
                .slice(0, 10)
                .map((t) => (
                  <a
                    className="btn quiet"
                    key={t.id}
                    href={t.original_url}
                    download={`${t.language}-original.vtt`}
                  >
                    Download {t.language}
                  </a>
                ))}
            </div>
          </details>
        )}
      </div>
    </details>
  );
}
