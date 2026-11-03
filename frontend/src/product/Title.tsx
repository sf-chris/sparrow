import { PreferenceFields } from "./Preferences";
import CollectionCare from "./CollectionCare";
import { SubtitleRepair } from "./SubtitleRepair";
import { Still, Tick } from "./Brand";
import { useWebSocket } from "../hooks/useWebSocket";
import { useEffect, useState } from "react";
import { Link, useParams, useLocation } from "react-router-dom";
import { ArrowLeft, Play } from "lucide-react";
import {
  api,
  post,
  type Title as TitleData,
  type Item,
  type User,
  type Asset,
  type Preferences,
} from "./api";
import {
  Bar,
  Cover,
  Dialog,
  Empty,
  ErrorNote,
  Field,
  Flag,
  Loading,
  Meter,
  Page,
  bytes,
  duration,
  episodeCode,
  inProgress,
  progress,
  useResource,
} from "./ui";
import { scope } from "./Collection";
import {
  StorageOptions,
  places,
  usualPlace,
  useDestinations,
  type Destinations,
} from "./StoragePicker";

type Placed = { reason?: string; error?: string; name?: string };

/** An air date the way a listings page prints it: "Airs 5 Oct". */
const airs = (date: string) =>
  new Date(date + "T00:00").toLocaleDateString([], {
    day: "numeric",
    month: "short",
  });

