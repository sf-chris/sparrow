import { useState } from "react";
import { Link } from "react-router-dom";
import {
  HardDrive,
  Plus,
  RefreshCw,
  FolderOpen,
  Copy,
  Check,
} from "lucide-react";
import { api, patch, post, type NodeInfo } from "./api";
import {
  Dialog,
  Empty,
  ErrorNote,
  Field,
  Loading,
  Page,
  Section,
  bytes,
  useResource,
} from "./ui";

type Candidate = {
  id: string;
  path: string;
  suggested_title: string;
  media_type: "tv" | "movie";
  season: number | null;
  episode: number | null;
  size_bytes: number;
};
type Scan = { id: string; node_id: string; candidates: Candidate[] };
type Selection = {
  id: string;
  title: string;
  tmdb_id?: number;
  media_type: "movie" | "tv";
  season: number | null;
  episode: number | null;
};
export default function Storage({
  onboarding = false,
  onChanged,
}: {
  onboarding?: boolean;
  onChanged?: () => void | Promise<void>;
} = {}) {
  const nodes = useResource(() => api<NodeInfo[]>("/nodes"));
  const cfg = useResource(() =>
    api<{ library_dir: string; staging_dir: string }>("/admin/config"),
  );
  const [pairingId, setPairingId] = useState<string | undefined>();
  const [pairing, setPairing] = useState(false);
  const [name, setName] = useState("Windows storage");
  const [code, setCode] = useState("");
  const [local, setLocal] = useState(false);
  const [library, setLibrary] = useState("");
  const [staging, setStaging] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const [scan, setScan] = useState<Scan | null>(null);
  async function pair() {
    setBusy("pair");
    setError("");
    try {
      const result = await post<{ code: string }>("/admin/nodes/enroll", {
        name,
        node_id: pairingId,
      });
      setCode(result.code);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy("");
    }
  }
  async function saveLocal() {
    setBusy("local");
    setError("");
    try {
      await patch("/admin/config", {
        library_dir: library,
        staging_dir: staging,
      });
      setLocal(false);
      await Promise.all([nodes.refresh(), cfg.refresh()]);
      await onChanged?.();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy("");
    }
  }
  async function preview(node: NodeInfo) {
    setBusy(node.id);
    setError("");
    try {
      setScan(await post<Scan>("/admin/imports/preview", { node_id: node.id }));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy("");
    }
  }
  return (
    <Page
      embedded={onboarding}
      title="Storage & import"
      description="Keep files where you want them. Pair a storage node, or use folders on this server."
      action={
        <button
          className="sp-button primary"
          onClick={() => {
            setPairingId(undefined);
            setPairing(true);
            setCode("");
          }}
        >
          <Plus size={16} />
          Pair storage
        </button>
      }
    >
      <ErrorNote
        error={error || nodes.error || cfg.error}
        retry={() => {
          void nodes.refresh();
          void cfg.refresh();
        }}
      />
      {nodes.loading && !nodes.data ? (
        <Loading label="Checking storage…" />
      ) : (
        <Section
          title="Connected storage"
          action={
            <button
              className="sp-button quiet"
              onClick={() => {
                void nodes.refresh();
                void onChanged?.();
              }}
            >
              <RefreshCw size={15} />
              Refresh
            </button>
          }
        >
          <div className="sp-form">
            {nodes.data?.map((node) => {
              const root = node.capabilities.roots.find(
                (r) => r.id === "library",
              );
              return (
                <article className="sp-panel sp-storage-card" key={node.id}>
                  <div className="sp-row" style={{ paddingTop: 0 }}>
                    <div className="sp-node-head">
                      <HardDrive size={22} className="sp-node-icon" />
                      <div>
                        <h2>{node.name}</h2>
                        <p>
                          {node.disabled
                            ? "Access revoked"
                            : node.online
                              ? root?.available
                                ? "Connected · Library available"
                                : "Connected · Choose or reconnect a library folder"
                              : "Offline · Files and progress are preserved"}
                        </p>
                      </div>
                    </div>
                    <div className="sp-actions">
                      {node.id !== "local" && (
                        <>
                          <button
                            className="sp-button quiet"
                            onClick={() => {
                              setPairingId(node.id);
                              setName(node.name);
                              setCode("");
                              setPairing(true);
                            }}
                          >
                            Reconnect
                          </button>
                          {!node.disabled && (
                            <button
                              className="sp-button quiet"
                              onClick={async () => {
                                try {
                                  await api(`/admin/nodes/${node.id}`, {
                                    method: "DELETE",
                                  });
                                  await nodes.refresh();
                                } catch (e) {
                                  setError((e as Error).message);
                                }
                              }}
                            >
                              Revoke access
                            </button>
                          )}
                        </>
                      )}
                      {node.id === "local" && (
                        <button
                          className="sp-button secondary"
                          onClick={() => {
                            setLibrary(cfg.data?.library_dir || "");
                            setStaging(cfg.data?.staging_dir || "");
                            setLocal(true);
                          }}
                        >
                          Choose folders
                        </button>
                      )}
                      {!onboarding && (
                        <button
                          className="sp-button secondary"
                          disabled={
                            busy === node.id || !node.online || !root?.available
                          }
                          onClick={() => preview(node)}
                        >
                          <FolderOpen size={16} />
                          {busy === node.id
                            ? "Scanning…"
                            : "Import existing media"}
                        </button>
                      )}
                    </div>
                  </div>
                  <div className="sp-actions sp-muted">
                    <span>
                      {root?.free_bytes !== undefined
                        ? `${bytes(root.free_bytes)} free`
                        : root?.error || "No library folder configured"}
                    </span>
                    <span>·</span>
                    <span>
                      {node.capabilities.probe
                        ? "Media inspection ready"
                        : "Media tools need attention"}
                    </span>
                  </div>
                </article>
              );
            })}
          </div>
        </Section>
      )}
      <p className="sp-muted">
        Import previews your files and lets you correct title matches. Existing
        files stay in place.
      </p>
      {pairing && (
        <Dialog title="Pair a storage node" onClose={() => setPairing(false)}>
          <div className="sp-form">
            <ErrorNote error={error} />
            {code ? (
              <>
                <p className="sp-muted">
                  Open Sparrow Node on your storage machine. Enter this server
                  address and pairing code. The code expires in ten minutes.
                </p>
                <Field label="Server address">
                  <input
                    readOnly
                    value={location.origin}
                    onFocus={(e) => e.target.select()}
                  />
                </Field>
                <Field label="Pairing code">
                  <input
                    readOnly
                    value={code}
                    onFocus={(e) => e.target.select()}
                  />
                </Field>
                <p className="sp-muted">
                  Choose the library folder and a separate staging folder in the
                  node installer, then start the node. Its library will appear
                  here when it connects.
                </p>
                <button
                  className="sp-button primary"
                  onClick={() => {
                    setPairing(false);
                    void nodes.refresh();
                    void onChanged?.();
                  }}
                >
                  Check connection
                </button>
              </>
            ) : (
              <>
                <Field label="Storage name">
                  <input
                    value={name}
                    maxLength={100}
                    onChange={(e) => setName(e.target.value)}
                  />
                </Field>
                <button
                  className="sp-button primary"
                  disabled={busy === "pair"}
                  onClick={pair}
                >
                  {busy === "pair" ? "Creating code…" : "Generate pairing code"}
                </button>
              </>
            )}
          </div>
        </Dialog>
      )}
      {local && (
        <Dialog title="Folders on this server" onClose={() => setLocal(false)}>
          <div className="sp-form">
            <ErrorNote error={error} />
            <p className="sp-muted">
              These paths belong to the Linux server. To use Windows folders,
              pair a Windows storage node. With Docker, enter the mounted paths
              visible inside the container. Folders must already exist.
            </p>
            <Field
              label="Library folder"
              hint="Where existing movies and shows live."
            >
              <input
                value={library}
                onChange={(e) => setLibrary(e.target.value)}
                placeholder="/media/library"
              />
            </Field>
            <Field
              label="Staging folder"
              hint="A separate folder for incoming downloads."
            >
              <input
                value={staging}
                onChange={(e) => setStaging(e.target.value)}
                placeholder="/media/incoming"
              />
            </Field>
            <button
              className="sp-button primary"
              disabled={busy === "local"}
              onClick={saveLocal}
            >
              Save folders
            </button>
          </div>
        </Dialog>
      )}
      {scan && <ImportPreview scan={scan} onClose={() => setScan(null)} />}
    </Page>
  );
}
function ImportPreview({ scan, onClose }: { scan: Scan; onClose: () => void }) {
  const [selected, setSelected] = useState<Record<string, Selection>>({});
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<number | null>(null);
  const [matching, setMatching] = useState<Candidate | null>(null);
  const candidateSelection = (c: Candidate): Selection => ({
    id: c.id,
    title: c.suggested_title,
    media_type: c.media_type,
    season: c.season,
    episode: c.episode,
  });
  async function confirm() {
    setBusy(true);
    setError("");
    try {
      const result = await post<{
        imported: { id: string }[];
        failed: { id: string; message: string }[];
      }>(`/admin/imports/${scan.id}/confirm`, {
        selections: Object.values(selected),
      });
      if (result.failed.length) {
        setError(
          `${result.imported.length} files imported. ${result.failed.map((f) => f.message).join(" ")}`,
        );
        setSelected(
          Object.fromEntries(
            Object.entries(selected).filter(([id]) =>
              result.failed.some((f) => f.id === id),
            ),
          ),
        );
      } else setDone(result.imported.length);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Dialog
      title={done === null ? "Review your import" : "Added to your collection"}
      onClose={onClose}
    >
      <div className="sp-form">
        <ErrorNote error={error} />
        {done !== null ? (
          <>
            <p>
              {done} {done === 1 ? "file is" : "files are"} ready in your
              collection. The originals stayed in place.
            </p>
            <Link className="sp-button primary" to="/library">
              Open your library
            </Link>
          </>
        ) : (
          <>
            <p className="sp-muted">
              {scan.candidates.length} video files found. Confirm the title and
              episode for each selected file. Names are suggestions; Sparrow
              checks the actual media before importing.
            </p>
            {scan.candidates.length === 0 && (
              <p className="sp-muted">
                No supported video files were found in this folder.
              </p>
            )}
            <div className="sp-actions">
              <button
                className="sp-button secondary"
                disabled={busy}
                onClick={() =>
                  setSelected(
                    Object.fromEntries(
                      scan.candidates
                        .slice(0, 100)
                        .map((c) => [c.id, candidateSelection(c)]),
                    ),
                  )
                }
              >
                Select {Math.min(scan.candidates.length, 100)} files
              </button>
              <button
                className="sp-button quiet"
                onClick={() => setSelected({})}
              >
                Clear
              </button>
            </div>
            <div style={{ maxHeight: "44vh", overflowY: "auto" }}>
              {scan.candidates.map((candidate) => (
                <div className="sp-row" key={candidate.id}>
                  <div className="sp-grow">
                    <label className="sp-checkbox">
                      <input
                        type="checkbox"
                        checked={Boolean(selected[candidate.id])}
                        onChange={(e) =>
                          setSelected((previous) => {
                            const next = { ...previous };
                            if (e.target.checked)
                              next[candidate.id] =
                                candidateSelection(candidate);
                            else delete next[candidate.id];
                            return next;
                          })
                        }
                      />
                      <span className="sp-path">{candidate.path}</span>
                    </label>
                    {selected[candidate.id] && (
                      <div className="sp-form">
                        <Field label="Title">
                          <input
                            value={selected[candidate.id].title}
                            onChange={(e) =>
                              setSelected({
                                ...selected,
                                [candidate.id]: {
                                  ...selected[candidate.id],
                                  title: e.target.value,
                                  tmdb_id: undefined,
                                },
                              })
                            }
                          />
                        </Field>
                        <div className="sp-actions">
                          <button
                            className="sp-button secondary"
                            onClick={() => setMatching(candidate)}
                          >
                            Match a movie or show
                          </button>
                          <span className="sp-muted">
                            {selected[candidate.id].tmdb_id
                              ? "Matched to catalogue"
                              : "Using your title"}
                          </span>
                        </div>
                        {selected[candidate.id].media_type === "tv" && (
                          <div className="sp-form-grid">
                            <Field label="Season">
                              <input
                                type="number"
                                min={0}
                                value={selected[candidate.id].season ?? 1}
                                onChange={(e) =>
                                  setSelected({
                                    ...selected,
                                    [candidate.id]: {
                                      ...selected[candidate.id],
                                      season: Number(e.target.value),
                                    },
                                  })
                                }
                              />
                            </Field>
                            <Field label="Episode">
                              <input
                                type="number"
                                min={1}
                                value={selected[candidate.id].episode ?? 1}
                                onChange={(e) =>
                                  setSelected({
                                    ...selected,
                                    [candidate.id]: {
                                      ...selected[candidate.id],
                                      episode: Number(e.target.value),
                                    },
                                  })
                                }
                              />
                            </Field>
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                </div>
              ))}
            </div>
            <button
              className="sp-button primary"
              disabled={busy || !Object.keys(selected).length}
              onClick={confirm}
            >
              {busy
                ? "Inspecting and importing…"
                : `Import ${Object.keys(selected).length} selected files`}
            </button>
          </>
        )}
        {matching && (
          <MatchTitle
            query={matching.suggested_title}
            onClose={() => setMatching(null)}
            onSelect={(card) => {
              setSelected({
                ...selected,
                [matching.id]: {
                  ...selected[matching.id],
                  title: card.title,
                  tmdb_id: card.tmdb_id,
                  media_type: card.media_type,
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
function MatchTitle({
  query: initial,
  onSelect,
  onClose,
}: {
  query: string;
  onSelect: (card: any) => void;
  onClose: () => void;
}) {
  const [query, setQuery] = useState(initial);
  const [results, setResults] = useState<any[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function search() {
    setBusy(true);
    try {
      setResults(await api<any[]>(`/suggest?q=${encodeURIComponent(query)}`));
      setError("");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Dialog title="Find the correct title" onClose={onClose}>
      <div className="sp-form">
        <Field label="Movie or show title">
          <input value={query} onChange={(e) => setQuery(e.target.value)} />
        </Field>
        <button
          className="sp-button primary"
          disabled={busy || query.trim().length < 2}
          onClick={search}
        >
          {busy ? "Searching…" : "Search titles"}
        </button>
        <ErrorNote error={error} />
        {results.map((card) => (
          <button
            className="sp-row sp-choice"
            key={`${card.media_type}-${card.tmdb_id}`}
            onClick={() => onSelect(card)}
          >
            <div>
              <h3>{card.title}</h3>
              <p>
                {card.year} · {card.media_type === "tv" ? "TV show" : "Movie"}
              </p>
            </div>
            <Check size={16} />
          </button>
        ))}
      </div>
    </Dialog>
  );
}
