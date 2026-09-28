import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams, useLocation } from "react-router-dom";
import { Search, ArrowRight } from "lucide-react";
import { api, post, type Item } from "./api";
import { Tick } from "./Brand";
import { Bar, Cover, Empty, ErrorNote, Loading, useResource } from "./ui";

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
const ideas = [
  "A mystery for tonight",
  "Something to watch together",
  "An adventure somewhere far away",
];

export default function Discover() {
  const location = useLocation();
  const [params, setParams] = useSearchParams();
  const [query, setQuery] = useState(params.get("q") || "");
  const [identity, setIdentity] = useState(params.get("session") || "");
  const [titles, setTitles] = useState<Card[]>([]);
  const [research, setResearch] = useState<Research | null>(null);
  const [pollVersion, setPollVersion] = useState(0);
  const [searching, setSearching] = useState(false);
  const [asking, setAsking] = useState(false);
  const [searched, setSearched] = useState("");
  const [error, setError] = useState("");
  const input = useRef<HTMLInputElement>(null);
  const owned = useResource(() => api<Item[]>("/catalogue"));
  const inGuide = useMemo(
    () =>
      new Set(
        (owned.data || []).map((item) => `${item.media_type}:${item.tmdb_id}`),
      ),
    [owned.data],
  );
  useEffect(() => {
    if (!params.get("q") && matchMedia("(pointer: fine)").matches)
      input.current?.focus();
  }, []);
  useEffect(() => {
    if (!identity) return;
    let cancelled = false,
      timer: number;
    const poll = async () => {
      try {
        const result = await api<Research>(`/discovery/${identity}`);
        if (cancelled) return;
        setResearch(result);
        setAsking(result.state === "running");
        setError("");
        if (result.state === "running") timer = window.setTimeout(poll, 1500);
      } catch (e) {
        if (!cancelled) {
          setAsking(false);
          setError((e as Error).message);
        }
      }
    };
    void poll();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [identity, pollVersion]);
  useEffect(() => {
    const text = query.trim();
    if (text.length < 2) {
      setTitles([]);
      setSearching(false);
      setSearched("");
      return;
    }
    let cancelled = false;
    const timer = window.setTimeout(async () => {
      setSearching(true);
      try {
        const result = await api<Card[]>(
          `/suggest?q=${encodeURIComponent(text)}`,
        );
        if (!cancelled) {
          setTitles(result);
          setSearched(text);
          setError("");
        }
      } catch (e) {
        if (!cancelled) setError((e as Error).message);
      } finally {
        if (!cancelled) setSearching(false);
      }
    }, 300);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [query]);
  function remember(text: string, session = identity) {
    setParams(
      {
        ...(text ? { q: text } : {}),
        ...(session ? { mode: "assisted", session } : {}),
      },
      { replace: true },
    );
  }
  async function ask() {
    setError("");
    setAsking(true);
    try {
      const result = await post<{ id: string }>("/discovery", {
        message: query,
        session_id: identity || undefined,
      });
      setResearch(null);
      setIdentity(result.id);
      setPollVersion((v) => v + 1);
      remember(query, result.id);
    } catch (e) {
      setError((e as Error).message);
      setAsking(false);
    }
  }
  async function stop() {
    try {
      await api(`/discovery/${identity}`, { method: "DELETE" });
      setAsking(false);
      setResearch((previous) =>
        previous
          ? { ...previous, state: "closed", status_line: "Stopped." }
          : previous,
      );
    } catch (e) {
      setError((e as Error).message);
    }
  }
  function startOver() {
    if (asking) void stop();
    setIdentity("");
    setResearch(null);
    setAsking(false);
    remember(query, "");
  }
  const from = { from: location.pathname + location.search };
  function rows(cards: Card[]) {
    return (
      <ul className="results">
        {cards.map((card) => (
          <li key={`${card.media_type}:${card.tmdb_id}`}>
            <Link
              className="entry"
              data-nav
              to={`/title/${card.media_type}/${card.tmdb_id}`}
              state={from}
            >
              <Cover
                title={card.title}
                src={
                  card.poster_url ||
                  (card.poster_path
                    ? `https://image.tmdb.org/t/p/w342${card.poster_path}`
                    : undefined)
                }
              />
              <span className="entry-line">
                <span className="entry-title">{card.title}</span>
                {inGuide.has(`${card.media_type}:${card.tmdb_id}`) && (
                  <span className="owned" title="In your guide">
                    <Tick />
                    <span className="sr-only">In your guide</span>
                  </span>
                )}
                <span className="leader" aria-hidden="true" />
                <span className="entry-meta num">
                  {[card.year, card.media_type === "tv" ? "Series" : "Film"]
                    .filter(Boolean)
                    .join(" · ")}
                </span>
              </span>
            </Link>
          </li>
        ))}
      </ul>
    );
  }
  const typed = query.trim().length >= 2;
  return (
    <main id="main-content" className="page find">
      <header className="page-head">
        <h1 className="display">Find</h1>
      </header>
      <form
        className="finder"
        role="search"
        onSubmit={(event) => {
          event.preventDefault();
          document.querySelector<HTMLElement>(".results [data-nav]")?.focus();
        }}
      >
        <label className="finder-field">
          <Search size={24} strokeWidth={2.25} aria-hidden="true" />
          <input
            ref={input}
            type="search"
            data-search
            aria-label="Find a title or describe a mood"
            placeholder="A title, or a mood"
            maxLength={2000}
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              remember(e.target.value);
            }}
          />
        </label>
        <button
          type="button"
          className="btn primary"
          disabled={asking || !typed}
          onClick={() => void ask()}
        >
          {identity ? "Ask again" : "Ask Sparrow"}
          <ArrowRight size={18} strokeWidth={2.5} />
        </button>
      </form>
      <ErrorNote error={error} />
      {identity && (
        <section className="picks" aria-labelledby="picks">
          <Bar id="picks" title="Sparrow’s picks">
            {asking && (
              <button className="bar-button" onClick={() => void stop()}>
                Stop
              </button>
            )}
            <button className="bar-button" onClick={startOver}>
              Start over
            </button>
          </Bar>
          {asking && <Loading label="Reading titles and your guide" />}
          {research?.message && <p className="pick-note">{research.message}</p>}
          {research && !asking && !research.message && (
            <p className="muted pick-status" role="status">
              {research.status_line} Change the words and ask again, or start
              over.
            </p>
          )}
          {!!research?.cards.length && rows(research.cards)}
        </section>
      )}
      {typed && (!identity || titles.length > 0) && (
        <section aria-labelledby="titles">
          <Bar id="titles" title="Titles">
            {searching && <span>Searching…</span>}
          </Bar>
          {titles.length
            ? rows(titles)
            : searched &&
              !searching &&
              !error && (
                <Empty title="No title by that name.">
                  Check the spelling, or ask Sparrow to look for it by
                  description.
                </Empty>
              )}
        </section>
      )}
      {!query && !identity && (
        <section aria-labelledby="ideas" className="ideas">
          <Bar id="ideas" title="Ideas" />
          <ul>
            {ideas.map((idea) => (
              <li key={idea}>
                <button
                  className="idea"
                  data-nav
                  onClick={() => {
                    setQuery(idea);
                    remember(idea);
                    input.current?.focus();
                  }}
                >
                  <span>{idea}</span>
                  <span className="leader" aria-hidden="true" />
                  <ArrowRight size={18} strokeWidth={2.5} aria-hidden="true" />
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}
    </main>
  );
}
