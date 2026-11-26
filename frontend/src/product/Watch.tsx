import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  Airplay,
  ArrowLeft,
  Captions,
  Cast,
  Maximize,
  Minimize,
  Minus,
  Pause,
  Play,
  Plus,
  RotateCcw,
  RotateCw,
  SkipForward,
  Tv,
  Volume2,
  VolumeX,
} from "lucide-react";
import { api, patch, post, type Asset, type Effective, type Item } from "./api";
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
  /** Addresses a TV can fetch without this browser's sign-in. */
  cast: {
    url: string;
    stream: string | null;
    subtitles: { index: number; url: string }[];
  };
};
type RemotePlayback = EventTarget & {
  state: "connecting" | "connected" | "disconnected";
  watchAvailability(callback: (available: boolean) => void): Promise<number>;
  cancelWatchAvailability(id?: number): Promise<void>;
  prompt(): Promise<void>;
};
/** Safari's AirPlay and the standard Remote Playback API (Chrome's cast). */
type TvVideo = HTMLVideoElement & {
  remote?: RemotePlayback;
  webkitShowPlaybackTargetPicker?: () => void;
  webkitCurrentPlaybackTargetIsWireless?: boolean;
  webkitEnterFullscreen?: () => void;
};
const AIRPLAY =
  typeof window !== "undefined" && "WebKitPlaybackTargetAvailabilityEvent" in window;
const NATIVE_HLS =
  typeof document !== "undefined" &&
  !!document.createElement("video").canPlayType("application/vnd.apple.mpegurl");
