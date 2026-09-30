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
  sync_checked?: boolean;
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
  sync_checked: boolean;
};
type RepairState = {
  preferences: {
    values: {
      subtitle_languages: string[];
      subtitle_kind: string;
      verify_subtitles: boolean;
    };
  };
  tracks: Track[];
  tasks: { id: string; state: string; message: string; track_id?: string }[];
};
const names = new Intl.DisplayNames(["en"], { type: "language" });
const languageName = (code: string) => {
  try {
    return names.of(code.replace(/_/g, "-")) || code;
  } catch {
    return code;
  }
};
const styles: Record<string, string> = {
  sdh: "dialogue and sounds",
  forced: "foreign dialogue",
};
export function SubtitleRepair({
  assetId,
  audio,
  selected,
  onReady,
  onOffset,
  hasSubtitles = false,
  expanded = false,
}: {
  assetId: string;
  audio: number | null;
  selected?: Caption;
  onReady: (tracks: Caption[]) => void;
  onOffset: (id: string, seconds: number) => void;
  hasSubtitles?: boolean;
  expanded?: boolean;
}) {
  const resource = useResource(
    () => api<RepairState>(`/assets/${assetId}/subtitles`),
    [assetId],
  );
  const [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [language, setLanguage] = useState("en"),
    [kind, setKind] = useState("full");
  const [verify, setVerify] = useState(false);
  const [initialised, setInitialised] = useState(false);
  useEffect(() => {
    setInitialised(false);
  }, [assetId]);
  useEffect(() => {
    const prefs = resource.data?.preferences.values;
    if (prefs && !initialised) {
      setLanguage(prefs.subtitle_languages[0] || "en");
      setKind(prefs.subtitle_kind);
      setVerify(prefs.verify_subtitles);
      setInitialised(true);
    }
  }, [resource.data, initialised]);
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
            title: `${languageName(t.language)}${styles[t.kind] ? ` · ${styles[t.kind]}` : ""}${t.sync_checked ? " · sync checked" : ""}`,
          })),
      );
  }, [resource.data, audio]);
  async function repair(file?: File, fix = false) {
    setBusy(true);
    setError("");
    try {
      if (file && file.size > 2 * 1024 * 1024)
        throw new Error("Choose a subtitle file under 2 MB.");
      await post(`/assets/${assetId}/subtitles/repair`, {
        audio_index: audio,
        language,
        kind,
        verify,
        repair: fix,
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
      ? resource.data?.tracks.some(
          (t) =>
            t.id === latest.track_id && t.state === "ready" && t.sync_checked,
        )
        ? "Sync checked"
        : "Subtitles available"
      : running
        ? "In progress"
        : latest.state === "review_pending"
          ? "Review didn’t finish"
          : latest.state === "cancelled"
            ? "Stopped"
            : "Couldn’t fix");
  return (
    <details
      className="disclosure help subtitle-care"
      open={expanded || (!!latest && latest.state !== "ready")}
    >
      <summary>
        Subtitle help
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
          Find a track, check its timing or fix an out-of-sync track.
        </p>
        <div className="form-grid">
          <Field label="Language">
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
        <Field
          label="Check subtitle sync"
          hint="Uses AI within the household spending limit."
        >
          <label className="check">
            <input
              type="checkbox"
              checked={verify}
              onChange={(e) => setVerify(e.target.checked)}
            />{" "}
            Compare captions with the voice
          </label>
        </Field>
        <div className="actions">
          <button
            className="btn"
            disabled={busy || running || !initialised}
            onClick={() => void repair()}
          >
            <RefreshCw size={16} strokeWidth={2.5} />
            {verify ? "Check subtitles" : "Get subtitles"}
          </button>
          {(hasSubtitles ||
            selected ||
            resource.data?.tracks.some((t) => t.state === "ready")) && (
            <button
              className="btn"
              disabled={busy || running || !initialised}
              onClick={() => void repair(undefined, true)}
            >
              Fix subtitle timing
            </button>
          )}
          {running && (
            <button
              className="btn quiet"
              disabled={busy}
              onClick={() => void action("DELETE")}
            >
              Stop
            </button>
          )}
          {latest?.state === "review_pending" && (
            <button
              className="btn"
              disabled={busy}
              onClick={() => void action("POST", "/review")}
            >
              Try the review again
            </button>
          )}
        </div>
        <Field
          label="Upload a subtitle file"
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
            label="Subtitle delay (seconds)"
            hint="Positive numbers show subtitles later. Only for you."
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
            <p className="muted">As found, before syncing.</p>
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
                    Download {languageName(t.language)}
                  </a>
                ))}
            </div>
          </details>
        )}
      </div>
    </details>
  );
}
