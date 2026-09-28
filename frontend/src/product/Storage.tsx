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
  kind,
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
      action={
        <button
          className="sp-btn sp-btn-solid"
          onClick={() => {
            setPairingId(undefined);
            setPairing(true);
            setCode("");
          }}
        >
          <Plus size={16} aria-hidden="true" />
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
              className="sp-btn sp-btn-ghost"
              onClick={() => {
                void nodes.refresh();
                void onChanged?.();
              }}
            >
              <RefreshCw size={15} aria-hidden="true" />
              Refresh
            </button>
          }
        >
          <div className="sp-drives">
            {nodes.data?.map((node) => {
              const root = node.capabilities.roots.find((r) => r.id === "library");
              const used =
                root?.free_bytes !== undefined && root.total_bytes
                  ? Math.round(100 - (100 * root.free_bytes) / root.total_bytes)
                  : undefined;
              const tone = node.disabled
                ? "is-quiet"
                : node.online
                  ? root?.available
                    ? "is-ok"
                    : "is-warn"
                  : "is-bad";
              return (
                <article className="sp-drive" key={node.id}>
                  <div className="sp-drive-head">
                    <HardDrive size={22} aria-hidden="true" />
                    <div>
                      <h3>{node.name}</h3>
                      <span className={`sp-status ${tone}`}>
                        {node.disabled
                          ? "Access revoked"
                          : node.online
                            ? root?.available
                              ? "Connected"
                              : "Connected · no library folder"
                            : "Offline"}
                      </span>
                    </div>
                  </div>
                  <div className="sp-drive-gauge">
                    {used !== undefined && (
                      <div
                        className="sp-gauge"
                        role="meter"
                        aria-label={`${node.name} space used`}
                        aria-valuemin={0}
                        aria-valuemax={100}
                        aria-valuenow={used}
                      >
                        <span style={{ width: `${used}%` }} />
                      </div>
                    )}
                    <p className="sp-label">
                      {root?.free_bytes !== undefined
                        ? `${bytes(root.free_bytes)} free${root.total_bytes ? ` of ${bytes(root.total_bytes)}` : ""}`
                        : root?.error || "No library folder yet"}
                      {!node.capabilities.probe && " · Media tools need attention"}
                    </p>
                  </div>
                  <div className="sp-actions">
                    {node.id === "local" && (
                      <button
                        className="sp-btn sp-btn-line sp-btn-small"
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
                        className="sp-btn sp-btn-solid sp-btn-small"
                        disabled={busy === node.id || !node.online || !root?.available}
                        onClick={() => preview(node)}
                      >
                        <FolderOpen size={15} aria-hidden="true" />
                        {busy === node.id ? "Scanning…" : "Import existing media"}
                      </button>
                    )}
                    {node.id !== "local" && (
                      <>
                        <button
                          className="sp-btn sp-btn-ghost sp-btn-small"
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
                            className="sp-btn sp-btn-ghost sp-btn-small"
                            onClick={async () => {
                              try {
                                await api(`/admin/nodes/${node.id}`, { method: "DELETE" });
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
                  </div>
                </article>
              );
            })}
          </div>
        </Section>
      )}
      <p className="sp-hint">Importing leaves your files where they are.</p>
      {pairing && (
        <Dialog title="Pair a storage node" onClose={() => setPairing(false)}>
          <div className="sp-form">
            <ErrorNote error={error} />
            {code ? (
              <>
                <p className="sp-hint">
                  Enter this address and code in Sparrow Node on the other
                  machine. The code expires in 10 minutes.
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
                <p className="sp-hint">
                  Then choose its library and staging folders and start the
                  node.
                </p>
                <button
                  className="sp-btn sp-btn-solid"
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
                  className="sp-btn sp-btn-solid"
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
            <p className="sp-hint">
              Paths on this server, as seen inside the container if you use
              Docker. The folders must already exist.
            </p>
            <Field label="Library folder" hint="Where your films and series are.">
              <input
                value={library}
                onChange={(e) => setLibrary(e.target.value)}
                placeholder="/media/library"
              />
            </Field>
            <Field label="Staging folder" hint="A separate folder for downloads.">
              <input
                value={staging}
                onChange={(e) => setStaging(e.target.value)}
                placeholder="/media/incoming"
              />
            </Field>
            <button
              className="sp-btn sp-btn-solid"
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
      title={done === null ? "Review your import" : "Import complete"}
      onClose={onClose}
    >
      <div className="sp-form">
        <ErrorNote error={error} />
        {done !== null ? (
          <>
            <p>
              {done} {done === 1 ? "file" : "files"} added. The originals
              weren’t moved.
            </p>
            <Link className="sp-btn sp-btn-solid" to="/library">
              Open your library
            </Link>
          </>
        ) : (
          <>
            <p className="sp-hint">
              {scan.candidates.length} video files found. Check the title of
              each file you select.
            </p>
            {scan.candidates.length === 0 && (
              <p className="sp-hint">
                No supported video files were found in this folder.
              </p>
            )}
            <div className="sp-actions">
              <button
                className="sp-btn sp-btn-line"
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
                className="sp-btn sp-btn-ghost"
                onClick={() => setSelected({})}
              >
                Clear
              </button>
            </div>
            <div className="sp-rows sp-import-list">
              {scan.candidates.map((candidate) => (
                <div className="sp-row" key={candidate.id}>
                  <div>
                    <label className="sp-check">
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
                      <div className="sp-form sp-import-match">
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
                            className="sp-btn sp-btn-line"
                            onClick={() => setMatching(candidate)}
                          >
                            Match title
                          </button>
                          <span className="sp-hint">
                            {selected[candidate.id].tmdb_id
                              ? "Matched"
                              : "Not matched"}
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
              className="sp-btn sp-btn-solid"
              disabled={busy || !Object.keys(selected).length}
              onClick={confirm}
            >
              {busy
                ? "Importing…"
                : `Import ${Object.keys(selected).length} files`}
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
    <Dialog title="Match title" onClose={onClose}>
      <div className="sp-form">
        <Field label="Title">
          <input value={query} onChange={(e) => setQuery(e.target.value)} />
        </Field>
        <button
          className="sp-btn sp-btn-solid"
          disabled={busy || query.trim().length < 2}
          onClick={search}
        >
          {busy ? "Searching…" : "Search titles"}
        </button>
        <ErrorNote error={error} />
        {results.map((card) => (
          <button
            className="sp-row sp-match-result"
            key={`${card.media_type}-${card.tmdb_id}`}
            onClick={() => onSelect(card)}
          >
            <div>
              <h3>{card.title}</h3>
              <p>
                {card.year} · {kind(card.media_type)}
              </p>
            </div>
            <Check size={16} />
          </button>
        ))}
      </div>
    </Dialog>
  );
}
