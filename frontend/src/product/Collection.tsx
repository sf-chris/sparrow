import { useWebSocket } from "../hooks/useWebSocket";
import { useMemo, useEffect } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  Play,
  ArrowUpRight,
  ArrowRight,
  Search,
  Compass,
  Plus,
} from "lucide-react";
import { Backdrop, PlayroomArt } from "./Brand";
import { api, type Item, type User, type Asset } from "./api";
import {
  ActionLink,
  Empty,
  ErrorNote,
  Loading,
  MediaCard,
  Page,
  Section,
  duration,
  itemLink,
  useResource,
} from "./ui";

function watchProgress(asset: Asset) {
  return Math.round(
    Math.min(
      100,
      Math.max(
        0,
        (asset.watch!.position / Math.max(1, asset.watch!.duration)) * 100,
      ),
    ),
  );
}

export default function Collection({
  home = false,
  user,
}: {
  home?: boolean;
  user: User;
}) {
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
  return (
    <Page
      className={home ? "sp-home-page" : "sp-library-page"}
      title={
        home
          ? `What’s on tonight, ${user.name.split(" ")[0]}?`
          : "Your library."
      }
    >
      <ErrorNote error={resource.error} retry={resource.refresh} />
      {!resource.data ? (
        resource.loading && <Loading />
      ) : (
        <>
          {home && empty && (
            <section
              className="sp-feature sp-feature-empty"
              aria-label="Welcome to your collection"
            >
              <PlayroomArt />
              <div className="sp-feature-copy">
                <p className="sp-eyebrow">A LITTLE ROOM FOR YOUR FAVOURITES</p>
                <h2>Make yourself at home.</h2>
                <p>
                  {user.role === "viewer"
                    ? "Your household’s stories will appear here. Ask your server owner to add a collection."
                    : "Your films. Your shows. All the stories you want to get lost in, together in one place."}
                </p>
                <div className="sp-actions">
                  {user.role === "admin" ? (
                    <Link className="sp-button primary" to="/settings/storage">
                      <Plus size={17} />
                      Bring your collection
                    </Link>
                  ) : (
                    <Link className="sp-button primary" to="/discover">
                      <Compass size={17} />
                      Explore titles
                    </Link>
                  )}
                  {user.role === "admin" && (
                    <Link className="sp-button secondary" to="/discover">
                      Find your first film <ArrowUpRight size={16} />
                    </Link>
                  )}
                </div>
              </div>
            </section>
          )}
          {home && continuing.length > 0 && (
            <Section
              title="Continue watching"
              action={
                <Link className="sp-button quiet" to="/library">
                  Your library <ArrowRight size={16} />
                </Link>
              }
            >
              <div
                className="sp-continue-grid"
                role="region"
                aria-label="Continue watching titles"
              >
                {continuing.map(({ item, asset }) => (
                  <article className="sp-continue" key={asset.id}>
                    <div className="sp-continue-art">
                      <Backdrop
                        src={item.backdrop_url || item.poster_url}
                        lazy
                      />
                      {asset.state === "ready" && (
                        <Link
                          className="sp-continue-play"
                          to={`/watch/${asset.id}`}
                          aria-label={`Resume ${item.title}`}
                        >
                          <Play size={20} fill="currentColor" />
                        </Link>
                      )}
                      <div
                        className="sp-continue-progress"
                        role="progressbar"
                        aria-label={`Watch progress for ${item.title}`}
                        aria-valuemin={0}
                        aria-valuemax={100}
                        aria-valuenow={watchProgress(asset)}
                      >
                        <span style={{ width: `${watchProgress(asset)}%` }} />
                      </div>
                    </div>
                    <div className="sp-continue-info">
                      <div>
                        <h3>
                          <Link to={itemLink(item)}>{item.title}</Link>
                        </h3>
                        <p>
                          {asset.episode
                            ? `S${asset.season} · E${asset.episode} · `
                            : ""}
                          {duration(
                            Math.max(
                              0,
                              asset.facts.duration - asset.watch!.position,
                            ),
                          )}{" "}
                          left
                        </p>
                        {asset.state !== "ready" && (
                          <p>Storage unavailable · progress saved</p>
                        )}
                      </div>
                      <ArrowUpRight size={17} />
                    </div>
                  </article>
                ))}
              </div>
            </Section>
          )}
          {empty ? (
            !home && (
              <Empty
                title="Your collection starts here."
                action={
                  <div className="sp-actions justify-center">
                    {user.role === "admin" && (
                      <ActionLink to="/settings/storage">
                        Connect storage & import
                      </ActionLink>
                    )}
                    {user.role !== "viewer" && (
                      <ActionLink to="/discover">
                        Find a movie or show
                      </ActionLink>
                    )}
                  </div>
                }
              >
                {user.role === "viewer"
                  ? "Titles shared with you will appear here. Ask the server owner to add a collection."
                  : "Bring the movies and shows you already own, then let Sparrow help with the rest."}
              </Empty>
            )
          ) : (
            <Section
              title={
                home ? "Now showing in your library" : "The whole collection"
              }
              action={
                home ? (
                  <Link className="sp-button quiet" to="/library">
                    View all <ArrowRight size={16} />
                  </Link>
                ) : (
                  <span className="sp-library-count">
                    <strong>{items.length}</strong>{" "}
                    {items.length === 1 ? "title" : "titles"}
                  </span>
                )
              }
            >
              {!home && (
                <div className="sp-toolbar">
                  <label className="sp-search">
                    <Search size={18} />
                    <input
                      aria-label="Search your library"
                      placeholder="Find something in your library…"
                      value={query}
                      onChange={(event) => filter("q", event.target.value)}
                    />
                  </label>
                  <select
                    aria-label="Media type"
                    value={type}
                    onChange={(event) => filter("type", event.target.value)}
                  >
                    <option value="">Films & series</option>
                    <option value="movie">Movies</option>
                    <option value="tv">TV shows</option>
                  </select>
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
                    <option value="recent">Library order</option>
                    <option value="title">Title: A–Z</option>
                    <option value="year">Newest release</option>
                  </select>
                </div>
              )}
              {items.length ? (
                <div className={home ? "sp-grid sp-poster-shelf" : "sp-grid"}>
                  {(home ? items.slice(0, 12) : items).map((item) => (
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
                  Try a different title or show everything in your collection.
                </Empty>
              )}
            </Section>
          )}
          {home && (
            <section className="sp-home-discover">
              <div>
                <h2>Your next favourite is out there.</h2>
                <p>Have a title in mind? Or just a feeling? Start there.</p>
              </div>
              <Link className="sp-button secondary" to="/discover">
                <Compass size={17} />
                Find your next watch <ArrowUpRight size={17} />
              </Link>
            </section>
          )}
        </>
      )}
    </Page>
  );
}
