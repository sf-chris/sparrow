import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Download, Info, Play } from "lucide-react";
import { api, post, type Asset, type Job, type Title as TitleData } from "./api";
import { Tick } from "./Brand";
import {
  Bar,
  Dialog,
  Flag,
  Meter,
  bytes,
  duration,
  episodeCode,
  inProgress,
  progress,
  transferLine,
  useResource,
} from "./ui";

type SeasonEpisode = {
  episode_number: number;
  name: string;
  overview?: string;
  still_path?: string | null;
  runtime?: number | null;
  air_date?: string | null;
};

const languages = new Intl.DisplayNames(["en"], { type: "language" });
export const languageName = (code?: string) => {
  if (!code || code === "und") return "";
  try {
    return languages.of(code.replace(/_/g, "-")) || code;
  } catch {
    return code;
  }
};
const day = (seconds?: number) =>
  seconds
    ? new Date(seconds * 1000).toLocaleDateString([], {
        day: "numeric",
        month: "short",
        year: "numeric",
      })
    : "";
const airs = (date: string) =>
  new Date(date + "T00:00:00").toLocaleDateString([], {
    day: "numeric",
    month: "short",
  });
const open = (job: Job) => ["active", "paused"].includes(job.status);

/** A series' whole season, every episode with its picture: playable,
 * downloading, requested, not downloaded, or still to air. */
export function SeasonEpisodes({
  title,
  assets,
  start,
  onSubtitles,
  canRequest = false,
  onRequested,
}: {
  title: TitleData;
  assets: Asset[];
  start?: number;
  onSubtitles: (asset: Asset) => void;
  canRequest?: boolean;
  onRequested?: () => void;
}) {
  const seasons = useMemo(
    () =>
      title.seasons
        .filter((s) => s.episode_count > 0)
        // Specials (season 0) go last.
        .sort(
          (a, b) => (a.season_number || 999) - (b.season_number || 999),
        ),
    [title.seasons],
  );
  const wantedSeason = title.jobs
    .filter(open)
    .flatMap((j) => Object.keys(j.wanted_episodes).map(Number))[0];
  const first =
    start ??
    assets.find((a) => a.episode)?.season ??
    wantedSeason ??
    seasons[0]?.season_number ??
    1;
  const [season, setSeason] = useState(first);
  useEffect(() => setSeason(first), [title.tmdb_id]);
  const listing = useResource(
    () =>
      api<SeasonEpisode[]>(`/titles/tv/${title.tmdb_id}/seasons/${season}`),
    [title.tmdb_id, season],
  );
  const here = assets.filter((a) => (a.season ?? 1) === season);
  const owned = new Map(here.filter((a) => a.episode).map((a) => [a.episode!, a]));
  const wanted = new Map<number, Job>();
  for (const job of title.jobs.filter(open))
    for (const n of job.wanted_episodes[String(season)] || []) wanted.set(n, job);
  const episodes: SeasonEpisode[] = listing.data?.length
    ? listing.data
    : [...owned.keys()]
        .sort((a, b) => a - b)
        .map((n) => ({ episode_number: n, name: "" }));
  const extras = here.filter(
    (a) => !a.episode || !episodes.some((e) => e.episode_number === a.episode),
  );
  const today = new Date().toISOString().slice(0, 10);
  const count = (n: number) =>
    here.filter((a) => a.episode).length
      ? ` · ${here.filter((a) => a.episode).length} of ${n}`
      : "";
  return (
    <section className="episodes" aria-labelledby="episodes">
      <Bar id="episodes" title="Episodes">
        {seasons.length > 1 && (
          <select
            className="bar-select"
            aria-label="Season"
            value={season}
            onChange={(event) => setSeason(Number(event.target.value))}
          >
            {seasons.map((s) => (
              <option key={s.season_number} value={s.season_number}>
                {s.season_number ? `Season ${s.season_number}` : "Specials"}
                {s.season_number === season ? count(s.episode_count) : ""}
              </option>
            ))}
          </select>
        )}
      </Bar>
      {listing.error && !episodes.length ? (
        <p className="muted episodes-note">{listing.error}</p>
      ) : (
        <ol className="season-list">
          {episodes.map((episode) => (
            <EpisodeRow
              key={episode.episode_number}
              season={season}
              episode={episode}
              asset={owned.get(episode.episode_number)}
              job={wanted.get(episode.episode_number)}
              today={today}
              onSubtitles={onSubtitles}
              onRequest={
                canRequest && title.tmdb_id
                  ? async () => {
                      await post("/jobs", {
                        tmdb_id: title.tmdb_id,
                        media_type: "tv",
                        wanted_episodes: { [season]: [episode.episode_number] },
                      });
                      onRequested?.();
                    }
                  : undefined
              }
            />
          ))}
          {extras.map((asset) => (
            <EpisodeRow
              key={asset.id}
              season={season}
              episode={{ episode_number: asset.episode || 0, name: "Extra" }}
              asset={asset}
              today={today}
              onSubtitles={onSubtitles}
            />
          ))}
        </ol>
      )}
    </section>
  );
}

