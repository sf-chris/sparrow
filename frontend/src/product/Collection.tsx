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
  if (hour >= 17 && hour < 23) return `Good evening, ${name}.`;
  return `Still up, ${name}?`;
}
const today = () =>
  new Date().toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" });

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
  const numbers = new Map(all.map((item, index) => [item.id, index + 1]));
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
  const count = (n: number) => `${n} ${n === 1 ? "title" : "titles"}`;

  return (
    <Page
      className={home ? "sp-tonight" : "sp-library"}
      kicker={
        home
          ? `${today()}${resource.data ? ` · ${count(all.length)} in the house` : ""}`
          : "The collection"
      }
      title={home ? greeting(first) : "Library"}
      action={
        !home && resource.data && !empty ? (
          <p className="sp-tally" aria-live="polite">
            <strong>{String(items.length).padStart(2, "0")}</strong>
            <span className="sp-label">
              {items.length === all.length ? count(all.length) : `of ${count(all.length)}`}
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
          {home && empty && (
            <section className="sp-opening" aria-label="Welcome to your collection">
              <p className="sp-label">Opening night</p>
              <h2>The house is empty. For now.</h2>
              <p>
                {user.role === "viewer"
                  ? "Titles shared with you will appear here. Ask whoever runs your server to add a collection."
                  : user.role === "admin"
                    ? "Bring in the films and series you already own, or ask Sparrow to find your first one."
                    : "Ask Sparrow to find a film or series and it will appear here when it’s ready."}
              </p>
              <div className="sp-actions">
                {user.role === "admin" && (
                  <Link className="sp-btn sp-btn-solid" to="/settings/storage">
                    Bring in your collection
                  </Link>
                )}
                {user.role !== "viewer" && (
                  <Link
                    className={`sp-btn ${user.role === "admin" ? "sp-btn-line" : "sp-btn-solid"}`}
                    to="/discover"
                  >
                    Find your first title <ArrowRight size={16} aria-hidden="true" />
                  </Link>
                )}
              </div>
            </section>
          )}
          {home && continuing.length > 0 && (
            <Section kicker="Your tickets" title="Pick up where you left off">
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
                          {asset.episode ? `Season ${asset.season} · Episode ${asset.episode}` : "Feature"}
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
                              Storage offline · progress saved
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
          {empty ? (
            !home && (
              <Empty
                title="Your collection starts here."
                action={
                  <>
                    {user.role === "admin" && (
                      <ActionLink solid to="/settings/storage">
                        Connect storage & import
                      </ActionLink>
                    )}
                    {user.role !== "viewer" && (
                      <ActionLink to="/discover">Find a film or series</ActionLink>
                    )}
                  </>
                }
              >
                {user.role === "viewer"
                  ? "Titles shared with you will appear here. Ask whoever runs your server to add a collection."
                  : "Bring in what you already own, then let Sparrow help with the rest."}
              </Empty>
            )
          ) : home ? (
            <Section
              kicker="In the house"
              title="Now showing"
              action={
                <Link className="sp-btn sp-btn-ghost" to="/library">
                  The whole library <ArrowRight size={16} aria-hidden="true" />
                </Link>
              }
            >
              <div className="sp-prints">
                {all.slice(0, 12).map((item) => (
                  <PrintCard key={item.id} item={item} number={numbers.get(item.id)} />
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
                  <option value="subtitles_pending">Subtitles need attention</option>
                  <option value="verifying">Being checked</option>
                </select>
                <select
                  className="sp-select"
                  aria-label="Sort titles"
                  value={sort}
                  onChange={(event) => filter("sort", event.target.value)}
                >
                  <option value="recent">Library order</option>
                  <option value="title">Title, A–Z</option>
                  <option value="year">Newest release</option>
                </select>
              </div>
              {items.length ? (
                <div className="sp-prints">
                  {items.map((item) => (
                    <PrintCard key={item.id} item={item} number={numbers.get(item.id)} />
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
                  Try another title, or show everything in your collection.
                </Empty>
              )}
            </>
          )}
          {home && user.role !== "viewer" && (
            <section className="sp-callout">
              <div>
                <p className="sp-label">Box office</p>
                <h2>Something in mind? Or just a mood?</h2>
              </div>
              <Link className="sp-btn sp-btn-solid" to="/discover">
                Find something to watch <ArrowRight size={17} aria-hidden="true" />
              </Link>
            </section>
          )}
        </>
      )}
    </Page>
  );
}
