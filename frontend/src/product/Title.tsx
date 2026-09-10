import { PreferenceFields } from "./Preferences";
import CollectionCare from "./CollectionCare";
import { Backdrop } from "./Brand";
import { useWebSocket } from "../hooks/useWebSocket";
import { useEffect, useState } from "react";
import { Link, useParams, useLocation } from "react-router-dom";
import { ArrowLeft, Play, Plus } from "lucide-react";
import {
  api,
  post,
  type Title as TitleData,
  type Item,
  type User,
  type NodeInfo,
  type Asset,
  type Preferences,
} from "./api";
import {
  Dialog,
  Empty,
  ErrorNote,
  Field,
  Loading,
  Page,
  Poster,
  Section,
  Status,
  duration,
  useResource,
} from "./ui";

export default function Title({ user }: { user: User }) {
  const { mediaType, tmdbId, itemId } = useParams();
  const location = useLocation();
  const previous = location.state?.from;
  const returnTo =
    typeof previous === "string" && /^\/(library|discover)(\?|$)/.test(previous)
      ? previous
      : "/library";
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
  const [visibleSeason, setVisibleSeason] = useState("all");
  useEffect(() => setVisibleSeason("all"), [mediaType, tmdbId, itemId]);
  const title = resource.data;
  useEffect(() => {
    if (title) document.title = `${title.title} · Sparrow`;
  }, [title?.title]);
  const assets = title?.items.flatMap((i) => i.assets) || [];
  const next =
    assets.find(
      (a) =>
        a.state === "ready" &&
        a.watch &&
        !a.watch.watched &&
        a.watch.position > 5,
    ) ||
    assets.find((a) => a.state === "ready" && !a.watch?.watched) ||
    assets.find((a) => a.state === "ready");
  const active = title?.jobs.find((j) =>
    ["active", "paused"].includes(j.status),
  );
  if (!title)
    return (
      <Page title="Your next watch">
        <ErrorNote error={resource.error} retry={resource.refresh} />
        {resource.loading && <Loading label="Loading this title…" />}
      </Page>
    );
  return (
    <Page title="" className="sp-title-page">
      <Link className="sp-back" to={returnTo}>
        <ArrowLeft size={15} />
        {returnTo.startsWith("/discover")
          ? "Back to discovery"
          : "Your collection"}
      </Link>
      <ErrorNote error={resource.error} retry={resource.refresh} />
      <div className="sp-title-hero">
        <Backdrop className="sp-title-backdrop" src={title.backdrop_url} />
        <Poster title={title.title} src={title.poster_url} />
        <div className="sp-title-copy">
          <p className="sp-eyebrow">
            {title.media_type === "tv" ? "TV show" : "Movie"}
            {title.year && ` · ${title.year}`}
          </p>
          <h1>{title.title}</h1>
          <div className="sp-title-meta">
            {title.runtime ? <span>{duration(title.runtime * 60)}</span> : null}
            {title.media_type === "tv" && title.seasons.length > 0 && (
              <span>
                {
                  title.seasons.filter((season) => season.season_number > 0)
                    .length
                }{" "}
                seasons
              </span>
            )}
            {next && <Status value="ready" />}
          </div>
          <p className="sp-description">
            {title.overview || "A place for this title in your collection."}
          </p>
          <div className="sp-actions">
            {next && (
              <Link className="sp-button primary" to={`/watch/${next.id}`}>
                <Play size={17} fill="currentColor" />
                {next.watch?.position && !next.watch.watched
                  ? "Resume"
                  : "Play"}
                {next.episode ? ` episode ${next.episode}` : ""}
              </Link>
            )}
            {user.role !== "viewer" && title.tmdb_id && (
              <button
                className={`sp-button ${next ? "secondary" : "primary"}`}
                onClick={() => setRequestOpen(true)}
              >
                <Plus size={17} />
                {title.media_type === "movie"
                  ? "Request movie"
                  : "Choose episodes"}
              </button>
            )}
            {active && (
              <Link className="sp-button secondary" to="/activity">
                View request
              </Link>
            )}
          </div>
          {assets.some((a) => a.state === "unavailable") && (
            <p className="sp-muted mt-4">
              Some files are on storage that’s currently unavailable. Your
              collection and progress are saved.
            </p>
          )}
        </div>
      </div>
      {assets.length > 0 ? (
        <Section
          action={
            title.media_type === "tv" ? (
              <select
                className="sp-season-select"
                aria-label="Show episodes from"
                value={visibleSeason}
                onChange={(event) => setVisibleSeason(event.target.value)}
              >
                <option value="all">All seasons</option>
                {[...new Set(assets.map((asset) => asset.season || 1))]
                  .sort((a, b) => a - b)
                  .map((season) => (
                    <option key={season} value={season}>
                      Season {season}
                    </option>
                  ))}
              </select>
            ) : undefined
          }
          title={
            title.media_type === "tv"
              ? "In your collection"
              : "Available copies"
          }
        >
          <div className="sp-panel">
            {[...assets]
              .filter(
                (asset) =>
                  visibleSeason === "all" ||
                  String(asset.season || 1) === visibleSeason,
              )
              .sort(
                (a, b) =>
                  (a.season || 0) - (b.season || 0) ||
                  (a.episode || 0) - (b.episode || 0),
              )
              .map((asset) => (
                <Episode key={asset.id} asset={asset} />
              ))}
          </div>
        </Section>
      ) : (
        <Empty
          title={
            active
              ? "Sparrow has your request."
              : "This title is waiting for a place in your collection."
          }
          action={
            active ? (
              <Link className="sp-button secondary" to="/activity">
                Follow its progress
              </Link>
            ) : undefined
          }
        >
          {active
            ? "You’ll find updates and any useful next steps in Activity."
            : "Choose exactly what you want, or import an existing copy from Storage settings."}
        </Empty>
      )}
      {user.role !== "viewer" && title.tmdb_id && (
        <CollectionCare
          mediaType={title.media_type}
          tmdbId={title.tmdb_id}
          title={title.title}
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
function Episode({ asset }: { asset: Asset }) {
  return (
    <div className="sp-episode">
      <span className="sp-episode-number">
        {asset.episode ? String(asset.episode).padStart(2, "0") : "↗"}
      </span>
      <div>
        <h3>
          {asset.episode
            ? `Season ${asset.season} · Episode ${asset.episode}`
            : "Movie"}
          {asset.watch?.watched ? " · Watched" : ""}
        </h3>
        <p>
          {duration(asset.facts.duration)} · <Status value={asset.state} />
        </p>
      </div>
      {asset.state === "ready" && (
        <Link className="sp-button secondary" to={`/watch/${asset.id}`}>
          <Play size={14} />
          {asset.watch?.position && !asset.watch.watched ? "Resume" : "Play"}
        </Link>
      )}
    </div>
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
  const nodes = useResource(() => api<NodeInfo[]>("/nodes"));
  const [node, setNode] = useState(
    title.jobs.find((j) => ["active", "paused"].includes(j.status))?.node_id ||
      "local",
  );
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
  async function submit() {
    setBusy(true);
    setError("");
    try {
      await post("/jobs", {
        tmdb_id: title.tmdb_id,
        media_type: title.media_type,
        wanted_episodes:
          title.media_type === "tv" ? { [season]: selected } : null,
        node_id: node,
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
  return (
    <Dialog
      title={`Request ${title.title}`}
      onClose={onClose}
      footer={
        <button
          className="sp-button primary"
          disabled={busy || (title.media_type === "tv" && !selected.length)}
          onClick={submit}
        >
          {busy
            ? "Starting request…"
            : title.media_type === "movie"
              ? "Request movie"
              : `Request ${selected.length} episode${selected.length === 1 ? "" : "s"}`}
        </button>
      }
    >
      <div className="sp-form">
        <div className="sp-request-summary">
          <Poster title={title.title} src={title.poster_url} />
          <div>
            <h3>{title.title}</h3>
            <p>
              {title.year} ·{" "}
              {title.media_type === "tv"
                ? "Choose exactly the episodes you want."
                : "One good film, coming to your collection."}
            </p>
          </div>
        </div>
        <ErrorNote error={error || nodes.error} />
        {title.jobs.some((j) => ["active", "paused"].includes(j.status)) && (
          <p className="sp-muted">
            This adds your selected episodes to the existing request and applies
            the preferences shown below. A paused request stays paused.
          </p>
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
              <Loading label="Checking episode information…" />
            ) : (
              <div>
                <div className="sp-savebar" style={{ marginTop: 0 }}>
                  <span className="sp-muted">{selected.length} selected</span>
                  <button
                    className="sp-button quiet"
                    onClick={() =>
                      setSelected(
                        episodes
                          .filter(
                            (e) =>
                              e.air_date &&
                              e.air_date <=
                                new Date().toISOString().slice(0, 10),
                          )
                          .map((e) => e.episode_number),
                      )
                    }
                  >
                    Select aired episodes
                  </button>
                </div>
                <div style={{ maxHeight: 240, overflowY: "auto" }}>
                  {episodes.map((ep) => (
                    <label className="sp-checkbox" key={ep.episode_number}>
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
                      <span>
                        {ep.episode_number}. {ep.name}
                        {ep.air_date > new Date().toISOString().slice(0, 10)
                          ? ` · ${ep.air_date}`
                          : ""}
                      </span>
                    </label>
                  ))}
                </div>
              </div>
            )}
            <Field label="Future monitoring">
              <select
                value={monitoring}
                onChange={(e) => setMonitoring(e.target.value)}
              >
                <option value="exact">Off — only these episodes</option>
                <option value="keep_current">
                  Get new episodes as they air
                </option>
              </select>
            </Field>
          </>
        )}
        <Field label="Store on">
          <select value={node} onChange={(e) => setNode(e.target.value)}>
            {nodes.data
              ?.filter((n) => !n.disabled)
              .map((n) => (
                <option value={n.id} key={n.id}>
                  {n.name}
                  {n.online ? "" : " · offline"}
                </option>
              ))}
          </select>
        </Field>
        {values && (
          <div className="sp-panel">
            <p className="sp-muted">Your preferences</p>
            <p>
              {values.preferred_quality} preferred ·{" "}
              {values.audio_pref === "original"
                ? "Original audio"
                : values.audio_pref}{" "}
              · {values.subtitle_languages.join(", ")} subtitles
            </p>
            <details>
              <summary className="sp-button quiet">
                Change preferences for this request
              </summary>
              <PreferenceFields
                values={values}
                onChange={(key, value) =>
                  setOverrides({ ...overrides, [key]: value })
                }
              />
            </details>
          </div>
        )}
      </div>
    </Dialog>
  );
}
