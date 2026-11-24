import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  ArrowLeft,
  Captions,
  Maximize,
  Minimize,
  Minus,
  Pause,
  Play,
  Plus,
  RotateCcw,
  RotateCw,
  Volume2,
  VolumeX,
} from "lucide-react";
import { api, patch, post, type Asset, type Effective } from "./api";
import { type Caption } from "./SubtitleRepair";
import { ErrorNote, Loading, episodeCode, useResource } from "./ui";

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
/** Show the chosen subtitles. Tracks are matched by element, not position:
 *  Safari also lists the text tracks inside the file, which shifts positions. */
function showSubtitle(element: HTMLVideoElement | null, chosen: unknown) {
  if (!element) return;
  const own = new Set<TextTrack>();
  element.querySelectorAll("track").forEach((el) => {
    own.add(el.track);
    el.track.mode = el.dataset.index === String(chosen) ? "showing" : "disabled";
  });
  for (let i = 0; i < element.textTracks.length; i++) {
    if (!own.has(element.textTracks[i])) element.textTracks[i].mode = "disabled";
  }
}

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
/** Watching takes the whole screen: no navigation, the picture edge to edge,
 * and the controls over it only while they are wanted. Track choices live in
 * a small panel; subtitle repair is on the title page. */
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
  useEffect(() => {
    if (data) document.title = `${data.title} · Sparrow`;
  }, [data?.title]);
  return (
    <main id="main-content" className="watch on-air">
      {data ? (
        <Player key={assetId} {...data} />
      ) : (
        <div className="watch-waiting">
          <ErrorNote error={resource.error} retry={resource.refresh} />
          {resource.loading && <Loading page label="Starting" />}
        </div>
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
  const [panel, setPanel] = useState(false);
  // The controls and title show while the pointer moves, while paused and
  // while the panel is open; otherwise they fade after a moment.
  const [chrome, setChrome] = useState(true);
  const hideTimer = useRef(0);
  const wake = useCallback(() => {
    setChrome(true);
    clearTimeout(hideTimer.current);
    hideTimer.current = window.setTimeout(() => setChrome(false), 2800);
  }, []);
  useEffect(() => () => clearTimeout(hideTimer.current), []);
  const shown = chrome || !playing || panel || busy;
  const navigate = useNavigate();
  const back = () =>
    (window.history.state?.idx ?? 0) > 0
      ? navigate(-1)
      : navigate(`/items/${asset.item_id}`);
  async function nudge(track: Caption, seconds: number) {
    if (!track.id) return;
    const value = Math.round(seconds * 4) / 4;
    try {
      await patch(`/subtitles/tracks/${track.id}/offset`, { seconds: value });
      setSession((current) =>
        current
          ? {
              ...current,
              subtitles: current.subtitles.map((t) =>
                t.id === track.id
                  ? {
                      ...t,
                      offset: value,
                      url: t.url.split("?")[0] + "?offset=" + value,
                    }
                  : t,
              ),
            }
          : current,
      );
    } catch (e) {
      setError((e as Error).message);
    }
  }
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
    const element = video.current;
    const apply = () => showSubtitle(element, subtitle);
    apply();
    // Safari picks tracks again when one loads or appears; choose again then.
    const tracks = element ? [...element.querySelectorAll("track")] : [];
    tracks.forEach((t) => t.addEventListener("load", apply));
    element?.textTracks.addEventListener("addtrack", apply);
    return () => {
      tracks.forEach((t) => t.removeEventListener("load", apply));
      element?.textTracks.removeEventListener("addtrack", apply);
    };
  }, [subtitle, session]);
  const selected = session?.subtitles.find((t) => t.index === subtitle);
  return (
    <div
      className={`theatre ${shown ? "chrome" : ""} ${playing ? "playing" : ""}`}
      ref={screen}
      onPointerMove={(event) => event.pointerType === "mouse" && wake()}
      onKeyDown={(event) => {
        wake();
        const target = event.target as HTMLElement;
        const key = event.key.toLowerCase();
        if (key === "escape" && panel) {
          event.preventDefault();
          setPanel(false);
          return;
        }
        if (target.matches("input, select, summary")) return;
        if ((key === " " || key === "enter") && target.closest("button"))
          return;
        const actions: Record<string, () => void> = {
          " ": toggle,
          k: toggle,
          arrowleft: () => seek(time - 10),
          arrowright: () => seek(time + 30),
          m: () => setMuted(!muted),
          f: fullscreen,
          c: () => setPanel(!panel),
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
        onPointerUp={(event) => {
          // A tap shows or hides the controls; a click plays or pauses.
          if (event.pointerType === "mouse") {
            toggle();
            wake();
          } else if (shown && playing) setChrome(false);
          else wake();
          if (panel) setPanel(false);
        }}
        onPlay={() => {
          setPlaying(true);
          wake();
        }}
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
        onCanPlay={() => setWaiting(false)}
        onPlaying={() => setWaiting(false)}
        onError={() => {
          if (session && !busy)
            setError(
              transcode
                ? "This copy still won’t play. Check its storage is connected, then try again."
                : "This copy won’t play. Try another format under Audio and subtitles.",
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
            data-index={t.index}
          />
        ))}
      </video>
      {(busy || waiting) && (
        <div className="screen-status">
          <Loading label={busy ? "Starting" : "Buffering"} />
        </div>
      )}
      <header className="theatre-top">
        <button className="control" aria-label="Back to title" onClick={back}>
          <ArrowLeft size={22} strokeWidth={2.5} />
        </button>
        <h1>
          {title}
          {asset.episode ? (
            <span className="num"> {episodeCode(asset)}</span>
          ) : null}
        </h1>
      </header>
      {(error || progressError) && (
        <div className="theatre-errors">
          <ErrorNote error={error} retry={() => setAttempt((v) => v + 1)} />
          <ErrorNote error={progressError} retry={() => void save()} />
        </div>
      )}
      {panel && session && (
        <div
          className="theatre-panel"
          role="dialog"
          aria-label="Audio and subtitles"
        >
          <fieldset className="track-choices">
            <legend>Audio</legend>
            {asset.facts.audio_tracks.map((t) => (
              <label className="track-choice" key={t.index}>
                <input
                  type="radio"
                  name="audio"
                  checked={session.audio_index === t.index}
                  onChange={() => {
                    position.current = video.current?.currentTime;
                    setAudio(t.index);
                  }}
                />
                <span>{trackLabel(t, "Audio track")}</span>
              </label>
            ))}
            {!asset.facts.audio_tracks.length && (
              <p className="meta">No audio track</p>
            )}
          </fieldset>
          <fieldset className="track-choices">
            <legend>Subtitles</legend>
            <label className="track-choice">
              <input
                type="radio"
                name="subtitles"
                checked={subtitle === -1}
                onChange={() => setSubtitle(-1)}
              />
              <span>Off</span>
            </label>
            {session.subtitles.map((t) => (
              <label className="track-choice" key={t.index}>
                <input
                  type="radio"
                  name="subtitles"
                  checked={subtitle === t.index}
                  onChange={() => setSubtitle(t.index)}
                />
                <span>{trackLabel(t, "Subtitles")}</span>
              </label>
            ))}
            {selected?.id && (
              <div className="nudge" role="group" aria-label="Subtitle timing">
                <button
                  className="control"
                  aria-label="Show subtitles earlier"
                  onClick={() => void nudge(selected, (selected.offset || 0) - 0.25)}
                >
                  <Minus size={16} strokeWidth={2.5} />
                </button>
                <span className="num">
                  {(selected.offset || 0) > 0 ? "+" : ""}
                  {(selected.offset || 0).toFixed(2)}s
                </span>
                <button
                  className="control"
                  aria-label="Show subtitles later"
                  onClick={() => void nudge(selected, (selected.offset || 0) + 0.25)}
                >
                  <Plus size={16} strokeWidth={2.5} />
                </button>
              </div>
            )}
          </fieldset>
          <p className="panel-foot meta">
            {session.mode === "hls" ? "Converted for this browser. " : ""}
            {!transcode && (
              <button
                className="link"
                onClick={() => {
                  position.current = video.current?.currentTime;
                  setTranscode(true);
                  setPanel(false);
                }}
              >
                No picture or sound? Try another format
              </button>
            )}
          </p>
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
          aria-label="Audio and subtitles"
          aria-expanded={panel}
          disabled={!session}
          onClick={() => setPanel(!panel)}
        >
          <Captions size={22} strokeWidth={2.25} />
        </button>
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
  );
}
