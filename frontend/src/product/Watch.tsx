import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  ArrowLeft,
  Maximize,
  Minimize,
  Pause,
  Play,
  RefreshCw,
  RotateCcw,
  RotateCw,
  Volume2,
  VolumeX,
} from "lucide-react";
import { api, post, type Asset, type Effective } from "./api";
import { SubtitleRepair, type Caption } from "./SubtitleRepair";
import { ErrorNote, Field, Loading, episodeCode, useResource } from "./ui";

type Playback = {
  id: string;
  mode: "direct" | "hls";
  url: string;
  position: number;
  duration: number;
  audio_index: number | null;
  subtitles: Caption[];
};
const languageNames = new Intl.DisplayNames(["en"], { type: "language" });
const stamp = (seconds: number) => {
  const total = Math.max(0, Math.floor(seconds || 0));
  const h = Math.floor(total / 3600),
    m = Math.floor((total % 3600) / 60),
    s = String(total % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${s}` : `${m}:${s}`;
};
function trackLabel(
  track: { title?: string; language?: string },
  fallback: string,
) {
  if (track.title) return track.title;
  const code = track.language?.replace(/_/g, "-");
  if (!code || code === "und") return fallback;
  try {
    return languageNames.of(code) || fallback;
  } catch {
    return fallback;
  }
}
export default function Watch() {
  const { assetId } = useParams();
  const resource = useResource(
    () =>
      api<{ asset: Asset; title: string; preferences: Effective }>(
        `/assets/${assetId}`,
      ),
    [assetId],
  );
  const data = resource.data;
  return (
    <main id="main-content" className="page player-page">
      <header className="player-head">
        <Link className="back" to={data ? `/items/${data.asset.item_id}` : "/"}>
          <ArrowLeft size={18} strokeWidth={2.5} />
          Back to title
        </Link>
        <h1>
          {data?.title || "Player"}
          {data?.asset.episode ? (
            <span className="num"> {episodeCode(data.asset)}</span>
          ) : null}
        </h1>
      </header>
      <ErrorNote error={resource.error} retry={resource.refresh} />
      {resource.loading && !data ? (
        <Loading label="Loading" />
      ) : (
        data && <Player key={assetId} {...data} />
      )}
    </main>
  );
}
function Player({
  asset,
  title,
  preferences,
}: {
  asset: Asset;
  title: string;
  preferences: Effective;
}) {
  const video = useRef<HTMLVideoElement>(null);
  const active = useRef<Playback | null>(null);
  const sequence = useRef(0);
  const generation = useRef(0);
  const queue = useRef<Promise<void>>(Promise.resolve());
  const [session, setSession] = useState<Playback | null>(null);
  const [audio, setAudio] = useState<number | undefined>(undefined);
  const [attempt, setAttempt] = useState(0);
  const [transcode, setTranscode] = useState(false);
  const [error, setError] = useState("");
  const [progressError, setProgressError] = useState("");
  const [busy, setBusy] = useState(true);
  const [subtitle, setSubtitle] = useState(-1);
  const [waiting, setWaiting] = useState(false);
  const screen = useRef<HTMLDivElement>(null);
  const [playing, setPlaying] = useState(false);
  const [time, setTime] = useState(0);
  const [length, setLength] = useState(asset.facts.duration || 0);
  const [muted, setMuted] = useState(false);
  const [full, setFull] = useState(false);
  useEffect(() => {
    const change = () => setFull(document.fullscreenElement === screen.current);
    document.addEventListener("fullscreenchange", change);
    return () => document.removeEventListener("fullscreenchange", change);
  }, []);
  useEffect(() => {
    if (video.current) video.current.muted = muted;
  }, [muted, session?.id]);
  function toggle() {
    const element = video.current;
    if (!element) return;
    if (element.paused) void element.play().catch(() => {});
    else element.pause();
  }
  function seek(to: number) {
    const element = video.current;
    if (!element) return;
    element.currentTime = Math.max(0, Math.min(to, element.duration || to));
    setTime(element.currentTime);
  }
  function fullscreen() {
    if (document.fullscreenElement) void document.exitFullscreen();
    else void screen.current?.requestFullscreen?.().catch(() => {});
  }
  const position = useRef<number | undefined>(undefined);
  const save = async (ended = false, keepalive = false) => {
    const current = active.current,
      element = video.current;
    if (!current || !element || element.readyState < 1) return;
    position.current = element.currentTime;
    try {
      await api(`/playback/${current.id}/progress`, {
        method: "PUT",
        keepalive,
        body: JSON.stringify({
          position: element.currentTime,
          sequence: ++sequence.current,
          ended,
        }),
      });
      setProgressError("");
    } catch (e) {
      setProgressError(`Couldn’t save your place. ${(e as Error).message}`);
    }
  };
  useEffect(() => {
    const current = ++generation.current;
    setBusy(true);
    setError("");
    setSession(null);
    queue.current = queue.current
      .catch(() => {})
      .then(async () => {
        if (current !== generation.current) return;
        try {
          const supported = ["h264"];
          if (video.current?.canPlayType('video/webm; codecs="vp9"'))
            supported.push("vp9");
          const next = await post<Playback>("/playback", {
            asset_id: asset.id,
            audio_index: audio,
            position: position.current,
            force_transcode: transcode,
            supported_video: supported,
          });
          if (current !== generation.current) {
            await api(`/playback/${next.id}`, { method: "DELETE" });
            return;
          }
          active.current = next;
          sequence.current = 0;
          setSession(next);
          const prefs = preferences.values;
          const preferred = next.subtitles.find(
            (t) =>
              prefs.subtitle_languages.includes(t.language) ||
              prefs.subtitle_languages.some((l) => t.language.startsWith(l)),
          );
          setSubtitle(
            prefs.subtitle_mode === "off"
              ? -1
              : (preferred?.index ??
                  (prefs.subtitle_mode === "always"
                    ? (next.subtitles[0]?.index ?? -1)
                    : -1)),
          );
        } catch (e) {
          if (current === generation.current) setError((e as Error).message);
        } finally {
          if (current === generation.current) setBusy(false);
        }
      });
    return () => {
      ++generation.current;
      const previous = active.current;
      const saved = save(false, true);
      active.current = null;
      if (previous)
        void saved
          .then(() =>
            api(`/playback/${previous.id}`, {
              method: "DELETE",
              keepalive: true,
            }),
          )
          .catch(() => {});
    };
  }, [asset.id, audio, attempt, transcode]);
  useEffect(() => {
    const element = video.current;
    if (!element || !session) return;
    let disposed = false,
      destroy = () => {};
    const metadata = () => {
      element.currentTime = session.position;
      void element.play().catch(() => {});
    };
    element.addEventListener("loadedmetadata", metadata);
    if (
      session.mode === "direct" ||
      element.canPlayType("application/vnd.apple.mpegurl")
    )
      element.src = session.url;
    else
      void import("hls.js")
        .then(({ default: Hls }) => {
          if (disposed) return;
          if (!Hls.isSupported()) {
            setError(
              "This browser can’t play this stream. Try Chrome, Firefox, Edge or Safari.",
            );
            return;
          }
          const hls = new Hls({
            maxBufferLength: 24,
            maxMaxBufferLength: 48,
            startPosition: session.position,
            fragLoadingMaxRetry: 3,
          });
          destroy = () => hls.destroy();
          hls.on(Hls.Events.ERROR, (_, data) => {
            if (data.fatal)
              setError(
                "Playback stopped. Check the storage is connected, then try again.",
              );
          });
          hls.loadSource(session.url);
          hls.attachMedia(element);
        })
        .catch(() => setError("The player didn’t load. Refresh the page."));
    const timer = window.setInterval(() => {
      if (!element.paused) void save();
    }, 5000);
    const hidden = () => {
      if (document.visibilityState === "hidden") void save(false, true);
    };
    document.addEventListener("visibilitychange", hidden);
    return () => {
      disposed = true;
      destroy();
      clearInterval(timer);
      document.removeEventListener("visibilitychange", hidden);
      element.removeEventListener("loadedmetadata", metadata);
      element.removeAttribute("src");
      element.load();
    };
  }, [session?.id]);
  useEffect(() => {
    const tracks = video.current?.textTracks;
    if (tracks)
      for (let i = 0; i < tracks.length; i++)
        tracks[i].mode =
          session?.subtitles[i]?.index === subtitle ? "showing" : "disabled";
  }, [subtitle, session]);
  return (
    <div className="player">
      <ErrorNote error={error} retry={() => setAttempt((v) => v + 1)} />
      <ErrorNote error={progressError} retry={() => void save()} />
      <div
        className={`screen ${playing ? "playing" : ""}`}
        ref={screen}
        onKeyDown={(event) => {
          const target = event.target as HTMLElement;
          if (target.matches("input, select, summary")) return;
          const key = event.key.toLowerCase();
          if ((key === " " || key === "enter") && target.closest("button"))
            return;
          const actions: Record<string, () => void> = {
            " ": toggle,
            k: toggle,
            arrowleft: () => seek(time - 10),
            arrowright: () => seek(time + 30),
            m: () => setMuted(!muted),
            f: fullscreen,
          };
          if (!actions[key]) return;
          event.preventDefault();
          actions[key]();
        }}
      >
        <video
          ref={video}
          playsInline
          tabIndex={-1}
          onClick={toggle}
          onPlay={() => setPlaying(true)}
          onTimeUpdate={(e) => setTime(e.currentTarget.currentTime)}
          onDurationChange={(e) =>
            Number.isFinite(e.currentTarget.duration) &&
            setLength(e.currentTarget.duration)
          }
          preload="metadata"
          aria-label={`Watch ${title}`}
          onPause={() => {
            setPlaying(false);
            void save();
          }}
          onEnded={() => void save(true)}
          onWaiting={() => setWaiting(true)}
          onPlaying={() => setWaiting(false)}
          onError={() => {
            if (session && !busy)
              setError(
                transcode
                  ? "This copy still won’t play. Check its storage is connected, then try again."
                  : "This copy won’t play. Try another format under Playback help.",
              );
          }}
        >
          {session?.subtitles.map((t) => (
            <track
              key={`${session.id}-${t.index}-${t.url}`}
              kind="subtitles"
              src={t.url}
              srcLang={t.language || "und"}
              label={trackLabel(t, "Subtitles")}
              onLoad={() => {
                const tracks = video.current?.textTracks;
                if (tracks)
                  for (let i = 0; i < tracks.length; i++)
                    tracks[i].mode =
                      session.subtitles[i]?.index === subtitle
                        ? "showing"
                        : "disabled";
              }}
            />
          ))}
        </video>
        {(busy || waiting) && (
          <div className="screen-status">
            <Loading label={busy ? "Starting" : "Buffering"} />
          </div>
        )}
        <div className="controls" role="group" aria-label="Playback controls">
          <button
            className="control play"
            aria-label={playing ? "Pause" : "Play"}
            onClick={toggle}
          >
            {playing ? (
              <Pause size={24} fill="currentColor" strokeWidth={0} />
            ) : (
              <Play size={24} fill="currentColor" strokeWidth={0} />
            )}
          </button>
          <button
            className="control jump"
            aria-label="Back 10 seconds"
            onClick={() => seek(time - 10)}
          >
            <RotateCcw size={20} strokeWidth={2.25} />
          </button>
          <button
            className="control jump"
            aria-label="Forward 30 seconds"
            onClick={() => seek(time + 30)}
          >
            <RotateCw size={20} strokeWidth={2.25} />
          </button>
          <span className="clock num" aria-hidden="true">
            {stamp(time)}
          </span>
          <input
            className="scrubber"
            type="range"
            min={0}
            max={Math.max(1, Math.floor(length))}
            step={1}
            value={Math.floor(Math.min(time, length || time))}
            aria-label="Position"
            aria-valuetext={`${stamp(time)} of ${stamp(length)}`}
            style={
              {
                "--played": `${(100 * time) / Math.max(1, length)}%`,
              } as React.CSSProperties
            }
            onChange={(e) => seek(Number(e.target.value))}
          />
          <span className="clock num" aria-hidden="true">
            {stamp(length)}
          </span>
          <button
            className="control"
            aria-label={muted ? "Unmute" : "Mute"}
            aria-pressed={muted}
            onClick={() => setMuted(!muted)}
          >
            {muted ? (
              <VolumeX size={20} strokeWidth={2.25} />
            ) : (
              <Volume2 size={20} strokeWidth={2.25} />
            )}
          </button>
          <button
            className="control"
            aria-label={full ? "Exit full screen" : "Full screen"}
            onClick={fullscreen}
          >
            {full ? (
              <Minimize size={20} strokeWidth={2.25} />
            ) : (
              <Maximize size={20} strokeWidth={2.25} />
            )}
          </button>
        </div>
      </div>
      {session && (
        <div className="tracks">
          <Field label="Audio">
            <select
              value={session.audio_index ?? ""}
              onChange={(e) => {
                position.current = video.current?.currentTime;
                setAudio(Number(e.target.value));
              }}
            >
              {asset.facts.audio_tracks.map((t) => (
                <option key={t.index} value={t.index}>
                  {trackLabel(t, "Audio track")}
                  {t.default ? " · default" : ""}
                </option>
              ))}
              {!asset.facts.audio_tracks.length && (
                <option value="">No audio track</option>
              )}
            </select>
          </Field>
          <Field label="Subtitles">
            <select
              value={subtitle}
              onChange={(e) => setSubtitle(Number(e.target.value))}
            >
              <option value={-1}>Off</option>
              {session.subtitles.map((t) => (
                <option key={t.index} value={t.index}>
                  {trackLabel(t, "Subtitles")}
                </option>
              ))}
            </select>
          </Field>
        </div>
      )}
      {session?.mode === "hls" && (
        <p className="player-note meta">Converted for this browser.</p>
      )}
      {!transcode && (
        <details className="disclosure help">
          <summary>Playback help</summary>
          <div className="help-body">
            <p>
              No picture or no sound? Play a converted stream instead. The file
              itself isn’t changed.
            </p>
            <button
              className="btn"
              onClick={() => {
                position.current = video.current?.currentTime;
                setTranscode(true);
              }}
            >
              <RefreshCw size={16} strokeWidth={2.5} />
              Try another format
            </button>
          </div>
        </details>
      )}
      {session && (
        <SubtitleRepair
          assetId={asset.id}
          audio={session.audio_index}
          selected={session.subtitles.find((t) => t.index === subtitle)}
          onReady={(tracks) =>
            setSession((current) =>
              current
                ? {
                    ...current,
                    subtitles: [
                      ...tracks,
                      ...current.subtitles.filter((t) => !t.id),
                    ],
                  }
                : current,
            )
          }
          onOffset={(id, seconds) =>
            setSession((current) =>
              current
                ? {
                    ...current,
                    subtitles: current.subtitles.map((t) =>
                      t.id === id
                        ? {
                            ...t,
                            offset: seconds,
                            url: t.url.split("?")[0] + "?offset=" + seconds,
                          }
                        : t,
                    ),
                  }
                : current,
            )
          }
        />
      )}
    </div>
  );
}