export default function Title({ user }: { user: User }) {
  const { mediaType, tmdbId, itemId } = useParams();
  const location = useLocation();
  const previous = location.state?.from;
  const returnTo =
    typeof previous === "string" && /^\/(discover)?(\?|$)/.test(previous)
      ? previous
      : "/";
  const resource = useResource(async () => {
    if (itemId) {
      const item = await api<Item>(`/items/${itemId}`);
      if (item.tmdb_id)
        return api<TitleData>(`/titles/${item.media_type}/${item.tmdb_id}`);
      return {
        title: item.title,
        year: String(item.year || ""),
        overview: item.overview,
        poster_url: item.poster_url,
        backdrop_url: item.backdrop_url,
        items: [item],
        jobs: [],
        seasons: [],
        media_type: item.media_type,
        tmdb_id: item.tmdb_id,
      } as TitleData;
    }
    return api<TitleData>(`/titles/${mediaType}/${tmdbId}`);
  }, [mediaType, tmdbId, itemId]);
  useWebSocket(() => void resource.refresh());
  const [requestOpen, setRequestOpen] = useState(false);
  const [moving, setMoving] = useState<Item | null>(null);
  const destinations = useDestinations();
  const moves = useResource(
    async () =>
      user.role === "admin" && resource.data?.items.length
        ? (
            await Promise.all(
              resource.data.items.map((i) =>
                api<Move[]>(`/admin/moves?item_id=${i.id}`),
              ),
            )
          ).flat()
        : [],
    [resource.data],
  );
  const movingNow = moves.data?.find((m) =>
    ["copying", "switching", "cleaning"].includes(m.state),
  );
  useEffect(() => {
    if (!movingNow) return;
    const timer = setInterval(() => void moves.refresh(), 3000);
    return () => clearInterval(timer);
  }, [movingNow?.id]);
  const [subtitleAsset, setSubtitleAsset] = useState<Asset | null>(null);
  const [visibleSeason, setVisibleSeason] = useState("all");
  useEffect(() => setVisibleSeason("all"), [mediaType, tmdbId, itemId]);
  const title = resource.data;
  useEffect(() => {
    if (title) document.title = `${title.title} · Sparrow`;
  }, [title?.title]);
  const assets = title?.items.flatMap((i) => i.assets) || [];
  const next =
    assets.find((a) => a.state === "ready" && inProgress(a)) ||
    assets.find((a) => a.state === "ready" && !a.watch?.watched) ||
    assets.find((a) => a.state === "ready");
  const active = title?.jobs.find((j) =>
    ["active", "paused"].includes(j.status),
  );
  const backTo = returnTo.startsWith("/discover") ? "Find" : "Guide";
  const back = (
    <Link className="back" to={returnTo} aria-label={`Back to ${backTo}`}>
      <ArrowLeft size={18} strokeWidth={2.5} />
      {backTo}
    </Link>
  );
  if (!title)
    return (
      <Page title="" className="title-page">
        {back}
        <ErrorNote error={resource.error} retry={resource.refresh} />
        {resource.loading && <Loading label="Opening this title" />}
      </Page>
    );
  const tv = title.media_type === "tv";
  const seasons = [...new Set(assets.map((asset) => asset.season || 1))].sort(
    (a, b) => a - b,
  );
  const shown = [...assets]
    .filter(
      (asset) =>
        visibleSeason === "all" || String(asset.season || 1) === visibleSeason,
    )
    .sort(
      (a, b) =>
        (a.season || 0) - (b.season || 0) ||
        (a.episode || 0) - (b.episode || 0),
    );
  const seasonCount = title.seasons.filter((s) => s.season_number > 0).length;
  return (
    <Page title="" className="title-page">
      {back}
      <ErrorNote error={resource.error} retry={resource.refresh} />
      <article className={`feature ${title.backdrop_url ? "" : "portrait"}`}>
        <div className="feature-art">
          {title.backdrop_url ? (
            <Still src={title.backdrop_url} />
          ) : (
            <Cover title={title.title} src={title.poster_url} />
          )}
        </div>
        <div className="feature-copy">
          <h1 className="display">{title.title}</h1>
          <p className="feature-meta num">
            {[
              title.year,
              tv ? "Series" : "Film",
              tv && seasonCount
                ? `${seasonCount} season${seasonCount === 1 ? "" : "s"}`
                : "",
              title.runtime ? duration(title.runtime * 60) : "",
            ]
              .filter(Boolean)
              .join(" · ")}
          </p>
          {title.overview && <p className="feature-blurb">{title.overview}</p>}
          <div className="actions">
            {next && (
              <Link
                className="btn primary big"
                data-nav
                to={`/watch/${next.id}`}
              >
                <Play size={18} fill="currentColor" strokeWidth={0} />
                {inProgress(next) ? "Resume" : "Play"}
                {next.episode ? ` ${episodeCode(next)}` : ""}
              </Link>
            )}
            {user.role !== "viewer" && title.tmdb_id && (
              <button
                className={`btn ${next ? "" : "primary"} big`}
                data-nav
                onClick={() => setRequestOpen(true)}
              >
                {tv ? "Request episodes" : "Request"}
              </button>
            )}
          </div>
          {next && (
            <button
              className="btn quiet"
              onClick={() => setSubtitleAsset(next)}
            >
              {next.facts.subtitle_tracks.length
                ? "Check / fix subtitles"
                : "Get subtitles"}
              {next.episode ? ` · ${episodeCode(next)}` : ""}
            </button>
          )}
          {active && (
            <p className="feature-coming">
              <strong>
                {active.status === "paused" ? "Paused" : "Coming up"}
              </strong>
              <span>{scope(active)}</span>
              <Link className="link" to={`/activity?request=${active.id}`}>
                See request
              </Link>
            </p>
          )}
          {active?.placement && places(destinations.data).count > 1 && (
            <p className="meta">{active.placement}</p>
          )}
          {places(destinations.data).count > 1 &&
            title.items.map((item) => (
              <p className="meta stored-on" key={item.id}>
                {movingNow?.item_id === item.id
                  ? `Moving to ${movingNow.to} · ${movingNow.copied} of ${movingNow.files} files`
                  : `On ${storedOn(destinations.data, item)}`}
                {user.role === "admin" && movingNow?.item_id !== item.id && (
                  <button className="link" onClick={() => setMoving(item)}>
                    Move
                  </button>
                )}
              </p>
            ))}
          {assets.some((a) => a.state === "unavailable") && (
            <p className="muted">
              {tv
                ? "Some episodes are on storage that’s offline."
                : "This film is on storage that’s offline."}
            </p>
          )}
          {user.role !== "viewer" && title.tmdb_id && (
            <CollectionCare
              mediaType={title.media_type}
              tmdbId={title.tmdb_id}
              title={title.title}
            />
          )}
        </div>
      </article>
      {assets.length > 0 ? (
        <section className="episodes" aria-labelledby="episodes">
          <Bar id="episodes" title={tv ? "Episodes" : "Copies"}>
            {tv && seasons.length > 1 && (
              <select
                className="bar-select"
                aria-label="Show episodes from"
                value={visibleSeason}
                onChange={(event) => setVisibleSeason(event.target.value)}
              >
                <option value="all">All seasons</option>
                {seasons.map((season) => (
                  <option key={season} value={season}>
                    Season {season}
                  </option>
                ))}
              </select>
            )}
          </Bar>
          <ol>
            {shown.map((asset, index) => (
              <Episode
                key={asset.id}
                asset={asset}
                onSubtitles={() => setSubtitleAsset(asset)}
                number={index + 1}
                tv={tv}
              />
            ))}
          </ol>
        </section>
      ) : (
        <Empty
          title={active ? "Coming up." : "Not in your collection."}
          action={
            active ? (
              <Link className="btn" to={`/activity?request=${active.id}`}>
                See request
              </Link>
            ) : undefined
          }
        >
          {!active &&
            (user.role === "viewer"
              ? "Ask someone who can request it."
              : user.role === "admin"
                ? "Request it, or import your own copy in Storage."
                : undefined)}
        </Empty>
      )}
      {subtitleAsset && (
        <Dialog
          title={`Subtitles${subtitleAsset.episode ? ` · ${episodeCode(subtitleAsset)}` : ""}`}
          onClose={() => setSubtitleAsset(null)}
        >
          <SubtitleRepair
            key={subtitleAsset.id}
            assetId={subtitleAsset.id}
            audio={null}
            expanded
            hasSubtitles={subtitleAsset.facts.subtitle_tracks.length > 0}
            onReady={() => {}}
            onOffset={() => {}}
          />
        </Dialog>
      )}
      {moving && (
        <MoveDialog
          item={moving}
          destinations={destinations.data}
          onClose={() => setMoving(null)}
          onMoved={() => {
            setMoving(null);
            void moves.refresh();
          }}
        />
      )}
      {requestOpen && (
        <RequestSheet
          title={title}
          onClose={() => setRequestOpen(false)}
          onRequested={() => {
            setRequestOpen(false);
            void resource.refresh();
          }}
        />
      )}
    </Page>
  );
}
type Move = {
  id: string;
  item_id: string;
  state: string;
  to: string;
  message: string;
  files: number;
  copied: number;
};

