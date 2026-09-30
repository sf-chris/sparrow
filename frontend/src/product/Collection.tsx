import { useWebSocket } from "../hooks/useWebSocket";
import { useMemo, useEffect, useState, type ReactNode } from "react";
import {
  Link,
  useLocation,
  useOutletContext,
  useSearchParams,
} from "react-router-dom";
import { Play, Search, List, LayoutGrid } from "lucide-react";
import { Circled, Still } from "./Brand";
import { api, type Item, type Job, type User } from "./api";
import {
  Bar,
  Cover,
  Empty,
  ErrorNote,
  Flag,
  Loading,
  Meter,
  duration,
  episodeCode,
  inProgress,
  itemLink,
  progress,
  useResource,
} from "./ui";

const sortKey = (title: string) => title.replace(/^(the|a|an)\s+/i, "");
const kind = (item: { media_type: string }) =>
  item.media_type === "tv" ? "Series" : "Film";

/** How long since a request last changed: honest age, not a broadcast time. */
export const ago = (seconds: number) => {
  const age = Math.max(0, Date.now() / 1000 - seconds);
  return age < 60
    ? "now"
    : age < 3600
      ? `${Math.floor(age / 60)}m`
      : age < 86400
        ? `${Math.floor(age / 3600)}h`
        : `${Math.floor(age / 86400)}d`;
};
export function Age({ seconds }: { seconds: number }) {
  const value = ago(seconds);
  return (
    <time
      className="slot-time"
      dateTime={new Date(seconds * 1000).toISOString()}
      title={`Updated ${new Date(seconds * 1000).toLocaleString()}`}
    >
      <span aria-hidden="true">{value}</span>
      <span className="sr-only">
        Updated {value === "now" ? "just now" : `${value} ago`}
      </span>
    </time>
  );
}

export const clock = (seconds: number) => {
  const date = new Date(seconds * 1000);
  return new Date().toDateString() === date.toDateString()
    ? date.toLocaleTimeString([], {
        hour: "2-digit",
        minute: "2-digit",
        hourCycle: "h23",
      })
    : date.toLocaleDateString([], { day: "numeric", month: "short" });
};

export const scope = (job: Pick<Job, "media_type" | "wanted_episodes">) =>
  job.media_type === "movie"
    ? "Film"
    : Object.entries(job.wanted_episodes)
        .map(
          ([season, episodes]) =>
            `Season ${season} · ${episodes.length} episode${episodes.length === 1 ? "" : "s"}`,
        )
        .join(" / ") || "Series";

function Marked({ text, query }: { text: string; query: string }) {
  const at = query ? text.toLowerCase().indexOf(query.toLowerCase()) : -1;
  if (at < 0) return <>{text}</>;
  return (
    <>
      {text.slice(0, at)}
      <mark>{text.slice(at, at + query.length)}</mark>
      {text.slice(at + query.length)}
    </>
  );
}

/** A control's value, shown at once. The address updates as a low-priority
 *  navigation, so a slow device would otherwise show the old value after a press. */
function useShown(value: string) {
  const [shown, setShown] = useState(value);
  useEffect(() => setShown(value), [value]);
  return [shown, setShown] as const;
}

function Choice({
  name,
  label,
  value,
  options,
  onChange,
  className = "",
}: {
  name: string;
  label: string;
  value: string;
  options: [string, ReactNode, string?][];
  onChange: (value: string) => void;
  className?: string;
}) {
  const [shown, setShown] = useShown(value);
  return (
    <fieldset className={`segmented ${className}`}>
      <legend className="sr-only">{label}</legend>
      {options.map(([option, text, accessible]) => (
        <label key={option} title={accessible}>
          <input
            type="radio"
            name={name}
            value={option}
            checked={shown === option}
            onChange={() => {
              setShown(option);
              onChange(option);
            }}
            aria-label={accessible}
          />
          <span>{text}</span>
        </label>
      ))}
    </fieldset>
  );
}

