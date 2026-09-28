import { useWebSocket } from "../hooks/useWebSocket";
import { useMemo, useEffect } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Play, ArrowRight, Search } from "lucide-react";
import { Backdrop } from "./Brand";
import { api, type Item, type User, type Asset, type Job } from "./api";
import {
  ActionLink,
  Empty,
  ErrorNote,
  Loading,
  MediaCard,
  Page,
  Progress,
  Section,
  duration,
  itemLink,
  progressOf,
  useResource,
} from "./ui";

const remaining = (asset: Asset) =>
  duration(Math.max(0, asset.facts.duration - (asset.watch?.position || 0)));

export default function Collection({
  home = false,
  user,
}: {
  home?: boolean;
  user: User;
}) {
  const resource = useResource(() => api<Item[]>("/catalogue"));
  const requests = useResource(() =>
    home && user.role !== "viewer"
      ? api<Job[]>("/jobs").catch(() => [])
      : Promise.resolve([] as Job[]),
  );
  useWebSocket(() => {
    void resource.refresh();
    if (home) void requests.refresh();
  });
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
  const items = useMemo(() => {
    const result = (resource.data || []).filter(
      (item) =>
        (!query || item.title.toLowerCase().includes(query.toLowerCase())) &&
        (!type || item.media_type === type) &&
        (!state || item.state === state),
    );
    if (sort === "title") result.sort((a, b) => a.title.localeCompare(b.title));
    if (sort === "year") result.sort((a, b) => (b.year || 0) - (a.year || 0));
    return result;
  }, [resource.data, query, type, state, sort]);
  const continuing = (resource.data || [])
    .flatMap((item) =>
      item.assets
        .filter(
          (asset) =>
            asset.watch && !asset.watch.watched && asset.watch.position > 5,
        )
        .map((asset) => ({ item, asset })),
    )
    .sort((a, b) => b.asset.watch!.updated - a.asset.watch!.updated)
    .slice(0, 12);
  const empty = resource.data?.length === 0;
  const first = user.name.split(" ")[0];

  if (!home)
    return (
      <Page
        className="sp-library"
        title="Library"
        action={
          resource.data && !empty ? (
            <span className="sp-count">
              {items.length} {items.length === 1 ? "title" : "titles"}
            </span>
          ) : undefined
        }
      >
        <ErrorNote error={resource.error} retry={resource.refresh} />
        {!resource.data ? (
          resource.loading && <Loading />
        ) : empty ? (
          <Empty
            title="Your collection starts here."
            action={
              <div className="sp-actions">
                {user.role === "admin" && (
                  <ActionLink to="/settings/storage">
                    Connect storage & import
                  </ActionLink>
                )}
                {user.role !== "viewer" && (
                  <ActionLink to="/discover">Find a movie or show</ActionLink>
                )}
              </div>
            }
          >
            {user.role === "viewer"
              ? "Titles shared with you will appear here. Ask the server owner to add a collection."
              : "Bring the movies and shows you already own, then let Sparrow find the rest."}
          </Empty>
        ) : (
          <>
            <div className="sp-filters">
              <label className="sp-searchfield">
                <Search size={18} aria-hidden="true" />
                <input
                  type="search"
                  aria-label="Search your library"
                  placeholder="Search your library"
                  value={query}
                  onChange={(event) => filter("q", event.target.value)}
                />
              </label>
              <div
                className="sp-segments"
                role="radiogroup"
                aria-label="Media type"
              >
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
                    <span>{label}</span>
                  </label>
                ))}
              </div>
              <select
                aria-label="Availability"
                value={state}
                onChange={(event) => filter("state", event.target.value)}
              >
                <option value="">Any availability</option>
                <option value="ready">Ready to watch</option>
                <option value="unavailable">Storage unavailable</option>
                <option value="subtitles_pending">
                  Subtitles need attention
                </option>
                <option value="verifying">Needs verification</option>
              </select>
              <select
                aria-label="Sort titles"
                value={sort}
                onChange={(event) => filter("sort", event.target.value)}
              >
                <option value="recent">Recently added</option>
                <option value="title">A–Z</option>
                <option value="year">Newest release</option>
              </select>
            </div>
            {items.length ? (
              <div className="sp-grid">
                {items.map((item) => (
                  <MediaCard key={item.id} item={item} />
                ))}
              </div>
            ) : (
              <Empty
                title="No titles match those filters."
                action={
                  <button
                    className="sp-button secondary"
                    onClick={() => setParams({})}
                  >
                    Clear filters
                  </button>
                }
              >
                Try a different title, or show everything.
              </Empty>
            )}
          </>
        )}
      </Page>
    );

  const featured = continuing[0];
  const lead = featured?.item || resource.data?.[0];
  const leadAsset =
    featured?.asset ||
    lead?.assets.find(
      (asset) => asset.state === "ready" && !asset.watch?.watched,
    );
  const onTheWay = (requests.data || []).filter(
    (job) => ["active", "paused"].includes(job.status) || job.needs_attention,
  );
  return (
    <main id="main-content" className="sp-page sp-home">
      <ErrorNote error={resource.error} retry={resource.refresh} />
      {!resource.data ? (
        <>
          <h1 className="sp-hello">Welcome back, {first}.</h1>
          {resource.loading && <Loading />}
        </>
      ) : empty || !lead ? (
        <section className="sp-first-run" aria-labelledby="home-title">
          <h1 className="sp-hello" id="home-title">
            Welcome back, {first}.
          </h1>
          <h2>Nothing to watch yet.</h2>
          <p>
            {user.role === "viewer"
              ? "Your household’s films and shows will appear here once the server owner adds them."
              : user.role === "admin"
                ? "Point Sparrow at the films and shows you already own, or name something and it will fetch it."
                : "Name something you’d like to watch and Sparrow will fetch it."}
          </p>
          <div className="sp-actions">
            {user.role === "admin" && (
              <Link className="sp-button primary" to="/settings/storage">
                Bring your collection
              </Link>
            )}
            <Link
              className={`sp-button ${user.role === "admin" ? "secondary" : "primary"}`}
              to="/discover"
            >
              {user.role === "viewer"
                ? "Explore titles"
                : "Find a film or show"}
            </Link>
          </div>
        </section>
      ) : (
        <>
          <h1 className="sp-hello">Welcome back, {first}.</h1>
          <section className="sp-hero" aria-label={lead.title}>
            <Link
              className="sp-hero-art"
              to={itemLink(lead)}
              tabIndex={-1}
              aria-hidden="true"
            >
              <Backdrop
                src={lead.backdrop_url || lead.poster_url}
                title={lead.title}
              />
            </Link>
            <div className="sp-hero-copy">
              <p className="sp-kicker">
                {featured
                  ? featured.asset.episode
                    ? `Season ${featured.asset.season} · Episode ${featured.asset.episode}`
                    : "Continue watching"
                  : "Newest in your library"}
              </p>
              <h2 className="sp-hero-title">{lead.title}</h2>
              {featured ? (
                <div className="sp-hero-progress">
                  <Progress
                    value={progressOf(featured.asset)}
                    label={`Watch progress for ${lead.title}`}
                  />
                  <span>{remaining(featured.asset)} left</span>
                </div>
              ) : (
                lead.overview && (
                  <p className="sp-hero-overview">{lead.overview}</p>
                )
              )}
              <div className="sp-actions">
                {leadAsset?.state === "ready" && (
                  <Link
                    className="sp-button primary"
                    to={`/watch/${leadAsset.id}`}
                    aria-label={`${featured ? "Resume" : "Play"} ${lead.title}`}
                  >
                    <Play size={16} fill="currentColor" />
                    {featured ? "Resume" : "Play"}
                  </Link>
                )}
                <Link className="sp-button secondary" to={itemLink(lead)}>
                  Details
                </Link>
              </div>
              {featured && featured.asset.state !== "ready" && (
                <p className="sp-quiet">Storage unavailable · progress saved</p>
              )}
            </div>
          </section>
          {continuing.length > 1 && (
            <Section title="Continue watching">
              <div
                className="sp-rail"
                role="region"
                aria-label="Continue watching titles"
              >
                {continuing.slice(1).map(({ item, asset }) => (
                  <article className="sp-continue" key={asset.id}>
                    <Link
                      to={
                        asset.state === "ready"
                          ? `/watch/${asset.id}`
                          : itemLink(item)
                      }
                      aria-label={`Resume ${item.title}`}
                    >
                      <div className="sp-continue-art">
                        <Backdrop
                          src={item.backdrop_url || item.poster_url}
                          title={item.title}
                          lazy
                        />
                        <span className="sp-continue-play" aria-hidden="true">
                          <Play size={16} fill="currentColor" />
                        </span>
                      </div>
                      <Progress
                        value={progressOf(asset)}
                        label={`Watch progress for ${item.title}`}
                      />
                      <h3>{item.title}</h3>
                      <p>
                        {asset.episode
                          ? `S${asset.season} E${asset.episode} · `
                          : ""}
                        {remaining(asset)} left
                        {asset.state !== "ready" && " · storage unavailable"}
                      </p>
                    </Link>
                  </article>
                ))}
              </div>
            </Section>
          )}
          {onTheWay.length > 0 && (
            <Section
              title="On the way"
              action={
                <Link className="sp-more" to="/activity">
                  Activity <ArrowRight size={15} />
                </Link>
              }
            >
              <ul className="sp-arrivals">
                {onTheWay.slice(0, 4).map((job) => (
                  <li key={job.id}>
                    <Link to={`/activity?request=${job.id}`}>
                      <span
                        className={`sp-dot ${job.needs_attention ? "attention" : job.status}`}
                        aria-hidden="true"
                      />
                      <strong>{job.title}</strong>
                      <span>
                        {job.state_line ||
                          "Sparrow is checking what this needs."}
                      </span>
                    </Link>
                  </li>
                ))}
              </ul>
            </Section>
          )}
          <Section
            title="Recently added"
            action={
              <Link className="sp-more" to="/library">
                Library <ArrowRight size={15} />
              </Link>
            }
          >
            <div className="sp-grid sp-grid-row">
              {(resource.data || []).slice(0, 12).map((item) => (
                <MediaCard key={item.id} item={item} />
              ))}
            </div>
          </Section>
        </>
      )}
    </main>
  );
}