/** Where a title's files are, by name: "antivac" or "antivac, medivac". */
function storedOn(d: Destinations | null, item: Item) {
  const keys = [...new Set(item.assets.map((a) => `${a.node_id}/${a.root_id}`))];
  const disks = d?.computers.flatMap((c) => c.disks) || [];
  return keys
    .map((key) => disks.find((x) => x.value === key)?.name || "storage that’s gone")
    .join(", ");
}

function MoveDialog({
  item,
  destinations,
  onClose,
  onMoved,
}: {
  item: Item;
  destinations: Destinations | null;
  onClose: () => void;
  onMoved: () => void;
}) {
  const [storage, setStorage] = useState("auto");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function move() {
    setBusy(true);
    setError("");
    try {
      await post("/admin/moves", { item_id: item.id, storage });
      onMoved();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Dialog
      title={`Move ${item.title}`}
      onClose={onClose}
      footer={
        <button className="btn primary" disabled={busy} onClick={move}>
          {busy ? "Starting…" : "Move"}
        </button>
      }
    >
      <div className="form">
        <ErrorNote error={error} />
        <Field
          label="To"
          hint="Copied and checked first. The old copy goes after."
        >
          <select value={storage} onChange={(e) => setStorage(e.target.value)}>
            <StorageOptions destinations={destinations} />
          </select>
        </Field>
        <p className="meta">
          Now on {storedOn(destinations, item)} ·{" "}
          {bytes(item.assets.reduce((sum, a) => sum + a.facts.size_bytes, 0))}
        </p>
      </div>
    </Dialog>
  );
}

function Episode({
  asset,
  number,
  tv,
  onSubtitles,
}: {
  asset: Asset;
  number: number;
  tv: boolean;
  onSubtitles: () => void;
}) {
  const watching = inProgress(asset);
  return (
    <li className={`episode ${asset.watch?.watched ? "watched" : ""}`}>
      <span className="episode-no num" aria-hidden="true">
        {tv ? asset.episode || "–" : number}
      </span>
      <span className="episode-body">
        <span className="entry-line">
          <span className="episode-title">
            {tv
              ? asset.episode
                ? `Season ${asset.season ?? 1}, episode ${asset.episode}`
                : "Extra"
              : `Copy ${number}`}
          </span>
          <span className="leader" aria-hidden="true" />
          <span className="num entry-meta">
            {[
              tv ? "" : asset.facts.quality,
              duration(asset.facts.duration),
              tv ? "" : bytes(asset.facts.size_bytes),
            ]
              .filter(Boolean)
              .join(" · ")}
          </span>
        </span>
        <span className="entry-notes">
          {!!asset.watch?.watched && (
            <span className="done-note">
              <Tick /> Watched
            </span>
          )}
          {watching && (
            <>
              <Meter value={progress(asset)} label="Watch progress" />
              <span className="meta">{progress(asset)}%</span>
            </>
          )}
          <Flag value={asset.state} />
        </span>
      </span>
      {asset.state === "ready" && (
        <button
          className="btn quiet"
          onClick={onSubtitles}
          aria-label={`Subtitles${asset.episode ? ` for ${episodeCode(asset)}` : ""}`}
        >
          Subtitles
        </button>
      )}
      {asset.state === "ready" && (
        <Link className="btn" data-nav to={`/watch/${asset.id}`}>
          <Play size={15} fill="currentColor" strokeWidth={0} />
          {watching ? "Resume" : "Play"}
          <span className="sr-only">
            {tv && asset.episode ? ` ${episodeCode(asset)}` : ""}
          </span>
        </Link>
      )}
    </li>
  );
}
function RequestSheet({
  title,
  onClose,
  onRequested,
}: {
  title: TitleData;
  onClose: () => void;
  onRequested: () => void;
}) {
  const destinations = useDestinations();
  const usual = usualPlace(destinations.data, title.preferences);
  const [storage, setStorage] = useState("");
  const [placed, setPlaced] = useState<Placed | null>(null);
  const [overrides, setOverrides] = useState<Partial<Preferences>>({});
  const [season, setSeason] = useState(
    title.seasons.find((s) => s.season_number > 0)?.season_number || 1,
  );
  const [episodes, setEpisodes] = useState<
    { episode_number: number; name: string; air_date: string }[]
  >([]);
  const [selected, setSelected] = useState<number[]>([]);
  const [monitoring, setMonitoring] = useState(
    title.preferences?.values.monitoring || "exact",
  );
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const today = new Date().toISOString().slice(0, 10);
  useEffect(() => {
    if (title.media_type !== "tv") return;
    let cancelled = false;
    setLoading(true);
    setSelected([]);
    api<typeof episodes>(`/titles/tv/${title.tmdb_id}/seasons/${season}`)
      .then((value) => {
        if (!cancelled) {
          setEpisodes(value);
          setError("");
        }
      })
      .catch((e) => {
        if (!cancelled) setError(e.message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [season, title.tmdb_id, title.media_type]);
  const chosen = storage || usual.value;
  const count = title.media_type === "tv" ? selected.length || 1 : 1;
  useEffect(() => {
    let cancelled = false;
    if (chosen === "ask") return setPlaced(null);
    post<Placed>("/storage/preview", {
      tmdb_id: title.tmdb_id,
      media_type: title.media_type,
      count,
      storage: storage || undefined,
    })
      .then((value) => !cancelled && setPlaced(value))
      .catch(() => !cancelled && setPlaced(null));
    return () => {
      cancelled = true;
    };
  }, [chosen, count, title.tmdb_id, title.media_type]);
  async function submit() {
    setBusy(true);
    setError("");
    try {
      await post("/jobs", {
        tmdb_id: title.tmdb_id,
        media_type: title.media_type,
        wanted_episodes:
          title.media_type === "tv" ? { [season]: selected } : null,
        storage: storage || undefined,
        monitoring,
        preferences: overrides,
      });
      onRequested();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const values = title.preferences?.values
    ? { ...title.preferences.values, ...overrides }
    : undefined;
  const aired = episodes.filter((e) => e.air_date && e.air_date <= today);
  return (
    <Dialog
      title={`Request ${title.title}`}
      coupon
      onClose={onClose}
      footer={
        <button
          className="btn primary"
          disabled={
            busy ||
            chosen === "ask" ||
            (title.media_type === "tv" && !selected.length)
          }
          onClick={submit}
        >
          {busy
            ? "Requesting…"
            : title.media_type === "movie"
              ? "Request film"
              : selected.length
                ? `Request ${selected.length} episode${selected.length === 1 ? "" : "s"}`
                : "Choose episodes"}
        </button>
      }
    >
      <div className="form">
        <div className="order-head">
          <Cover title={title.title} src={title.poster_url} />
          <div>
            <h3>{title.title}</h3>
            <p className="meta">
              {[title.year, title.media_type === "tv" ? "Series" : "Film"]
                .filter(Boolean)
                .join(" · ")}
            </p>
          </div>
        </div>
        <ErrorNote error={error || destinations.error} />
        {title.jobs.some((j) => ["active", "paused"].includes(j.status)) && (
          <p className="muted">Adds to the open request.</p>
        )}
        {title.media_type === "tv" && (
          <>
            <Field label="Season">
              <select
                value={season}
                onChange={(e) => setSeason(Number(e.target.value))}
              >
                {title.seasons
                  .filter((s) => s.episode_count > 0)
                  .map((s) => (
                    <option value={s.season_number} key={s.season_number}>
                      {s.name || `Season ${s.season_number}`} ·{" "}
                      {s.episode_count} episodes
                    </option>
                  ))}
              </select>
            </Field>
            {loading ? (
              <Loading label="Loading episodes" />
            ) : (
              <fieldset className="checklist">
                <legend className="checklist-head">
                  <span>Episodes</span>
                  <span className="meta num">{selected.length} chosen</span>
                </legend>
                <button
                  type="button"
                  className="btn quiet checklist-all"
                  disabled={!aired.length}
                  onClick={() =>
                    setSelected(aired.map((e) => e.episode_number))
                  }
                >
                  All aired
                </button>
                <div className="checklist-rows">
                  {episodes.map((ep) => (
                    <label
                      className="check checklist-row"
                      key={ep.episode_number}
                    >
                      <input
                        type="checkbox"
                        checked={selected.includes(ep.episode_number)}
                        onChange={(e) =>
                          setSelected(
                            e.target.checked
                              ? [...selected, ep.episode_number]
                              : selected.filter((n) => n !== ep.episode_number),
                          )
                        }
                      />
                      <span className="num checklist-no">
                        {ep.episode_number}
                      </span>
                      <span>{ep.name}</span>
                      {ep.air_date > today && (
                        <span className="meta num">
                          Airs {airs(ep.air_date)}
                        </span>
                      )}
                    </label>
                  ))}
                </div>
              </fieldset>
            )}
            <Field label="New episodes">
              <select
                value={monitoring}
                onChange={(e) => setMonitoring(e.target.value)}
              >
                <option value="exact">Just the ones I chose</option>
                <option value="keep_current">Add them as they air</option>
              </select>
            </Field>
          </>
        )}
        {(places(destinations.data).count > 1 || chosen === "ask") && (
          <Field label="Store on">
            <select
              value={chosen}
              onChange={(e) =>
                setStorage(e.target.value === usual.value ? "" : e.target.value)
              }
            >
              {chosen === "ask" && (
                <option value="ask" disabled>
                  Choose…
                </option>
              )}
              <StorageOptions destinations={destinations.data} />
            </select>
          </Field>
        )}
        {placed && places(destinations.data).count > 1 && (
          <p className={`meta ${placed.error ? "problem-text" : ""}`}>
            {placed.error || placed.reason}
          </p>
        )}
        {values && (
          <details className="disclosure order-prefs">
            <summary>
              For this request: {values.preferred_quality} ·{" "}
              {values.audio_pref === "original"
                ? "original audio"
                : values.audio_pref === "any"
                  ? "any audio"
                  : `${values.audio_pref} audio`}{" "}
              ·{" "}
              {values.subtitle_languages.length
                ? `${values.subtitle_languages.join(", ")} subtitles`
                : "no subtitles"}
            </summary>
            <PreferenceFields
              values={values}
              onChange={(key, value) =>
                setOverrides({ ...overrides, [key]: value })
              }
            />
          </details>
        )}
      </div>
    </Dialog>
  );
}
