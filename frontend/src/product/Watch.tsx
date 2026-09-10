import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ArrowLeft, RefreshCw } from "lucide-react";
import { api, post, type Asset, type Effective } from "./api";
import { SubtitleRepair, type Caption } from "./SubtitleRepair";
import { ErrorNote, Field, Loading, Page, useResource } from "./ui";

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
  return (
    <Page
      className="sp-player-page"
      title={resource.data?.title || "Your player"}
    >
      <Link
        className="sp-back"
        to={
          resource.data ? `/items/${resource.data.asset.item_id}` : "/library"
        }
      >
        <ArrowLeft size={15} />
        Back to title
      </Link>
      <ErrorNote error={resource.error} retry={resource.refresh} />
      {resource.loading && !resource.data ? (
        <Loading label="Opening your media…" />
      ) : (
        resource.data && <Player key={assetId} {...resource.data} />
      )}
    </Page>
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
      setProgressError(
        `Your progress could not be saved. ${(e as Error).message}`,
      );
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
              "This browser cannot play the prepared stream. Try a current Chrome, Firefox, Edge or Safari browser.",
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
                "Playback was interrupted. Check that your storage is connected, then try again.",
              );
          });
          hls.loadSource(session.url);
          hls.attachMedia(element);
        })
        .catch(() =>
          setError(
            "The player could not load. Refresh the page and try again.",
          ),
        );
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
    <div className="sp-player">
      <ErrorNote error={error} retry={() => setAttempt((v) => v + 1)} />
      <ErrorNote error={progressError} retry={() => void save()} />
      <div className="sp-video-wrap">
        <video
          ref={video}
          controls
          playsInline
          preload="metadata"
          aria-label={`Watch ${title}`}
          onPause={() => void save()}
          onEnded={() => void save(true)}
          onWaiting={() => setWaiting(true)}
          onPlaying={() => setWaiting(false)}
          onError={() => {
            if (session && !busy)
              setError(
                transcode
                  ? "This copy still could not play. Check that its storage is connected, then try again."
                  : "This copy could not play. Open Playback help to try another format, or reconnect its storage.",
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
          <div className="sp-video-status">
            <Loading
              label={busy ? "Opening playback…" : "Waiting for your media…"}
            />
          </div>
        )}
      </div>
      {session && (
        <div className="sp-player-settings sp-form-grid">
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
      <div className="sp-savebar">
        <span className="sp-muted">
          {session?.mode === "hls"
            ? "Converting video for this browser."
            : "Plays directly from your collection."}{" "}
          Your progress saves automatically.
        </span>
      </div>
      {!transcode && (
        <details className="sp-playback-help">
          <summary>Playback help</summary>
          <p className="sp-muted">
            Video won’t play, or there’s no sound? Try converting it to a format
            this browser can play. This may take a moment; your original file
            stays unchanged.
          </p>
          <button
            className="sp-button quiet"
            onClick={() => {
              position.current = video.current?.currentTime;
              setTranscode(true);
            }}
          >
            <RefreshCw size={15} />
            Try another playback format
          </button>
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