function EpisodeRow({
  season,
  episode,
  asset,
  job,
  today,
  onSubtitles,
  onRequest,
}: {
  season: number;
  episode: SeasonEpisode;
  asset?: Asset;
  job?: Job;
  today: string;
  onSubtitles: (asset: Asset) => void;
  onRequest?: () => Promise<void>;
}) {
  const [asking, setAsking] = useState(false);
  const [askError, setAskError] = useState("");
  const ready = asset?.state === "ready";
  const watching = asset && inProgress(asset);
  const code = `S${season} E${episode.episode_number}`;
  const percent = Math.floor(100 * (job?.transfer?.progress || 0));
  const fetching = !asset && job?.transfer?.state === "downloading";
  const aired = !episode.air_date || episode.air_date <= today;
  const state = ready
    ? "ready"
    : asset
      ? "held"
      : job
        ? "coming"
        : aired
          ? "missing"
          : "unaired";
  const still = episode.still_path
    ? `https://image.tmdb.org/t/p/w300${episode.still_path}`
    : "";
  const minutes = asset
    ? duration(asset.facts.duration)
    : episode.runtime
      ? `${episode.runtime} min`
      : "";
  const picture = (
    <>
      {still ? (
        <img src={still} alt="" loading="lazy" />
      ) : (
        <span className="ep-blank num" aria-hidden="true">
          {episode.episode_number || "•"}
        </span>
      )}
      {ready && (
        <span className="ep-play" aria-hidden="true">
          <Play size={20} fill="currentColor" strokeWidth={0} />
        </span>
      )}
      {watching && (
        <span className="ep-meter">
          <Meter value={progress(asset!)} label={`Watch progress for ${code}`} />
        </span>
      )}
      {fetching && (
        <span className="ep-meter">
          <Meter value={percent} label={`Download progress for ${code}`} />
        </span>
      )}
    </>
  );
  return (
    <li className={`ep ${state} ${asset?.watch?.watched ? "watched" : ""}`}>
      {ready ? (
        <Link
          className="ep-still"
          to={`/watch/${asset!.id}`}
          aria-label={`${watching ? "Resume" : "Play"} ${code}`}
          tabIndex={-1}
        >
          {picture}
        </Link>
      ) : (
        <span className="ep-still">{picture}</span>
      )}
      <div className="ep-copy">
        <p className="ep-head">
          <span className="ep-no num">{episode.episode_number || ""}</span>
          <strong>{episode.name || `Episode ${episode.episode_number}`}</strong>
          <span className="meta num">{minutes}</span>
        </p>
        {episode.overview && <p className="ep-blurb">{episode.overview}</p>}
        <p className="ep-state meta">
          {ready ? (
            asset!.watch?.watched ? (
              <span className="done-note">
                <Tick /> Watched
              </span>
            ) : watching ? (
              `${progress(asset!)}% watched`
            ) : (
              "Ready to watch"
            )
          ) : asset ? (
            <Flag value={asset.state} />
          ) : job ? (
            transferLine(job)
          ) : asking ? (
            "Requesting…"
          ) : askError ? (
            <span className="ep-error">{askError}</span>
          ) : aired ? (
            "Not downloaded"
          ) : (
            `Airs ${airs(episode.air_date!)}`
          )}
        </p>
      </div>
      {ready && (
        <span className="episode-actions">
          <button
            className="btn quiet"
            onClick={() => onSubtitles(asset!)}
            aria-label={`Subtitles for ${code}`}
          >
            Subtitles
          </button>
          <Link className="btn" data-nav to={`/watch/${asset!.id}`}>
            <Play size={15} fill="currentColor" strokeWidth={0} />
            {watching ? "Resume" : "Play"}
            <span className="sr-only"> {code}</span>
          </Link>
        </span>
      )}
      {state === "missing" && onRequest && (
        <span className="episode-actions">
          <button
            className="icon-button"
            disabled={asking}
            aria-label={`Download ${code}`}
            title={`Download ${code}`}
            onClick={async () => {
              setAsking(true);
              setAskError("");
              try {
                await onRequest();
              } catch (e) {
                setAskError((e as Error).message);
              } finally {
                setAsking(false);
              }
            }}
          >
            <Download size={18} strokeWidth={2} />
          </button>
        </span>
      )}
    </li>
  );
}

