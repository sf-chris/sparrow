import { useEffect, useState } from "react";
import { Link, useSearchParams, useLocation } from "react-router-dom";
import { ArrowRight, Search, MessageSquareText } from "lucide-react";
import { Mark } from "./Brand";
import { api, post } from "./api";
import { Empty, ErrorNote, Loading, Page, Poster, Section, kind } from "./ui";

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
const ideas = ["A mystery for tonight", "Something to watch together", "An adventure somewhere far away"];

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
        const result = await api<Card[]>(`/suggest?q=${encodeURIComponent(query.trim())}`);
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
        previous ? { ...previous, state: "closed", status_line: "Search stopped." } : previous,
      );
    } catch (e) {
      setError((e as Error).message);
    }
  }
  function choose(next: "title" | "assisted") {
    setMode(next);
    setError("");
    if (next === "title") {
      setBusy(false);
      setCards([]);
      setParams({ q: query });
    } else {
      setBusy(research?.state === "running");
      setCards(research?.cards || []);
      setParams({ q: query, mode: "assisted", ...(identity ? { session: identity } : {}) });
    }
  }
  return (
    <Page className="sp-find" title="Find">
      <div className="sp-finder">
        <div
          className="sp-tabs"
          role="tablist"
          aria-label="Discovery method"
          onKeyDown={(event) => {
            if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
            event.preventDefault();
            const tabs = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="tab"]'));
            const index = tabs.indexOf(document.activeElement as HTMLButtonElement);
            const target =
              event.key === "Home"
                ? 0
                : event.key === "End"
                  ? tabs.length - 1
                  : (index + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
            tabs[target].click();
            tabs[target].focus();
          }}
        >
          <button
            role="tab"
            aria-selected={mode === "title"}
            tabIndex={mode === "title" ? 0 : -1}
            onClick={() => choose("title")}
          >
            <Search size={16} aria-hidden="true" />I know the title
          </button>
          <button
            role="tab"
            aria-selected={mode === "assisted"}
            tabIndex={mode === "assisted" ? 0 : -1}
            onClick={() => choose("assisted")}
          >
            <MessageSquareText size={16} aria-hidden="true" />
            Help me choose
          </button>
        </div>
        <form onSubmit={submit} className="sp-finder-form">
          <label className="sp-finder-field">
            <span className="sp-label">
              {mode === "title" ? "Film or series title" : "What would you like to watch?"}
            </span>
            {mode === "title" ? (
              <input
                type="search"
                value={query}
                onChange={(e) => {
                  setQuery(e.target.value);
                  setParams({ q: e.target.value }, { replace: true });
                }}
                placeholder="Start typing…"
              />
            ) : (
              <textarea
                value={query}
                rows={3}
                onChange={(e) => {
                  setQuery(e.target.value);
                  setParams(
                    { q: e.target.value, mode: "assisted", ...(identity ? { session: identity } : {}) },
                    { replace: true },
                  );
                }}
                maxLength={2000}
                placeholder="A clever mystery we can finish tonight…"
              />
            )}
          </label>
          {mode === "assisted" && (
            <div className="sp-finder-foot">
              <p className="sp-hint">Nothing is downloaded until you pick a title.</p>
              <div className="sp-actions">
                {busy && identity && (
                  <button type="button" className="sp-btn sp-btn-ghost" onClick={stop}>
                    Stop search
                  </button>
                )}
                {identity && (
                  <button
                    type="button"
                    className="sp-btn sp-btn-line"
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
                <button className="sp-btn sp-btn-solid" disabled={busy || query.trim().length < 2}>
                  {identity ? "Refine" : "Ask Sparrow"}
                  <ArrowRight size={16} aria-hidden="true" />
                </button>
              </div>
            </div>
          )}
        </form>
      </div>
      <ErrorNote error={error} />
      {busy && (
        <Loading label="Searching…" />
      )}
      {mode === "assisted" && research?.message && (
        <figure className="sp-usher">
          <Mark />
          <blockquote>{research.message}</blockquote>
        </figure>
      )}
      {mode === "assisted" && research && !busy && !research.message && (
        <p className="sp-note" role="status">
          {research.status_line}
        </p>
      )}
      {!!cards.length && (
        <Section title={mode === "title" ? "Matching titles" : "Suggestions"}>
          <div className="sp-prints">
            {cards.map((card) => (
              <article className="sp-print" key={`${card.media_type}:${card.tmdb_id}`}>
                <Link
                  to={`/title/${card.media_type}/${card.tmdb_id}`}
                  state={{ from: location.pathname + location.search }}
                >
                  <div className="sp-print-art">
                    <Poster
                      title={card.title}
                      src={
                        card.poster_url ||
                        (card.poster_path ? `https://image.tmdb.org/t/p/w500${card.poster_path}` : undefined)
                      }
                    />
                  </div>
                  <div className="sp-print-caption">
                    <span className="sp-label">
                      <span>{kind(card.media_type)}</span>
                      <span>{card.year}</span>
                    </span>
                    <h3>{card.title}</h3>
                  </div>
                </Link>
              </article>
            ))}
          </div>
        </Section>
      )}
      {!query && !busy && !research && (
        <section className="sp-slips" aria-labelledby="slips-title">
          <h2 className="sp-label" id="slips-title">
            Or ask for
          </h2>
          <div>
            {ideas.map((idea) => (
              <button
                key={idea}
                className="sp-slip"
                onClick={() => {
                  setMode("assisted");
                  setQuery(idea);
                  setParams({ mode: "assisted", q: idea });
                }}
              >
                <span className="sp-slip-idea">{idea}</span>
                <span className="sp-slip-go" aria-hidden="true">
                  <ArrowRight size={18} />
                </span>
              </button>
            ))}
          </div>
        </section>
      )}
      {!cards.length && !busy && !error && mode === "title" && query.length > 0 && (
        <Empty title={searched && query.length > 1 ? "No matches." : "Keep typing…"}>
          {searched && query.length > 1 ? "Check the spelling, or try Help me choose." : undefined}
        </Empty>
      )}
    </Page>
  );
}