const absolute = (path: string) => new URL(path, window.location.href).href;
const languageNames = new Intl.DisplayNames(["en"], { type: "language" });
const stamp = (seconds: number) => {
  const total = Math.max(0, Math.floor(seconds || 0));
  const h = Math.floor(total / 3600),
    m = Math.floor((total % 3600) / 60),
    s = String(total % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${s}` : `${m}:${s}`;
};
/** Show the chosen subtitles. Tracks are matched by element, not position:
 *  Safari also lists the text tracks inside the file, which shifts positions.
 *  On a TV the stream itself carries the one chosen track; show that. */
function showSubtitle(
  element: HTMLVideoElement | null,
  chosen: unknown,
  inStream = false,
) {
  if (!element) return;
  const own = new Set<TextTrack>();
  element.querySelectorAll("track").forEach((el) => {
    own.add(el.track);
    el.track.mode = el.dataset.index === String(chosen) ? "showing" : "disabled";
  });
  for (let i = 0; i < element.textTracks.length; i++) {
    const track = element.textTracks[i];
    if (own.has(track)) continue;
    const text = track.kind === "subtitles" || track.kind === "captions";
    track.mode = inStream && text && chosen !== -1 ? "showing" : "disabled";
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
  // Whether a TV is in reach, and whether this video is playing on one.
  const [tvs, setTvs] = useState(false);
  const [tv, setTv] = useState<"off" | "connecting" | "on">("off");
  const applied = useRef("");
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
  const [ended, setEnded] = useState(false);
  // A series' next playable episode, offered when this one ends.
  const siblings = useResource(
    async () => (asset.episode ? api<Item>(`/items/${asset.item_id}`) : null),
    [asset.item_id],
  );
  const order = (a: Pick<Asset, "season" | "episode">) =>
    (a.season ?? 1) * 10000 + (a.episode || 0);
  const upNext = siblings.data?.assets
    .filter((a) => a.state === "ready" && a.episode && order(a) > order(asset))
    .sort((a, b) => order(a) - order(b))[0];
  const shown = chrome || !playing || panel || busy || tv !== "off";
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
    else if (screen.current?.requestFullscreen)
      void screen.current.requestFullscreen().catch(() => {});
    // An iPhone has no page full screen; its own player does it instead.
    else
      (video.current as TvVideo | null)?.webkitEnterFullscreen?.();
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
    // A browser that plays the address itself gets it from the source effect
    // below, so a TV can take it over; others convert through hls.js.
    if (session.mode !== "direct" && !NATIVE_HLS) {
      element.addEventListener("loadedmetadata", metadata);
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
    }
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
      applied.current = "";
    };
  }, [session?.id]);
  // What the video element plays. On a TV with subtitles chosen, the stream
  // that carries them: a TV shows only what the stream itself contains.
  const stream = session?.cast.stream;
  const tvSubtitles = tv === "on" && subtitle !== -1 && !!stream && NATIVE_HLS;
  const source =
    session && (session.mode === "direct" || NATIVE_HLS)
      ? tvSubtitles
        ? `${stream}?subtitle=${subtitle}`
        : session.cast.url
      : null;
  useEffect(() => {
    const element = video.current;
    if (!element || !session || !source || applied.current === source) return;
    // A switch keeps the place and whether it was playing.
    const first = !applied.current;
    const at = first ? session.position : element.currentTime;
    const resume = first || !element.paused;
    applied.current = source;
    const resumed = () => {
      setError("");
      element.currentTime = at;
      if (resume) void element.play().catch(() => {});
    };
    element.addEventListener("loadedmetadata", resumed, { once: true });
    element.src = absolute(
      source === session.cast.url ? source : `${source}&start=${at.toFixed(1)}`,
    );
    return () => element.removeEventListener("loadedmetadata", resumed);
  }, [session?.id, source]);
  // AirPlay (Safari) or the browser's cast: is a TV in reach, are we on it?
  useEffect(() => {
    const element = video.current as TvVideo | null;
    if (!element) return;
    if (AIRPLAY) {
      const reach = (event: Event) =>
        setTvs(
          (event as Event & { availability?: string }).availability ===
            "available",
        );
      const wireless = () =>
        setTv(element.webkitCurrentPlaybackTargetIsWireless ? "on" : "off");
      element.addEventListener("webkitplaybacktargetavailabilitychanged", reach);
      element.addEventListener("webkitcurrentplaybacktargetiswirelesschanged", wireless);
      return () => {
        element.removeEventListener("webkitplaybacktargetavailabilitychanged", reach);
        element.removeEventListener(
          "webkitcurrentplaybacktargetiswirelesschanged",
          wireless,
        );
      };
    }
    const remote = element.remote;
    if (!remote) return;
    const update = () =>
      setTv(
        remote.state === "connected"
          ? "on"
          : remote.state === "connecting"
            ? "connecting"
            : "off",
      );
    const names = ["connecting", "connect", "disconnect"];
    names.forEach((name) => remote.addEventListener(name, update));
    return () => names.forEach((name) => remote.removeEventListener(name, update));
  }, []);
  useEffect(() => {
    const remote = (video.current as TvVideo | null)?.remote;
    if (AIRPLAY || !remote) return;
    // A converted stream played through hls.js can't be handed to a TV.
    let live = true,
      id: number | undefined;
    remote
      .watchAvailability((available) => live && setTvs(available))
      .then((n) => (id = n))
      .catch(() => live && setTvs(false));
    return () => {
      live = false;
      if (id !== undefined)
        void remote.cancelWatchAvailability(id).catch(() => {});
    };
  }, [source]);
  function chooseTv() {
    const element = video.current as TvVideo | null;
    if (AIRPLAY) element?.webkitShowPlaybackTargetPicker?.();
    else void element?.remote?.prompt().catch(() => {});
  }
  useEffect(() => {
    const element = video.current;
    const apply = () => showSubtitle(element, subtitle, tvSubtitles);
    apply();
    // Safari picks tracks again when one loads or appears; choose again then.
    const tracks = element ? [...element.querySelectorAll("track")] : [];
    tracks.forEach((t) => t.addEventListener("load", apply));
    element?.textTracks.addEventListener("addtrack", apply);
    return () => {
      tracks.forEach((t) => t.removeEventListener("load", apply));
      element?.textTracks.removeEventListener("addtrack", apply);
    };
  }, [subtitle, session, tvSubtitles]);
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
        x-webkit-airplay="allow"
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
          setEnded(false);
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
        onEnded={() => {
          setEnded(true);
          void save(true);
        }}
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
        {!tvSubtitles &&
          session?.subtitles.map((t) => (
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
      {tv !== "off" && (
        <div className="screen-status on-tv" role="status">
          <Tv size={40} strokeWidth={1.75} aria-hidden="true" />
          <p>{tv === "on" ? "Playing on your TV" : "Connecting to your TV"}</p>
        </div>
      )}
      {(busy || waiting) && tv === "off" && (
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
        {(tvs || tv !== "off") && (
          <button
            className={`tv-button${tv === "on" ? " on-tv" : ""}`}
            aria-label={
              tv === "on" ? "On your TV. Choose where to play" : undefined
            }
            onClick={chooseTv}
          >
            {AIRPLAY ? (
              <Airplay size={20} strokeWidth={2.25} aria-hidden="true" />
            ) : (
              <Cast size={20} strokeWidth={2.25} aria-hidden="true" />
            )}
            {tv === "on" ? "On your TV" : "Play on TV"}
          </button>
        )}
      </header>
      {ended && upNext && (
        <div className="up-next">
          <span className="up-next-label">Up next</span>
          <button
            className="up-next-play"
            autoFocus
            onClick={() => navigate(`/watch/${upNext.id}`, { replace: true })}
          >
            <SkipForward size={20} strokeWidth={2.25} aria-hidden="true" />
            Play {episodeCode(upNext)}
          </button>
        </div>
      )}
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
        <span className="clock total num" aria-hidden="true">
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
