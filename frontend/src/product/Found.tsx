import { useState } from "react";
import { Check, RefreshCw } from "lucide-react";
import { api, post, type NodeInfo } from "./api";
import { Tick } from "./Brand";
import { ago } from "./Collection";
import { Dialog, ErrorNote, Field, Loading, bytes, useResource } from "./ui";

type Guess = {
  media_type: "movie" | "tv";
  title: string;
  year: number | null;
  season: number | null;
  episode: number | null;
};
type Waiting = {
  root_id: string;
  path: string;
  name: string;
  size_bytes: number;
  guess: Guess | null;
  reason: string;
};
type Files = { unsure: Waiting[]; added: { path: string; title: string; at: number }[] };
type Named = {
  tmdb_id: number;
  media_type: "movie" | "tv";
  title: string;
  year?: string;
  season: number | null;
  episode: number | null;
};

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

/** How long ago, in words: "just now", "2h ago". */
function since(seconds: number) {
  const value = ago(seconds);
  return value === "now" ? "just now" : `${value} ago`;
}

/** The Found line of a computer's card: what Sparrow found in its folders. */
export function foundLine(
  node: NodeInfo,
  { onLook, onReview }: { onLook: () => void; onReview: () => void },
) {
  const found = node.found;
  if (!found) return null;
  const when = found.checked ? `Checked ${since(found.checked)}` : "Not looked yet";
  const path = (
    <span className="path found-path">
      {found.error ? found.error : found.added && found.unsure ? `Added ${found.added} this week` : when}
    </span>
  );
  // One round refresh button looks again. While looking, it turns and can't be pressed.
  const refresh = (found.looking || node.online) && (
    <button
      className="icon-btn found-refresh"
      aria-label={found.looking ? "Looking" : "Look again"}
      title={found.looking ? "Looking" : "Look again"}
      disabled={found.looking}
      onClick={onLook}
    >
      <RefreshCw size={15} strokeWidth={2.5} className={found.looking ? "spin" : undefined} />
    </button>
  );
  const end = (
    <span className="folder-state">
      {found.looking ? (
        <span className="sr-only" role="status">
          Looking…
        </span>
      ) : found.unsure ? (
        <>
          {found.unsure} need{found.unsure === 1 ? "s" : ""} a name
          <button className="btn fix update" onClick={onReview}>
            Review
          </button>
        </>
      ) : found.added ? (
        <>
          <Tick /> Added {found.added}
        </>
      ) : null}
      {refresh}
    </span>
  );
  return { path, end };
}

