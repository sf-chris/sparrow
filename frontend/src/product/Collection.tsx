import { useWebSocket } from "../hooks/useWebSocket";
import { useMemo, useEffect } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Play, ArrowRight, Search } from "lucide-react";
import { Backdrop } from "./Brand";
import { api, type Item, type User } from "./api";
import {
  ActionLink,
  Empty,
  ErrorNote,
  Loading,
  Page,
  PrintCard,
  Progress,
  Section,
  duration,
  itemLink,
  percent,
  useResource,
} from "./ui";

function greeting(name: string) {
  const hour = new Date().getHours();
  if (hour >= 5 && hour < 12) return `Good morning, ${name}.`;
  if (hour >= 12 && hour < 17) return `Good afternoon, ${name}.`;
  return `Good evening, ${name}.`;
}

export default function Collection({ home = false, user }: { home?: boolean; user: User }) {
  const resource = useResource(() => api<Item[]>("/catalogue"));
  useWebSocket(() => void resource.refresh());
  useEffect(() => {
    const timer = window.setInterval(resource.refresh, 15000);
    return () => clearInterval(timer);
  }, [resource.refresh]);
  const [params, setParams] = useSearchParams();
  const query = home ? "" : params.get("q") || "";
  const type = home ? "" : params.get("type") || "";
  const state = home ? "" : params.get("state") || "";
  const sort = home ? "recent" : params.get("sort") || "recent";
  function filter(key: string, value: string) {
    setParams(
      (previous) => {
        const next = new URLSearchParams(previous);
        if (value) next.set(key, value);
        else next.delete(key);
        return next;
      },
      { replace: true },
    );
  }
  const all = resource.data || [];
  const items = useMemo(() => {
    const result = all.filter(
      (item) =>
        (!query || item.title.toLowerCase().includes(query.toLowerCase())) &&
        (!type || item.media_type === type) &&
        (!state || item.state === state),
    );
    if (sort === "title") result.sort((a, b) => a.title.localeCompare(b.title));
    if (sort === "year") result.sort((a, b) => (b.year || 0) - (a.year || 0));
    return result;
  }, [resource.data, query, type, state, sort]);
  const continuing = all
    .flatMap((item) =>
      item.assets
        .filter((asset) => asset.watch && !asset.watch.watched && asset.watch.position > 5)
        .map((asset) => ({ item, asset })),
    )
    .sort((a, b) => b.asset.watch!.updated - a.asset.watch!.updated)
    .slice(0, 12);
  const empty = resource.data?.length === 0;
  const first = user.name.split(" ")[0];

  return (
    <Page
      className={home ? "sp-tonight" : "sp-library"}
      title={home ? greeting(first) : "Library"}
      action={
        !home && resource.data && !empty ? (
          <p className="sp-tally" aria-live="polite">
            <strong>{String(items.length).padStart(2, "0")}</strong>
            <span className="sp-label">
              {items.length === all.length
                ? items.length === 1
                  ? "title"
                  : "titles"
                : `of ${all.length}`}
            </span>
          </p>
        ) : undefined
      }
    >
      <ErrorNote error={resource.error} retry={resource.refresh} />
      {!resource.data ? (
        resource.loading && <Loading />
      ) : (
        <>
          {empty && (
            <Empty
              title="Nothing to watch yet."
              action={
                <>
                  {user.role === "admin" && (
                    <ActionLink solid to="/settings/storage">
                      Import media
                    </ActionLink>
                  )}
                  {user.role !== "viewer" && (
                    <ActionLink solid={user.role !== "admin"} to="/discover">
                      Find a title
                    </ActionLink>
                  )}
                </>
              }
            >
              {user.role === "viewer"
                ? "Ask whoever runs this server to add something."
                : user.role === "admin"
                  ? "Import what you already have, or find something new."
                  : "Find a film or series and it will appear here when it’s ready."}
            </Empty>
          )}
          {home && continuing.length > 0 && (
            <Section title="Continue watching">
              <div className="sp-tickets" role="region" aria-label="Continue watching titles" tabIndex={0}>
                {continuing.map(({ item, asset }) => {
                  const value = percent(asset.watch!.position, asset.watch!.duration);
                  return (
                    <article className="sp-ticket" key={asset.id}>
                      <div className="sp-ticket-art">
                        <Backdrop src={item.backdrop_url || item.poster_url} lazy />
                        {asset.state === "ready" && (
                          <Link to={`/watch/${asset.id}`} aria-label={`Resume ${item.title}`}>
                            <span className="sp-play-disc">
                              <Play size={22} fill="currentColor" aria-hidden="true" />
                            </span>
                          </Link>
                        )}
                        <Progress
                          className="sp-ticket-progress"
                          label={`Watch progress for ${item.title}`}
                          value={value}
                        />
                      </div>
                      <div className="sp-ticket-stub">
                        <span className="sp-label">
                          {asset.episode ? `Season ${asset.season} · Episode ${asset.episode}` : "Film"}
                        </span>
                        <h3>
                          <Link to={itemLink(item)}>{item.title}</Link>
                        </h3>
                        <p className="sp-ticket-meta">
                          <strong>{value}%</strong> watched ·{" "}
                          {duration(Math.max(0, asset.facts.duration - asset.watch!.position))} left
                          {asset.state !== "ready" && (
                            <>
                              <br />
                              Storage offline
                            </>
                          )}
                        </p>
                      </div>
                    </article>
                  );
                })}
              </div>
            </Section>
          )}
          {empty ? null : home ? (
            <Section
              title="Your library"
              action={
                <Link className="sp-btn sp-btn-ghost" to="/library">
                  See all <ArrowRight size={16} aria-hidden="true" />
                </Link>
              }
            >
              <div className="sp-prints">
                {all.slice(0, 12).map((item) => (
                  <PrintCard key={item.id} item={item} />
                ))}
              </div>
            </Section>
          ) : (
            <>
              <div className="sp-filters" role="search">
                <label className="sp-search">
                  <Search size={18} aria-hidden="true" />
                  <input
                    type="search"
                    aria-label="Search your library"
                    placeholder="Search your library"
                    value={query}
                    onChange={(event) => filter("q", event.target.value)}
                  />
                </label>
                <div className="sp-segments" role="radiogroup" aria-label="Media type">
                  {[
                    ["", "All"],
                    ["movie", "Films"],
                    ["tv", "Series"],
                  ].map(([value, label]) => (
                    <label key={value}>
                      <input
                        type="radio"
                        name="media-type"
                        value={value}
                        checked={type === value}
                        onChange={() => filter("type", value)}
                      />
                      {label}
                    </label>
                  ))}
                </div>
                <select
                  className="sp-select"
                  aria-label="Availability"
                  value={state}
                  onChange={(event) => filter("state", event.target.value)}
                >
                  <option value="">Any availability</option>
                  <option value="ready">Ready to watch</option>
                  <option value="unavailable">Storage offline</option>
                  <option value="subtitles_pending">No subtitles yet</option>
                  <option value="verifying">Checking</option>
                </select>
                <select
                  className="sp-select"
                  aria-label="Sort titles"
                  value={sort}
                  onChange={(event) => filter("sort", event.target.value)}
                >
                  <option value="recent">Library order</option>
                  <option value="title">A–Z</option>
                  <option value="year">Newest</option>
                </select>
              </div>
              {items.length ? (
                <div className="sp-prints">
                  {items.map((item) => (
                    <PrintCard key={item.id} item={item} />
                  ))}
                </div>
              ) : (
                <Empty
                  title="No titles match those filters."
                  action={
                    <button className="sp-btn sp-btn-line" onClick={() => setParams({})}>
                      Clear filters
                    </button>
                  }
                >
                </Empty>
              )}
            </>
          )}
        </>
      )}
    </Page>
  );
}
