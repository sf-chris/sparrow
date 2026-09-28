import { useEffect, useState } from "react";
import { Link, useSearchParams, useLocation } from "react-router-dom";
import { Search } from "lucide-react";
import { api, post } from "./api";
import { Empty, ErrorNote, Loading, Poster, Section } from "./ui";

type Card = {
  tmdb_id: number;
  media_type: "movie" | "tv";
  title: string;
  year: string;
  overview?: string;
  poster_path?: string;
  poster_url?: string;
};
type Research = {
  id: string;
  query: string;
  message: string;
  cards: Card[];
  state: string;
  status_line: string;
};
export default function Discover() {
  const location = useLocation();
  const [params, setParams] = useSearchParams();
  const [query, setQuery] = useState(params.get("q") || "");
  const [mode, setMode] = useState<"title" | "assisted">(
    params.get("mode") === "assisted" ? "assisted" : "title",
  );
  const [cards, setCards] = useState<Card[]>([]);
  const [research, setResearch] = useState<Research | null>(null);
  const [identity, setIdentity] = useState(params.get("session") || "");
  const [pollVersion, setPollVersion] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [searched, setSearched] = useState(false);
  useEffect(() => {
    if (!identity || mode !== "assisted") return;
    let cancelled = false,
      timer: number;
    const poll = async () => {
      try {
        const result = await api<Research>(`/discovery/${identity}`);
        if (cancelled) return;
        setResearch(result);
        setCards(result.cards);
        setBusy(result.state === "running");
        setError("");
        if (result.state === "running") timer = window.setTimeout(poll, 1500);
      } catch (e) {
        if (!cancelled) {
          setBusy(false);
          setError((e as Error).message);
        }
      }
    };
    void poll();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [identity, mode, pollVersion]);
  useEffect(() => {
    if (mode !== "title" || query.trim().length < 2) {
      if (mode === "title") {
        setCards([]);
        setBusy(false);
        setSearched(false);
        setError("");
      }
      return;
    }
    let cancelled = false;
    const timer = window.setTimeout(async () => {
      setBusy(true);
      try {
        const result = await api<Card[]>(
          `/suggest?q=${encodeURIComponent(query.trim())}`,
        );
        if (!cancelled) {
          setCards(result);
          setError("");
          setSearched(true);
        }
      } catch (e) {
        if (!cancelled) setError((e as Error).message);
      } finally {
        if (!cancelled) setBusy(false);
      }
    }, 300);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [query, mode]);
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (mode === "title") {
      setParams({ q: query });
      return;
    }
    setError("");
    setBusy(true);
    setSearched(true);
    try {
      const result = await post<{ id: string }>("/discovery", {
        message: query,
        session_id: identity || undefined,
      });
      setResearch(null);
      setIdentity(result.id);
      setPollVersion((v) => v + 1);
      setParams({ q: query, mode, session: result.id });
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }
  async function stop() {
    try {
      await api(`/discovery/${identity}`, { method: "DELETE" });
      setBusy(false);
      setResearch((previous) =>
        previous
          ? { ...previous, state: "closed", status_line: "Search stopped." }
          : previous,
      );
    } catch (e) {
      setError((e as Error).message);
    }
  }
  const titleMode = mode === "title";
  return (
    <main id="main-content" className="sp-page sp-find">
      <header className="sp-find-head">
        <h1>Discover</h1>
        <div
          className="sp-switch"
          role="tablist"
          aria-label="Discovery method"
          onKeyDown={(event) => {
            if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key))
              return;
            event.preventDefault();
            const tabs = Array.from(
              event.currentTarget.querySelectorAll<HTMLButtonElement>(
                '[role="tab"]',
              ),
            );
            const index = tabs.indexOf(
              document.activeElement as HTMLButtonElement,
            );
            const target =
              event.key === "Home"
                ? 0
                : event.key === "End"
                  ? tabs.length - 1
                  : (index +
                      (event.key === "ArrowRight" ? 1 : -1) +
                      tabs.length) %
                    tabs.length;
            tabs[target].click();
            tabs[target].focus();
          }}
        >
          <button
            role="tab"
            aria-selected={titleMode}
            tabIndex={titleMode ? 0 : -1}
            onClick={() => {
              setMode("title");
              setBusy(false);
              setCards([]);
              setError("");
              setParams({ q: query });
            }}
          >
            I know the name
          </button>
          <button
            role="tab"
            aria-selected={!titleMode}
            tabIndex={titleMode ? -1 : 0}
            onClick={() => {
              setMode("assisted");
              setBusy(research?.state === "running");
              setCards(research?.cards || []);
              setError("");
              setParams({
                q: query,
                mode: "assisted",
                ...(identity ? { session: identity } : {}),
              });
            }}
          >
            Describe it
          </button>
        </div>
        <form onSubmit={submit} className="sp-ask">
          {titleMode ? (
            <label className="sp-ask-field">
              <Search size={26} aria-hidden="true" />
              <input
                type="search"
                aria-label="Film or show title"
                value={query}
                autoFocus
                onChange={(e) => {
                  setQuery(e.target.value);
                  setParams({ q: e.target.value }, { replace: true });
                }}
                placeholder="Type a film or show"
              />
            </label>
          ) : (
            <>
              <textarea
                className="sp-ask-describe"
                aria-label="What would you like to watch?"
                value={query}
                onChange={(e) => {
                  setQuery(e.target.value);
                  setParams(
                    {
                      q: e.target.value,
                      mode: "assisted",
                      ...(identity ? { session: identity } : {}),
                    },
                    { replace: true },
                  );
                }}
                maxLength={2000}
                placeholder="A clever mystery we can finish tonight…"
              />
              <div className="sp-actions">
                <button
                  className="sp-button primary"
                  disabled={busy || query.trim().length < 2}
                >
                  {identity
                    ? "Refine these suggestions"
                    : "Find something for me"}
                </button>
                {identity && (
                  <button
                    type="button"
                    className="sp-button secondary"
                    onClick={() => {
                      if (busy) void stop();
                      setIdentity("");
                      setResearch(null);
                      setCards([]);
                      setParams({ mode: "assisted" });
                      setBusy(false);
                    }}
                  >
                    New search
                  </button>
                )}
                {busy && identity && (
                  <button
                    type="button"
                    className="sp-button quiet"
                    onClick={stop}
                  >
                    Stop search
                  </button>
                )}
              </div>
            </>
          )}
        </form>
        {!query && !busy && !research && (
          <div className="sp-ideas" aria-label="Ideas to explore" role="group">
            <span aria-hidden="true">Or describe a mood:</span>
            {[
              "A mystery for tonight",
              "Something to watch together",
              "An adventure somewhere far away",
            ].map((idea) => (
              <button
                key={idea}
                onClick={() => {
                  setMode("assisted");
                  setQuery(idea);
                  setParams({ mode: "assisted", q: idea });
                }}
              >
                {idea}
              </button>
            ))}
          </div>
        )}
      </header>
      <ErrorNote error={error} />
      {busy && (
        <Loading
          label={
            titleMode
              ? "Finding titles…"
              : "Checking titles and your collection…"
          }
        />
      )}
      {!titleMode && research?.message && (
        <Section title="A few possibilities">
          <p className="sp-answer">{research.message}</p>
        </Section>
      )}
      {!titleMode && research && !busy && !research.message && (
        <p className="sp-muted" role="status">
          {research.status_line} You can refine the description or start a new
          search.
        </p>
      )}
      {!!cards.length && (
        <Section
          title={
            titleMode ? "Matching titles" : "Open a title to choose what to get"
          }
        >
          <div className="sp-grid">
            {cards.map((card) => (
              <article
                className="sp-tile"
                key={`${card.media_type}:${card.tmdb_id}`}
              >
                <Link
                  to={`/title/${card.media_type}/${card.tmdb_id}`}
                  state={{ from: location.pathname + location.search }}
                >
                  <Poster
                    title={card.title}
                    src={
                      card.poster_url ||
                      (card.poster_path
                        ? `https://image.tmdb.org/t/p/w500${card.poster_path}`
                        : undefined)
                    }
                  />
                  <h3>{card.title}</h3>
                  <p>
                    {[card.year, card.media_type === "tv" ? "Series" : "Film"]
                      .filter(Boolean)
                      .join(" · ")}
                  </p>
                </Link>
              </article>
            ))}
          </div>
        </Section>
      )}
      {!cards.length && !busy && !error && titleMode && query.length > 0 && (
        <Empty
          title={
            searched && query.length > 1
              ? "No matching titles yet."
              : "Keep typing."
          }
        >
          {searched && query.length > 1
            ? "Try another spelling, or switch to Describe it."
            : "Two letters or more and Sparrow starts looking."}
        </Empty>
      )}
    </main>
  );
}
