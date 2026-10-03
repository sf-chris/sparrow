import { useState } from "react";
import { Link } from "react-router-dom";
import {
  HardDrive,
  Plus,
  RefreshCw,
  FolderOpen,
  Check,
  Scissors,
} from "lucide-react";
import { api, patch, post, type NodeInfo } from "./api";
import { Tick } from "./Brand";
import {
  Bar,
  Dialog,
  ErrorNote,
  Field,
  Loading,
  Page,
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
  const suggested = useResource(() =>
    api<{ library: string; incoming: string }>("/admin/storage/suggestions"),
  );
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
  /** Saved folders this server can see, else the suggested ones. */
  function planFolders(node: NodeInfo) {
    const usable = (id: string) =>
      node.capabilities.roots.find((r) => r.id === id)?.available;
    return {
      library:
        (usable("library") && cfg.data?.library_dir) ||
        suggested.data?.library ||
        "",
      staging:
        (usable("staging") && cfg.data?.staging_dir) ||
        suggested.data?.incoming ||
        "",
    };
  }
  function chooseLocal(node: NodeInfo) {
    const plan = planFolders(node);
    setError("");
    setLibrary(plan.library);
    setStaging(plan.staging);
    setLocal(true);
  }
  async function useSuggested(node: NodeInfo) {
    const plan = planFolders(node);
    await saveFolders(plan.library, plan.staging, "suggested");
  }
  /** Make missing folders again where they were. */
  async function recreate() {
    await saveFolders(
      cfg.data?.library_dir || "",
      cfg.data?.staging_dir || "",
      "recreate",
    );
  }
  function startPairing(node?: NodeInfo) {
    setPairingId(node?.id);
    setName(node?.name || "Windows storage");
    setCode("");
    setError("");
    setPairing(true);
  }
  async function saveFolders(library: string, staging: string, key: string) {
    setBusy(key);
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
      title={onboarding ? "" : "Storage"}
      action={
        !onboarding && (
          <button className="btn primary" onClick={() => startPairing()}>
            <Plus size={18} strokeWidth={2.5} />
            Pair a computer
          </button>
        )
      }
    >
      <ErrorNote
        error={(!pairing && !local && error) || nodes.error || cfg.error}
        retry={() => {
          void nodes.refresh();
          void cfg.refresh();
        }}
      />
      {nodes.loading && !nodes.data ? (
        <Loading label="Loading storage" />
      ) : (
        <section aria-labelledby={onboarding ? undefined : "devices"}>
          {!onboarding && (
            <Bar id="devices" title="Devices">
              <button
                className="bar-button"
                onClick={() => {
                  void nodes.refresh();
                  void onChanged?.();
                }}
              >
                <RefreshCw size={14} strokeWidth={2.5} />
                Refresh
              </button>
            </Bar>
          )}
          <ul className={`devices ${onboarding ? "plain" : ""}`}>
            {nodes.data?.map((node) => {
              const rootOf = (id: string) =>
                node.capabilities.roots.find((r) => r.id === id);
              const root = rootOf("library");
              const local = node.id === "local";
              // Red is for something that broke; a folder not chosen yet is just the next step.
              const problem = node.disabled
                ? "Revoked"
                : !node.online
                  ? "Offline"
                  : "";
              const folders = [
                {
                  label: "Library",
                  path: cfg.data?.library_dir || "",
                  root,
                  suggestion: suggested.data?.library || "",
                  write: false,
                },
                {
                  label: "Incoming",
                  path: cfg.data?.staging_dir || "",
                  root: rootOf("staging"),
                  suggestion: suggested.data?.incoming || "",
                  write: true,
                },
              ];
              const missing = folders.some((f) => f.path && !f.root?.available);
              const unset = folders.some((f) => !f.path);
              return (
                <li className="device-card" key={node.id}>
                  <HardDrive size={26} strokeWidth={2} aria-hidden="true" />
                  <div className="device-body">
                    <div className="device-head">
                      <h2>{node.name}</h2>
                      {problem && (
                        <span className="flag problem">{problem}</span>
                      )}
                      {root?.free_bytes !== undefined && (
                        <span className="meta num device-free">
                          {bytes(root.free_bytes)} free
                        </span>
                      )}
                    </div>
                    {local ? (
                      <ul className="folder-rows">
                        {folders.map((folder) => {
                          const shown = folder.path || folder.suggestion;
                          const ready =
                            folder.path &&
                            folder.root?.available &&
                            !(folder.write && folder.root.writable === false);
                          return (
                            <li key={folder.label}>
                              <span className="folder-label">
                                {folder.label}
                              </span>
                              <span
                                className={`path ${folder.path ? "" : "suggested"}`}
                              >
                                {shown || "Not chosen"}
                              </span>
                              <span className="leader" aria-hidden="true" />
                              {ready ? (
                                <span className="folder-state">
                                  <Tick />{" "}
                                  {folder.root!.writable === false
                                    ? "Read only"
                                    : "Ready"}
                                </span>
                              ) : !folder.path ? (
                                <span className="folder-state quiet">
                                  {shown ? "Suggested" : ""}
                                </span>
                              ) : folder.root?.available ? (
                                <span className="folder-state problem-text">
                                  Can’t save here
                                </span>
                              ) : (
                                <span className="folder-state problem-text">
                                  Not found
                                </span>
                              )}
                            </li>
                          );
                        })}
                      </ul>
                    ) : (
                      <p className="meta num">
                        {root?.available
                          ? ""
                          : root?.error || "No library folder yet"}
                        {!node.capabilities.probe && " · Media tools missing"}
                      </p>
                    )}
                    <div className="actions">
                      {local ? (
                        unset && !missing && suggested.data ? (
                          <>
                            <button
                              className="btn primary"
                              disabled={!!busy}
                              onClick={() => void useSuggested(node)}
                            >
                              {busy === "suggested"
                                ? "Creating folders…"
                                : "Use these folders"}
                            </button>
                            <button
                              className="btn quiet"
                              onClick={() => chooseLocal(node)}
                            >
                              Choose other folders
                            </button>
                          </>
                        ) : missing && !unset ? (
                          <>
                            <button
                              className="btn primary"
                              disabled={!!busy}
                              onClick={() => void recreate()}
                            >
                              {busy === "recreate"
                                ? "Creating folders…"
                                : "Create folders"}
                            </button>
                            <button
                              className="btn quiet"
                              onClick={() => chooseLocal(node)}
                            >
                              Choose other folders
                            </button>
                          </>
                        ) : (
                          <button
                            className={`btn ${unset ? "primary" : "quiet"}`}
                            onClick={() => chooseLocal(node)}
                          >
                            {unset ? "Choose folders" : "Change folders"}
                          </button>
                        )
                      ) : (
                        <>
                          <button
                            className="btn"
                            onClick={() => startPairing(node)}
                          >
                            Reconnect
                          </button>
                          {!node.disabled && (
                            <button
                              className="btn quiet"
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
                      {!onboarding && (
                        <button
                          className="btn"
                          disabled={
                            busy === node.id || !node.online || !root?.available
                          }
                          onClick={() => preview(node)}
                        >
                          <FolderOpen size={17} strokeWidth={2.25} />
                          {busy === node.id ? "Scanning…" : "Import files"}
                        </button>
                      )}
                    </div>
                  </div>
                </li>
              );
            })}
          </ul>
        </section>
      )}
      {onboarding ? (
        <p className="muted storage-pair">
          Files on another computer?{" "}
          <button className="link" onClick={() => startPairing()}>
            Pair it
          </button>
        </p>
      ) : (
        <p className="muted storage-note">
          Import shows what it found before adding anything. Files aren’t
          moved.
        </p>
      )}
      {pairing && (
        <Dialog title="Pair a computer" onClose={() => setPairing(false)}>
          <div className="form">
            <ErrorNote error={error} />
            {code ? (
              <>
                <div className="coupon ticket">
                  <Scissors
                    className="coupon-cut"
                    size={18}
                    strokeWidth={2}
                    aria-hidden="true"
                  />
                  <Field label="Server address">
                    <input
                      readOnly
                      value={location.origin}
                      onFocus={(e) => e.target.select()}
                    />
                  </Field>
                  <Field label="Pairing code" hint="Expires in 10 minutes.">
                    <input
                      className="big-code"
                      readOnly
                      value={code}
                      onFocus={(e) => e.target.select()}
                    />
                  </Field>
                </div>
                <p className="muted">
                  On the other computer, open Sparrow Node and enter the address
                  and code. Then choose a library folder and a separate incoming
                  folder.
                </p>
                <button
                  className="btn primary"
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
                <Field label="Name">
                  <input
                    value={name}
                    maxLength={100}
                    onChange={(e) => setName(e.target.value)}
                  />
                </Field>
                <button
                  className="btn primary"
                  disabled={busy === "pair"}
                  onClick={pair}
                >
                  {busy === "pair" ? "Creating code…" : "Get pairing code"}
                </button>
              </>
            )}
          </div>
        </Dialog>
      )}
      {local && (
        <Dialog
          title="Folders on this server"
          onClose={() => setLocal(false)}
          footer={
            <button
              className="btn primary"
              disabled={busy === "local"}
              onClick={() => void saveFolders(library, staging, "local")}
            >
              Save folders
            </button>
          }
        >
          <div className="form">
            <ErrorNote error={error} />
            <p className="muted">
              Folders on this server. Sparrow creates any that don’t exist yet.
              For another computer, pair it instead.
            </p>
            <Field
              label="Library folder"
              hint="Where your films and series are."
            >
              <input
                value={library}
                onChange={(e) => setLibrary(e.target.value)}
                placeholder="/media/library"
              />
            </Field>
            <Field
              label="Incoming folder"
              hint="A separate folder for downloads in progress."
            >
              <input
                value={staging}
                onChange={(e) => setStaging(e.target.value)}
                placeholder="/media/incoming"
              />
            </Field>
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
    <Dialog title={done === null ? "Import" : "Imported"} onClose={onClose}>
      <div className="form">
        <ErrorNote error={error} />
        {done !== null ? (
          <>
            <p>
              {done} {done === 1 ? "file" : "files"} added to your collection.
            </p>
            <Link className="btn primary" to="/">
              Open the guide
            </Link>
          </>
        ) : (
          <>
            <p className="muted">
              {scan.candidates.length === 0
                ? "No video files found."
                : `${scan.candidates.length} video ${scan.candidates.length === 1 ? "file" : "files"} found. Titles are guessed from file names, so check each one you select.`}
            </p>
            <div className="actions">
              <button
                className="btn"
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
              <button className="btn quiet" onClick={() => setSelected({})}>
                Clear
              </button>
            </div>
            <div className="import-list">
              {scan.candidates.map((candidate) => (
                <div className="import-row" key={candidate.id}>
                  <div>
                    <label className="check">
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
                      <span className="path">{candidate.path}</span>
                    </label>
                    {selected[candidate.id] && (
                      <div className="form import-match">
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
                        <div className="actions">
                          <button
                            className="btn"
                            onClick={() => setMatching(candidate)}
                          >
                            Find title
                          </button>
                          <span className="muted">
                            {selected[candidate.id].tmdb_id
                              ? "Matched"
                              : "Not matched"}
                          </span>
                        </div>
                        {selected[candidate.id].media_type === "tv" && (
                          <div className="form-grid">
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
              className="btn primary"
              disabled={busy || !Object.keys(selected).length}
              onClick={confirm}
            >
              {busy
                ? "Checking and importing…"
                : `Import ${Object.keys(selected).length} ${Object.keys(selected).length === 1 ? "file" : "files"}`}
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
    <Dialog title="Find title" onClose={onClose}>
      <div className="form">
        <Field label="Film or series">
          <input value={query} onChange={(e) => setQuery(e.target.value)} />
        </Field>
        <button
          className="btn primary"
          disabled={busy || query.trim().length < 2}
          onClick={search}
        >
          {busy ? "Searching…" : "Search"}
        </button>
        <ErrorNote error={error} />
        {results.map((card) => (
          <button
            className="match-row"
            key={`${card.media_type}-${card.tmdb_id}`}
            onClick={() => onSelect(card)}
          >
            <div>
              <h3>{card.title}</h3>
              <p>
                {[card.year, card.media_type === "tv" ? "Series" : "Film"]
                  .filter(Boolean)
                  .join(" · ")}
              </p>
            </div>
            <Check size={18} strokeWidth={2.5} />
          </button>
        ))}
      </div>
    </Dialog>
  );
}
