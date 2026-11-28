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
/** Titles for some typed text; `more` asks for a wider follow-up search. */
type Answer = { titles: Card[]; more: boolean };

/** Answers seen this visit, so deleting a letter or coming back is instant. */
const answers = new Map<string, Answer>();

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
  // Every keystroke searches; the previous titles stay until the next arrive.
  useEffect(() => {
    const key = query.trimStart();
    if (!key.trim()) {
      setTitles([]);
      setSearching(false);
      setSearched("");
      return;
    }
    const control = new AbortController();
    let wider = 0;
    const ask = (wide: boolean) =>
      api<Answer>(
        `/suggest?q=${encodeURIComponent(key)}${wide ? "&wide=1" : ""}`,
        { signal: control.signal },
      );
    const failed = (e: unknown) => {
      if (control.signal.aborted) return;
      setError((e as Error).message);
      setSearching(false);
    };
    const show = (answer: Answer) => {
      setTitles(answer.titles);
      setSearched(key);
      setError("");
      setSearching(answer.more);
      // Few clean matches: once typing pauses, ask the wider catalogue too.
      if (answer.more)
        wider = window.setTimeout(
          () =>
            ask(true).then((wide) => {
              answers.set(key, wide);
              show(wide);
            }, failed),
          250,
        );
    };
    const known = answers.get(key);
    if (known) show(known);
    else {
      setSearching(true);
      ask(false).then((answer) => {
        answers.set(key, answer);
        show(answer);
      }, failed);
    }
    return () => {
      control.abort();
      clearTimeout(wider);
    };
  }, [query]);
  function change(value: string) {
    setQuery(value);
    setParams(value ? { q: value } : {}, { replace: true });
  }
  const from = { from: location.pathname + location.search };
  const text = query.trim();
  const typed = text.length >= 1;
  const nothing =
    typed && !searching && searched === query.trimStart() && !titles.length;
  // The rest of the best match's name, faded after the typing; Tab takes it.
  const best = titles[0];
  const rest =
    best &&
    query.length < 60 &&
    best.title.length > query.length &&
    best.title.toLocaleLowerCase().startsWith(query.toLocaleLowerCase())
      ? best.title.slice(query.length)
      : "";
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
          <span className="finder-input">
            <input
              ref={input}
              type="search"
              data-search
              aria-label="Find a film or series"
              placeholder="A film or series"
              maxLength={500}
              autoComplete="off"
              spellCheck={false}
              value={query}
              onChange={(e) => change(e.target.value)}
              onKeyDown={(e) => {
                const end = e.currentTarget.selectionStart === query.length;
                if (
                  rest &&
                  ((e.key === "Tab" && !e.shiftKey) ||
                    (e.key === "ArrowRight" && end))
                ) {
                  e.preventDefault();
                  change(best.title);
                }
              }}
            />
            {rest && (
              <span className="finder-rest" aria-hidden="true">
                <span>{query}</span>
                {rest}
              </span>
            )}
          </span>
        </label>
      </form>
      <ErrorNote error={error} />
      {typed && (titles.length > 0 || searching) && (
        <section aria-labelledby="titles">
          <Bar id="titles" title="Titles">
            {searching && !titles.length && <span>Searching…</span>}
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
