import { PreferenceFields } from "./Preferences";
import CollectionCare from "./CollectionCare";
import { Backdrop } from "./Brand";
import { useWebSocket } from "../hooks/useWebSocket";
import { useEffect, useState } from "react";
import { Link, useParams, useLocation } from "react-router-dom";
import { ArrowLeft, Check, Play, Plus } from "lucide-react";
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
  Progress,
  Section,
  Status,
  duration,
  kind,
  percent,
  useResource,
} from "./ui";

const audioNames: Record<string, string> = { original: "Original-language audio", any: "Any audio" };

export default function Title({ user }: { user: User }) {
  const { mediaType, tmdbId, itemId } = useParams();
  const location = useLocation();
  const previous = location.state?.from;
  const returnTo =
    typeof previous === "string" && /^\/(library|discover)(\?|$)/.test(previous) ? previous : "/library";
  const resource = useResource(async () => {
    if (itemId) {
      const item = await api<Item>(`/items/${itemId}`);
      if (item.tmdb_id) return api<TitleData>(`/titles/${item.media_type}/${item.tmdb_id}`);
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
    assets.find((a) => a.state === "ready" && a.watch && !a.watch.watched && a.watch.position > 5) ||
    assets.find((a) => a.state === "ready" && !a.watch?.watched) ||
    assets.find((a) => a.state === "ready");
  const active = title?.jobs.find((j) => ["active", "paused"].includes(j.status));
  const back = (
    <Link className="sp-back" to={returnTo}>
      <ArrowLeft size={15} aria-hidden="true" />
      {returnTo.startsWith("/discover") ? "Back to results" : "Back to library"}
    </Link>
  );
  if (!title)
    return (
      <Page title="" className="sp-title">
        {back}
        <ErrorNote error={resource.error} retry={resource.refresh} />
        {resource.loading && <Loading label="Loading this title…" />}
      </Page>
    );
  const seasons = title.seasons.filter((season) => season.season_number > 0).length;
  const resuming = !!(next?.watch?.position && !next.watch.watched);
  return (
    <Page title="" className="sp-title">
      {back}
      <ErrorNote error={resource.error} retry={resource.refresh} />
      <div className="sp-feature">
        <div className="sp-feature-still">
          <Backdrop src={title.backdrop_url || title.poster_url} />
        </div>
        <div className="sp-feature-poster">
          <Poster title={title.title} src={title.poster_url} />
        </div>
        <div className="sp-feature-copy">
          <p className="sp-label">
            {kind(title.media_type)}
            {title.year && ` · ${title.year}`}
            {title.runtime ? ` · ${duration(title.runtime * 60)}` : ""}
            {title.media_type === "tv" && seasons > 0 && ` · ${seasons} ${seasons === 1 ? "season" : "seasons"}`}
          </p>
          <h1>{title.title}</h1>
          {title.overview && <p className="sp-feature-overview">{title.overview}</p>}
          <div className="sp-actions sp-feature-actions">
            {next && (
              <Link className="sp-btn sp-btn-play" to={`/watch/${next.id}`}>
                <Play size={18} fill="currentColor" aria-hidden="true" />
                {resuming ? "Resume" : "Play"}
                {next.episode ? ` episode ${next.episode}` : ""}
              </Link>
            )}
            {user.role !== "viewer" && title.tmdb_id && (
              <button
                className={`sp-btn ${next ? "sp-btn-line" : "sp-btn-solid"}`}
                onClick={() => setRequestOpen(true)}
              >
                <Plus size={17} aria-hidden="true" />
                {title.media_type === "movie" ? "Request film" : "Choose episodes"}
              </button>
            )}
            {active && (
              <Link className="sp-btn sp-btn-ghost" to="/activity">
                View request
              </Link>
            )}
          </div>
          {next && resuming && next.watch && (
            <div className="sp-feature-progress">
              <Progress
                className="sp-bar-progress"
                label={`Watch progress for ${title.title}`}
                value={percent(next.watch.position, next.watch.duration)}
              />
              <span className="sp-label">
                {duration(Math.max(0, next.facts.duration - next.watch.position))} left
              </span>
            </div>
          )}
          {assets.some((a) => a.state === "unavailable") && (
            <p className="sp-note">Some copies are on storage that’s offline.</p>
          )}
        </div>
      </div>
      {assets.length > 0 ? (
        <Section
          title={title.media_type === "tv" ? "Episodes" : "Copies"}
          action={
            title.media_type === "tv" ? (
              <select
                className="sp-select sp-season-select"
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
        >
          <ol className="sp-listing">
            {[...assets]
              .filter((asset) => visibleSeason === "all" || String(asset.season || 1) === visibleSeason)
              .sort((a, b) => (a.season || 0) - (b.season || 0) || (a.episode || 0) - (b.episode || 0))
              .map((asset, index) => (
                <Episode key={asset.id} asset={asset} index={index} />
              ))}
          </ol>
        </Section>
      ) : (
        <Empty title={active ? "Requested." : "Not in your library."}>
          {active
            ? "Progress shows under Requests."
            : user.role === "viewer"
              ? "Ask whoever runs this server to add it."
              : undefined}
        </Empty>
      )}
      {user.role !== "viewer" && title.tmdb_id && (
        <CollectionCare mediaType={title.media_type} tmdbId={title.tmdb_id} title={title.title} />
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

function Episode({ asset, index }: { asset: Asset; index: number }) {
  const resume = !!(asset.watch?.position && !asset.watch.watched);
  return (
    <li className="sp-listing-row">
      <span className="sp-listing-number" aria-hidden="true">
        {String(asset.episode ?? index + 1).padStart(2, "0")}
      </span>
      <div className="sp-listing-info">
        <h3>{asset.episode ? `Episode ${asset.episode}` : "Film"}</h3>
        <p className="sp-label">
          {asset.episode ? `Season ${asset.season} · ` : ""}
          {duration(asset.facts.duration)}
        </p>
        {asset.watch?.watched ? (
          <span className="sp-status is-quiet">
            <Check size={12} aria-hidden="true" /> Watched
          </span>
        ) : (
          asset.state !== "ready" && <Status value={asset.state} />
        )}
      </div>
      {asset.state === "ready" && (
        <Link className="sp-btn sp-btn-play sp-btn-small" to={`/watch/${asset.id}`}>
          <Play size={14} fill="currentColor" aria-hidden="true" />
          {resume ? "Resume" : "Play"}
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
  const nodes = useResource(() => api<NodeInfo[]>("/nodes"));
  const [node, setNode] = useState(
    title.jobs.find((j) => ["active", "paused"].includes(j.status))?.node_id || "local",
  );
  const [overrides, setOverrides] = useState<Partial<Preferences>>({});
  const [season, setSeason] = useState(title.seasons.find((s) => s.season_number > 0)?.season_number || 1);
  const [episodes, setEpisodes] = useState<{ episode_number: number; name: string; air_date: string }[]>([]);
  const [selected, setSelected] = useState<number[]>([]);
  const [monitoring, setMonitoring] = useState(title.preferences?.values.monitoring || "exact");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const todayISO = new Date().toISOString().slice(0, 10);
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
        wanted_episodes: title.media_type === "tv" ? { [season]: selected } : null,
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
  const values = title.preferences?.values ? { ...title.preferences.values, ...overrides } : undefined;
  const aired = episodes.filter((e) => e.air_date && e.air_date <= todayISO).map((e) => e.episode_number);
  return (
    <Dialog
      title={`Request ${title.title}`}
      onClose={onClose}
      footer={
        <button
          className="sp-btn sp-btn-solid"
          disabled={busy || (title.media_type === "tv" && !selected.length)}
          onClick={submit}
        >
          {busy
            ? "Requesting…"
            : title.media_type === "movie"
              ? "Request film"
              : `Request ${selected.length} episode${selected.length === 1 ? "" : "s"}`}
        </button>
      }
    >
      <div className="sp-form">
        <div className="sp-request-head">
          <div className="sp-request-poster">
            <Poster title={title.title} src={title.poster_url} />
          </div>
          <p>
            <span className="sp-label">
              {kind(title.media_type)} · {title.year}
            </span>
            {title.media_type === "tv" && (
              <>
                <br />
                Only the episodes you pick are downloaded.
              </>
            )}
          </p>
        </div>
        <ErrorNote error={error || nodes.error} />
        {title.jobs.some((j) => ["active", "paused"].includes(j.status)) && (
          <p className="sp-note">
            These episodes join the existing request. If it’s paused, it stays paused.
          </p>
        )}
        {title.media_type === "tv" && (
          <>
            <Field label="Season">
              <select value={season} onChange={(e) => setSeason(Number(e.target.value))}>
                {title.seasons
                  .filter((s) => s.episode_count > 0)
                  .map((s) => (
                    <option value={s.season_number} key={s.season_number}>
                      {s.name || `Season ${s.season_number}`} · {s.episode_count} episodes
                    </option>
                  ))}
              </select>
            </Field>
            {loading ? (
              <Loading label="Loading episodes…" />
            ) : (
              <div className="sp-picker">
                <div className="sp-picker-head">
                  <span className="sp-label" role="status">
                    {selected.length} of {episodes.length} selected
                  </span>
                  <button
                    className="sp-btn sp-btn-ghost sp-btn-small"
                    onClick={() => setSelected(selected.length === aired.length && aired.length ? [] : aired)}
                  >
                    {selected.length === aired.length && aired.length ? "Clear" : "Select all aired"}
                  </button>
                </div>
                <ul className="sp-picker-list">
                  {episodes.map((ep) => (
                    <li key={ep.episode_number}>
                      <label className="sp-check">
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
                        <span className="sp-picker-number">{String(ep.episode_number).padStart(2, "0")}</span>
                        <span>
                          {ep.name}
                          {ep.air_date > todayISO && <span className="sp-label"> · Airs {ep.air_date}</span>}
                        </span>
                      </label>
                    </li>
                  ))}
                </ul>
              </div>
            )}
            <Field label="Future episodes">
              <select value={monitoring} onChange={(e) => setMonitoring(e.target.value)}>
                <option value="exact">Only these episodes</option>
                <option value="keep_current">Also get new episodes as they air</option>
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
          <div className="sp-request-prefs">
            <p className="sp-label">Preferences</p>
            <p>
              {values.preferred_quality} picture · {audioNames[values.audio_pref] || `${values.audio_pref} audio`} ·{" "}
              {values.subtitle_languages.join(", ") || "no"} subtitles
            </p>
            <details className="sp-disclosure">
              <summary>Change for this request</summary>
              <PreferenceFields
                values={values}
                onChange={(key, value) => setOverrides({ ...overrides, [key]: value })}
              />
            </details>
          </div>
        )}
      </div>
    </Dialog>
  );
}