/** The files Sparrow wasn't sure about, to name and add, or set aside. */
export function FoundSheet({
  node,
  onClose,
  onChanged,
}: {
  node: NodeInfo;
  onClose: () => void;
  onChanged: () => void | Promise<void>;
}) {
  const files = useResource(() => api<Files>(`/admin/found/${node.id}`));
  const [named, setNamed] = useState<Record<string, Named>>({});
  const [matching, setMatching] = useState<Waiting | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const waiting = files.data?.unsure || [];
  const ready = waiting.filter((f) => named[f.path]);
  const key = (f: Waiting) => f.path;

  async function add() {
    setBusy(true);
    setError("");
    try {
      const result = await post<{
        added: { path: string }[];
        failed: { path: string; message: string }[];
      }>(`/admin/found/${node.id}/add`, {
        files: ready.map((f) => ({
          root_id: f.root_id,
          path: f.path,
          tmdb_id: named[key(f)].tmdb_id,
          media_type: named[key(f)].media_type,
          season: named[key(f)].media_type === "tv" ? named[key(f)].season : null,
          episode: named[key(f)].media_type === "tv" ? named[key(f)].episode : null,
        })),
      });
      const done = new Set(result.added.map((a) => a.path));
      setNamed(Object.fromEntries(Object.entries(named).filter(([path]) => !done.has(path))));
      if (result.failed.length) setError(result.failed.map((f) => f.message).join(" "));
      await Promise.all([files.refresh(), onChanged()]);
      if (!result.failed.length && done.size === waiting.length) onClose();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function setAside(file: Waiting) {
    setError("");
    try {
      await post(`/admin/found/${node.id}/ignore`, {
        files: [{ root_id: file.root_id, path: file.path }],
      });
      await Promise.all([files.refresh(), onChanged()]);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  return (
    <Dialog
      title={`Found on ${node.id === "local" ? "this server" : node.name}`}
      onClose={onClose}
      footer={
        <>
          {error && (
            <p className="sheet-problem problem-text" role="alert">
              {error}
            </p>
          )}
          <button className="btn primary" disabled={busy || !ready.length} onClick={() => void add()}>
            {busy
              ? "Adding…"
              : ready.length
                ? `Add ${plural(ready.length, "title")} to the collection`
                : "Name a file to add it"}
          </button>
        </>
      }
    >
      <div className="form">
        <ErrorNote error={files.error} />
        {files.loading && !files.data ? (
          <Loading label="Loading what Sparrow found" />
        ) : !waiting.length ? (
          <p className="muted">Nothing is waiting for a name.</p>
        ) : (
          <>
            <p className="muted">
              Sparrow wasn’t sure what {waiting.length === 1 ? "this is" : "these are"}. Files stay where
              they are.
            </p>
            <ul className="found-rows">
              {waiting.map((file) => {
                const title = named[key(file)];
                return (
                  <li key={key(file)}>
                    <div className="found-head">
                      <strong className="found-name">{file.name}</strong>
                      <span className="meta num">{bytes(file.size_bytes)}</span>
                    </div>
                    <p className="meta found-where">
                      {file.path.includes("/") ? file.path.slice(0, file.path.lastIndexOf("/")) : ""}
                      {file.path.includes("/") && file.reason ? " · " : ""}
                      {!title && file.reason}
                    </p>
                    {title ? (
                      <div className="found-named">
                        <span className="folder-state">
                          <Tick /> {title.title}
                          {title.year ? ` (${title.year})` : ""}
                        </span>
                        {title.media_type === "tv" && (
                          <div className="found-episode">
                            <Field label="Season">
                              <input
                                type="number"
                                min={0}
                                value={title.season ?? 1}
                                onChange={(e) =>
                                  setNamed({
                                    ...named,
                                    [key(file)]: { ...title, season: Number(e.target.value) },
                                  })
                                }
                              />
                            </Field>
                            <Field label="Episode">
                              <input
                                type="number"
                                min={1}
                                value={title.episode ?? 1}
                                onChange={(e) =>
                                  setNamed({
                                    ...named,
                                    [key(file)]: { ...title, episode: Number(e.target.value) },
                                  })
                                }
                              />
                            </Field>
                          </div>
                        )}
                        <button className="link" onClick={() => setMatching(file)}>
                          Change
                        </button>
                      </div>
                    ) : (
                      <div className="found-actions">
                        <button className="btn" onClick={() => setMatching(file)}>
                          Which title is this?
                        </button>
                        <button className="link" onClick={() => void setAside(file)}>
                          Not a title
                        </button>
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
          </>
        )}
        {matching && (
          <FindTitle
            query={matching.guess?.title || matching.name.replace(/\.[^.]+$/, "")}
            onClose={() => setMatching(null)}
            onSelect={(card) => {
              const guess = matching.guess;
              setNamed({
                ...named,
                [key(matching)]: {
                  tmdb_id: card.tmdb_id,
                  media_type: card.media_type,
                  title: card.title,
                  year: card.year,
                  season: guess?.season ?? 1,
                  episode: guess?.episode ?? 1,
                },
              });
              setMatching(null);
            }}
          />
        )}
      </div>
    </Dialog>
  );
}

type Card = { tmdb_id: number; media_type: "movie" | "tv"; title: string; year?: string };

/** Search for the film or series a file is. */
function FindTitle({
  query: initial,
  onSelect,
  onClose,
}: {
  query: string;
  onSelect: (card: Card) => void;
  onClose: () => void;
}) {
  const [query, setQuery] = useState(initial);
  const [results, setResults] = useState<Card[] | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function search() {
    setBusy(true);
    try {
      setResults(await api<Card[]>(`/suggest?q=${encodeURIComponent(query)}`));
      setError("");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Dialog title="Which title is this?" onClose={onClose}>
      <form
        className="form"
        onSubmit={(e) => {
          e.preventDefault();
          void search();
        }}
      >
        <Field label="Film or series">
          <input value={query} autoFocus onChange={(e) => setQuery(e.target.value)} />
        </Field>
        <button className="btn primary" disabled={busy || query.trim().length < 2}>
          {busy ? "Searching…" : "Search"}
        </button>
        <ErrorNote error={error} />
        {results && !results.length && <p className="muted">Nothing found. Try another name.</p>}
        {results?.map((card) => (
          <button
            type="button"
            className="match-row"
            key={`${card.media_type}-${card.tmdb_id}`}
            onClick={() => onSelect(card)}
          >
            <div>
              <h3>{card.title}</h3>
              <p>{[card.year, card.media_type === "tv" ? "Series" : "Film"].filter(Boolean).join(" · ")}</p>
            </div>
            <Check size={18} strokeWidth={2.5} />
          </button>
        ))}
      </form>
    </Dialog>
  );
}