export default function Collection({ user }: { user: User }) {
  const resource = useResource(() => api<Item[]>("/catalogue"));
  const requests = useResource(() => api<Job[]>("/jobs"));
  useWebSocket(() => {
    void resource.refresh();
    void requests.refresh();
  });
  useEffect(() => {
    const timer = window.setInterval(resource.refresh, 15000);
    return () => clearInterval(timer);
  }, [resource.refresh]);
  const location = useLocation();
  const { notice } = useOutletContext<{ notice: ReactNode }>() || {
    notice: null,
  };
  const [params, setParams] = useSearchParams();
  const [more, setMore] = useState(false);
  const query = params.get("q") || "";
  const type = params.get("type") || "";
  const state = params.get("state") || "";
  const sort = params.get("sort") || "title";
  const view = params.get("view") === "covers" ? "covers" : "list";
  // Typed text stays local: syncing it back from a lagging address drops letters.
  const [search, setSearch] = useState(query);
  const [sortShown, setSortShown] = useShown(sort);
  const [stateShown, setStateShown] = useShown(state);
  function filter(key: string, value: string) {
    // History updates before React finishes the navigation render. Reading it
    // here preserves a preceding filter change when the next input arrives
    // during that render; useSearchParams' callback still sees the old render.
    const next = new URLSearchParams(window.location.search);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: true });
  }
  const all = resource.data || [];
  const items = useMemo(() => {
    const result = all.filter(
      (item) =>
        (!query || item.title.toLowerCase().includes(query.toLowerCase())) &&
        (!type || item.media_type === type) &&
        (!state || item.state === state),
    );
    if (sort === "title")
      result.sort((a, b) => sortKey(a.title).localeCompare(sortKey(b.title)));
    if (sort === "year") result.sort((a, b) => (b.year || 0) - (a.year || 0));
    return result;
  }, [resource.data, query, type, state, sort]);
  const groups = useMemo(() => {
    const keyed = new Map<string, Item[]>();
    for (const item of items) {
      const first = sortKey(item.title)[0] || "";
      const key =
        sort === "title"
          ? /[a-z]/i.test(first)
            ? first.toUpperCase()
            : "#"
          : sort === "year"
            ? String(item.year || "Undated")
            : "";
      keyed.set(key, [...(keyed.get(key) || []), item]);
    }
    return [...keyed.entries()];
  }, [items, sort]);
  const continuing = all
    .flatMap((item) =>
      item.assets.filter(inProgress).map((asset) => ({ item, asset })),
    )
    .sort((a, b) => b.asset.watch!.updated - a.asset.watch!.updated)
    .slice(0, 12);
  const coming = (requests.data || [])
    .filter((job) => job.status !== "complete")
    .sort((a, b) => b.updated_at - a.updated_at)
    .slice(0, 6);
  const today = new Date();
  const from = { from: location.pathname + location.search };
  const filtered = !!(query || type || state);
  return (
    <main id="main-content" className="page guide">
      <header className="dayband">
        <div className="dayband-inner">
          <h1 className="display">
            {today.toLocaleDateString([], { weekday: "long" })}
            <span className="sr-only">
              {" "}
              {today.toLocaleDateString([], { day: "numeric", month: "long" })}
            </span>
          </h1>
          <p aria-hidden="true">
            {today.toLocaleDateString([], { day: "numeric", month: "long" })}
          </p>
        </div>
      </header>
      {notice}
      <ErrorNote error={resource.error} retry={resource.refresh} />
      {!resource.data ? (
        resource.loading && <Loading label="Loading the guide" />
      ) : !all.length ? (
        <Empty
          title="Nothing listed yet."
          action={
            user.role === "admin" ? (
              <>
                <Link className="btn primary" to="/settings/storage">
                  Import your files
                </Link>
                <Link className="btn" to="/discover">
                  Find a title
                </Link>
              </>
            ) : user.role === "requester" ? (
              <Link className="btn primary" to="/discover">
                Find a title
              </Link>
            ) : undefined
          }
        >
          {user.role === "viewer"
            ? "Films and series you can watch will be listed here."
            : user.role === "admin"
              ? "Import films and series you have, or request something new."
              : "Find a film or series and request it."}
        </Empty>
      ) : (
        <div
          className={`guide-columns ${continuing.length || coming.length ? "" : "single"}`}
        >
          {(continuing.length > 0 || coming.length > 0) && (
            <div className="guide-side">
              {continuing.length > 0 && (
                <section aria-labelledby="tonight">
                  <Bar id="tonight" title="Tonight" />
                  <ol className="tonight">
                    {continuing
                      .slice(0, more ? 12 : 4)
                      .map(({ item, asset }) => {
                        const code = episodeCode(asset);
                        const art = (
                          <>
                            <Still
                              src={item.backdrop_url || item.poster_url}
                              lazy
                            />
                            {asset.state === "ready" && (
                              <span className="play-dot" aria-hidden="true">
                                <Play
                                  size={18}
                                  fill="currentColor"
                                  strokeWidth={0}
                                />
                              </span>
                            )}
                          </>
                        );
                        return (
                          <li className="tonight-item" key={asset.id}>
                            {asset.state === "ready" ? (
                              <Link
                                className="tonight-art"
                                data-nav
                                to={`/watch/${asset.id}`}
                                aria-label={`Resume ${item.title}${code ? " " + code : ""}`}
                              >
                                {art}
                              </Link>
                            ) : (
                              <span className="tonight-art">{art}</span>
                            )}
                            <div className="tonight-text">
                              <h3>
                                <Link to={itemLink(item)} state={from}>
                                  {item.title}
                                </Link>
                              </h3>
                              <p className="leader-line">
                                <span>{code || kind(item)}</span>
                                <span className="leader" aria-hidden="true" />
                                <span className="num">
                                  {duration(
                                    Math.max(
                                      0,
                                      asset.facts.duration -
                                        asset.watch!.position,
                                    ),
                                  )}{" "}
                                  left
                                </span>
                              </p>
                              <Meter
                                value={progress(asset)}
                                label={`Watch progress for ${item.title}`}
                              />
                              {asset.state !== "ready" && (
                                <p className="meta">
                                  <Flag value="unavailable" />
                                </p>
                              )}
                            </div>
                          </li>
                        );
                      })}
                  </ol>
                  {continuing.length > 4 && (
                    <button
                      className="btn quiet more"
                      aria-expanded={more}
                      onClick={() => setMore(!more)}
                    >
                      {more ? "Fewer" : `${continuing.length - 4} more`}
                    </button>
                  )}
                </section>
              )}
              {coming.length > 0 && (
                <section aria-labelledby="coming-up">
                  <Bar id="coming-up" title="Coming up">
                    <Link to="/activity">
                      See all<span className="sr-only"> requests</span>
                    </Link>
                  </Bar>
                  <ol className="slots">
                    {coming.map((job) => (
                      <li key={job.id}>
                        <Link
                          className="slot"
                          data-nav
                          to={`/activity?request=${job.id}`}
                        >
                          <Age seconds={job.updated_at} />
                          <span className="slot-body">
                            <Circled>
                              <strong>{job.title}</strong>
                            </Circled>
                            <span className="meta">{scope(job)}</span>
                          </span>
                          <Flag
                            value={
                              job.needs_attention ? "attention" : job.status
                            }
                          />
                        </Link>
                      </li>
                    ))}
                  </ol>
                </section>
              )}
            </div>
          )}
          <section className="guide-main" aria-labelledby="collection">
            <Bar id="collection" title="Collection">
              <span className="num">
                {filtered ? `${items.length} of ${all.length}` : all.length}
              </span>
              <Choice
                name="view"
                label="View"
                className="on-bar"
                value={view}
                onChange={(value) =>
                  filter("view", value === "list" ? "" : value)
                }
                options={[
                  [
                    "list",
                    <List size={16} strokeWidth={2.5} key="l" />,
                    "List",
                  ],
                  [
                    "covers",
                    <LayoutGrid size={16} strokeWidth={2.5} key="c" />,
                    "Covers",
                  ],
                ]}
              />
            </Bar>
            <div className="filters">
              <label className="search-field">
                <Search size={18} strokeWidth={2.25} aria-hidden="true" />
                <input
                  type="search"
                  data-search
                  aria-label="Search the collection"
                  placeholder="Search"
                  value={search}
                  onChange={(event) => {
                    setSearch(event.target.value);
                    filter("q", event.target.value);
                  }}
                />
              </label>
              <Choice
                name="type"
                label="Media type"
                value={type}
                onChange={(value) => filter("type", value)}
                options={[
                  ["", "All"],
                  ["movie", "Films"],
                  ["tv", "Series"],
                ]}
              />
              <select
                aria-label="Sort titles"
                value={sortShown}
                onChange={(event) => {
                  setSortShown(event.target.value);
                  filter(
                    "sort",
                    event.target.value === "title" ? "" : event.target.value,
                  );
                }}
              >
                <option value="title">A–Z</option>
                <option value="year">Year</option>
                <option value="recent">Library order</option>
              </select>
              <select
                aria-label="Availability"
                value={stateShown}
                onChange={(event) => {
                  setStateShown(event.target.value);
                  filter("state", event.target.value);
                }}
              >
                <option value="">Any status</option>
                <option value="ready">Playable</option>
                <option value="subtitles_pending">Needs subtitles</option>
                <option value="unavailable">Offline</option>
                <option value="verifying">Checking</option>
              </select>
            </div>
            {!items.length ? (
              <Empty
                title="Nothing matches."
                action={
                  <button
                    className="btn"
                    onClick={() => {
                      setSearch("");
                      setParams({});
                    }}
                  >
                    Clear filters
                  </button>
                }
              />
            ) : view === "covers" ? (
              <ul className="covers">
                {items.map((item) => {
                  const watching = item.assets.find(inProgress);
                  return (
                    <li key={item.id}>
                      <Link
                        className="cover-card"
                        data-nav
                        to={itemLink(item)}
                        state={from}
                        aria-label={`Open ${item.title}`}
                      >
                        <span className="cover-wrap">
                          <Cover title={item.title} src={item.poster_url} />
                          {watching && (
                            <Meter
                              value={progress(watching)}
                              label={`Watch progress for ${item.title}`}
                            />
                          )}
                        </span>
                        <span className="cover-title">
                          <Marked text={item.title} query={query} />
                        </span>
                        <span className="meta">
                          {[item.year, kind(item)].filter(Boolean).join(" · ")}
                        </span>
                        <Flag value={item.state} />
                      </Link>
                    </li>
                  );
                })}
              </ul>
            ) : (
              <div className="index">
                {groups.map(([key, rows]) => (
                  <section key={key || "all"} className="index-group">
                    {key && <h3 className="index-tab">{key}</h3>}
                    <ul>
                      {rows.map((item) => {
                        const watching = item.assets.find(inProgress);
                        return (
                          <li key={item.id}>
                            <Link
                              className="entry"
                              data-nav
                              to={itemLink(item)}
                              state={from}
                              aria-label={`Open ${item.title}`}
                            >
                              <Cover title={item.title} src={item.poster_url} />
                              <span className="entry-line">
                                <span className="entry-title">
                                  <Marked text={item.title} query={query} />
                                </span>
                                <span className="leader" aria-hidden="true" />
                                <span className="entry-meta num">
                                  {[item.year, kind(item)]
                                    .filter(Boolean)
                                    .join(" · ")}
                                </span>
                              </span>
                              <span className="entry-notes">
                                <Flag value={item.state} />
                                {watching && (
                                  <>
                                    <Meter
                                      value={progress(watching)}
                                      label={`Watch progress for ${item.title}`}
                                    />
                                    <span className="meta">
                                      {episodeCode(watching)}{" "}
                                      {progress(watching)}%
                                    </span>
                                  </>
                                )}
                              </span>
                            </Link>
                          </li>
                        );
                      })}
                    </ul>
                  </section>
                ))}
              </div>
            )}
          </section>
        </div>
      )}
    </main>
  );
}