/** The people in it, with their faces. */
export function CastRow({ cast }: { cast: NonNullable<TitleData["cast"]> }) {
  if (!cast.length) return null;
  return (
    <section className="cast" aria-labelledby="cast" id="cast-list">
      <Bar id="cast" title="Cast" />
      <ul className="cast-row" tabIndex={0} aria-label="Cast">
        {cast.map((person) => (
          <li key={person.name + person.character}>
            <span className="cast-photo">
              {person.photo_url ? (
                <img src={person.photo_url} alt="" loading="lazy" />
              ) : (
                <span aria-hidden="true">
                  {person.name
                    .split(" ")
                    .map((part) => part[0])
                    .slice(0, 2)
                    .join("")}
                </span>
              )}
            </span>
            <strong>{person.name}</strong>
            {person.character && (
              <span className="meta">{person.character}</span>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

export type Place = { id: string; label: string; move?: () => void };

/** The small ⓘ: everything about a title that isn't watching or requesting
 * it. What it is and who made it, then what is stored, where, how big, since
 * when and who asked for it. */
export function TitleInfo({
  title,
  assets,
  places,
}: {
  title: TitleData;
  assets: Asset[];
  places: Place[];
}) {
  const [shown, setShown] = useState(false);
  const tv = title.media_type === "tv";
  const total = assets.reduce((sum, a) => sum + (a.facts.size_bytes || 0), 0);
  const added = assets.map((a) => a.verified_at || 0).filter(Boolean).sort();
  const requests = [...title.jobs].sort(
    (a, b) => (a.created_at || 0) - (b.created_at || 0),
  );
  const sorted = [...assets].sort(
    (a, b) =>
      (a.season || 0) - (b.season || 0) || (a.episode || 0) - (b.episode || 0),
  );
  return (
    <>
      <button
        className="info-button"
        aria-label="Details"
        title="Details"
        onClick={() => setShown(true)}
      >
        <Info size={18} strokeWidth={2} />
      </button>
      {shown && (
        <Dialog title={title.title} onClose={() => setShown(false)}>
          {title.overview && <p className="info-about">{title.overview}</p>}
          <dl className="info-list">
            {!!title.makers?.length && (
              <div>
                <dt>{tv ? "Created by" : "Directed by"}</dt>
                <dd>{title.makers.join(", ")}</dd>
              </div>
            )}
            {!!title.genres?.length && (
              <div>
                <dt>Genres</dt>
                <dd>{title.genres.join(", ")}</dd>
              </div>
            )}
            {!!title.cast?.length && (
              <div>
                <dt>Starring</dt>
                <dd>{title.cast.map((person) => person.name).join(", ")}</dd>
              </div>
            )}
          </dl>
          <h3 className="info-head">In your collection</h3>
          <dl className="info-list">
            <div>
              <dt>Stored</dt>
              <dd>
                {assets.length
                  ? `${assets.length} ${
                      tv
                        ? assets.length === 1
                          ? "episode"
                          : "episodes"
                        : assets.length === 1
                          ? "copy"
                          : "copies"
                    } · ${bytes(total)}`
                  : "Nothing yet"}
              </dd>
            </div>
            {!!places.length && (
              <div>
                <dt>On</dt>
                <dd>
                  {places.map((place) => (
                    <span className="info-line" key={place.id}>
                      {place.label}
                      {place.move && (
                        <>
                          {" "}
                          <button
                            className="link"
                            onClick={() => {
                              setShown(false);
                              place.move?.();
                            }}
                          >
                            Move
                          </button>
                        </>
                      )}
                    </span>
                  ))}
                </dd>
              </div>
            )}
            {!!added.length && (
              <div>
                <dt>Added</dt>
                <dd>
                  {day(added[0])}
                  {day(added[added.length - 1]) !== day(added[0])
                    ? ` – ${day(added[added.length - 1])}`
                    : ""}
                </dd>
              </div>
            )}
            {!!requests.length && (
              <div>
                <dt>Requested by</dt>
                <dd>
                  {requests.map((job) => (
                    <span className="info-line" key={job.id}>
                      {job.requested_by || "Someone"} · {day(job.created_at)}
                      {tv && Object.keys(job.wanted_episodes).length
                        ? ` · ${Object.entries(job.wanted_episodes)
                            .map(([s, eps]) => `S${s} E${eps.join(", E")}`)
                            .join("; ")}`
                        : ""}
                    </span>
                  ))}
                </dd>
              </div>
            )}
          </dl>
          {!!sorted.length && (
            <table className="info-files">
              <caption className="sr-only">Files</caption>
              <thead>
                <tr>
                  <th scope="col">{tv ? "Episode" : "Copy"}</th>
                  <th scope="col">Picture</th>
                  <th scope="col">Audio</th>
                  <th scope="col">Size</th>
                  <th scope="col">Added</th>
                </tr>
              </thead>
              <tbody>
                {sorted.map((asset, index) => (
                  <tr key={asset.id}>
                    <th scope="row" className="num">
                      {asset.episode ? episodeCode(asset) : `Copy ${index + 1}`}
                    </th>
                    <td>{asset.facts.quality || "—"}</td>
                    <td>
                      {[
                        ...new Set(
                          asset.facts.audio_tracks
                            .map((t) => languageName(t.language))
                            .filter(Boolean),
                        ),
                      ].join(", ") || "—"}
                    </td>
                    <td className="num">{bytes(asset.facts.size_bytes)}</td>
                    <td className="num">{day(asset.verified_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Dialog>
      )}
    </>
  );
}
