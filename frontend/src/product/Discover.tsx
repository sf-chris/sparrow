import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams, useLocation } from "react-router-dom";
import { Search } from "lucide-react";
import { api, type Item } from "./api";
import { Tick } from "./Brand";
import { Bar, Cover, ErrorNote, useResource } from "./ui";

type Card = {
  tmdb_id: number;
  media_type: "movie" | "tv";
  title: string;
  year: string;
  overview?: string;
  poster_path?: string;
  poster_url?: string;
};

/** Find: a title search. Matches appear as you type. */
export default function Discover() {
  const location = useLocation();
  const [params, setParams] = useSearchParams();
  const [query, setQuery] = useState(params.get("q") || "");
  const [titles, setTitles] = useState<Card[]>([]);
  const [searching, setSearching] = useState(false);
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
  const from = { from: location.pathname + location.search };
  const text = query.trim();
  const typed = text.length >= 2;
  const nothing = typed && !searching && searched === text && !titles.length;
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
          document
            .querySelector<HTMLElement>(".find .results [data-nav]")
            ?.focus();
        }}
      >
        <label className="finder-field">
          <Search size={24} strokeWidth={2.25} aria-hidden="true" />
          <input
            ref={input}
            type="search"
            data-search
            aria-label="Find a film or series"
            placeholder="A film or series"
            maxLength={500}
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setParams(e.target.value ? { q: e.target.value } : {}, {
                replace: true,
              });
            }}
          />
        </label>
      </form>
      <ErrorNote error={error} />
      {typed && (titles.length > 0 || searching) && (
        <section aria-labelledby="titles">
          <Bar id="titles" title="Titles">
            {searching && <span>Searching…</span>}
          </Bar>
          <ul className="results">
            {titles.map((card) => (
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
                      <span className="owned" title="In your collection">
                        <Tick />
                        <span className="sr-only">In your collection</span>
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
        </section>
      )}
      {nothing && !error && (
        <p className="muted find-nothing" role="status">
          No film or series called “{text}”. Check the spelling.
        </p>
      )}
    </main>
  );
}
