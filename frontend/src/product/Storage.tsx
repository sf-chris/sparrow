import { useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { HardDrive, Plus, RefreshCw, FolderOpen, Check } from "lucide-react";
import {
  api,
  patch,
  post,
  type JobRow,
  type NodeInfo,
  type Release,
  type Update as UpdateInfo,
} from "./api";
import { ago } from "./Collection";
import JobsDialog, { folderSlots } from "./ComputerJobs";
import { Tick } from "./Brand";
import FolderSheet from "./FolderBrowser";
import PairComputer from "./PairComputer";
import Placement from "./Placement";
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
type Root = NodeInfo["capabilities"]["roots"][number];
type Disk = { names: string[]; total: number; free: number; sparrow: number };

/** One entry per physical disk: library folders on the same disk count once. */
function disksOf(roots: Root[]): Disk[] {
  const out: (Disk & { key: string })[] = [];
  roots.forEach((r) => {
    if (!r.available || !r.total_bytes) return;
    const name = r.id === "library" ? "Library" : `Library ${r.id.split("-")[1]}`;
    const free = r.free_bytes || 0;
    const same = out.find((d) =>
      r.disk
        ? d.key === r.disk
        : d.total === r.total_bytes && Math.abs(d.free - free) < 1e9,
    );
    if (same) {
      same.names.push(name);
      same.sparrow += r.library_bytes || 0;
    } else
      out.push({
        key: r.disk || r.id,
        names: [name],
        total: r.total_bytes,
        free,
        sparrow: r.library_bytes || 0,
      });
  });
  return out;
}

/** Sizes as people say them: 850 GB, 61 GB, 1.8 TB. */
const size = (n: number) =>
  n >= 1e12
    ? `${(n / 1e12).toFixed(1)} TB`
    : n >= 1e10
      ? `${Math.round(n / 1e9)} GB`
      : bytes(n);

const nearlyFull = (d: Disk) => d.free < Math.max(d.total * 0.05, 10e9);

/** A disk as a printed bar: Sparrow's films and shows, everything else, free. */
function DiskMeter({ disk, named }: { disk: Disk; named: boolean }) {
  const sparrow = Math.min(disk.sparrow, disk.total - disk.free);
  const other = Math.max(0, disk.total - disk.free - sparrow);
  const share = (n: number) => `${(100 * n) / disk.total}%`;
  const full = nearlyFull(disk);
  return (
    <div className="disk-meter">
      {named && <span className="disk-name">{disk.names.join(" · ")}</span>}
      <div
        className="disk-bar"
        role="img"
        aria-label={`${size(sparrow)} Sparrow, ${size(other)} other files, ${size(disk.free)} free of ${size(disk.total)}`}
      >
        {sparrow > 0 && (
          <span className="sparrow" style={{ width: share(sparrow) }} />
        )}
        {other > 0 && <span className="other" style={{ width: share(other) }} />}
      </div>
      <dl className="disk-legend num" aria-hidden="true">
        <div>
          <dt>
            <i className="swatch sparrow" />
            Sparrow
          </dt>
          <dd>{size(sparrow)}</dd>
        </div>
        <div>
          <dt>
            <i className="swatch other" />
            Other files
          </dt>
          <dd>{size(other)}</dd>
        </div>
        <div className={full ? "problem-text" : ""}>
          <dt>
            <i className="swatch free" />
            Free
          </dt>
          <dd>
            {size(disk.free)} <small>of {size(disk.total)}</small>
          </dd>
        </div>
      </dl>
    </div>
  );
}
type Selection = {
  id: string;
  title: string;
  tmdb_id?: number;
  media_type: "movie" | "tv";
  season: number | null;
  episode: number | null;
};
type Config = {
  library_dir: string;
  staging_dir: string;
  extra_library_dirs: string[];
};
type Fix = NonNullable<JobRow["fix"]>;
/** One line of a computer's card: a job or folder, where, and how it's doing. */
type Line = {
  key: string;
  label: string;
  path?: string;
  suggested?: boolean;
  end: ReactNode;
  action?: ReactNode;
};

/** How a job is doing, at the end of its dotted line. */
function JobState({
  row,
  offline,
  onFix,
}: {
  row?: JobRow;
  offline?: boolean;
  onFix: (fix: Fix) => void;
}) {
  if (!row) return null;
  if (row.state === "ready")
    return (
      <span className="folder-state">
        <Tick /> Ready
      </span>
    );
  if (row.state === "off")
    return <span className="folder-state quiet">Off</span>;
  if (row.state === "waiting")
    return (
      <span className={`folder-state ${offline ? "quiet" : "working"}`}>
        {row.line}
      </span>
    );
  return (
    <span className="folder-state problem-text">
      {row.line}
      {row.fix === "download_app" ? (
        <Link className="btn fix" to="/settings/server">
          Fix
        </Link>
      ) : row.fix ? (
        <button className="btn fix" onClick={() => onFix(row.fix!)}>
          Fix
        </button>
      ) : null}
    </span>
  );
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** A release as people recognise it: its date, then its commit. */
export function releaseLabel(release?: Release) {
  if (!release) return "";
  const when = release.date ? new Date(release.date) : null;
  const date =
    when && !Number.isNaN(when.getTime())
      ? `${when.getDate()} ${MONTHS[when.getMonth()]}${when.getFullYear() !== new Date().getFullYear() ? ` ${when.getFullYear()}` : ""}`
      : "";
  return [date, release.commit].filter(Boolean).join(" · ") || "Unknown";
}

const STEPS: Record<NonNullable<UpdateInfo["step"]>, string> = {
  downloading: "Downloading…",
  checking: "Checking…",
  waiting: "Waiting for work to finish…",
  restarting: "Restarting…",
};

/** Where a computer is on updates, at the end of its Version line. */
function UpdateState({
  update,
  offline,
  busy,
  onUpdate,
  onFix,
}: {
  update: UpdateInfo;
  offline?: boolean;
  busy: boolean;
  onUpdate: () => void;
  onFix: () => void;
}) {
  if (update.state === "current")
    return (
      <span className="folder-state">
        <Tick /> Up to date
      </span>
    );
  if (offline)
    return (
      <span className="folder-state quiet">
        Latest is {releaseLabel(update.latest)}
      </span>
    );
  if (update.state === "starting" || update.state === "updating")
    return (
      <span className="folder-state working" role="status">
        {update.step ? STEPS[update.step] : "Updating…"}
      </span>
    );
  if (update.state === "available")
    return (
      <span className="folder-state">
        Latest is {releaseLabel(update.latest)}
        <button className="btn fix update" disabled={busy} onClick={onUpdate}>
          {busy ? "Updating…" : "Update"}
        </button>
      </span>
    );
  if (update.state === "failed")
    return (
      <span className="folder-state problem-text" title={update.error || undefined}>
        Update didn’t work
        <button className="btn fix" disabled={busy} onClick={onUpdate}>
          Try again
        </button>
      </span>
    );
  return (
    <span className="folder-state problem-text">
      Set up again to update
      <button className="btn fix" onClick={onFix}>
        Fix
      </button>
    </span>
  );
}

/** A folder's own readiness, while it is still being set up. */
function FolderState({
  path,
  root,
  write,
  suggestion,
}: {
  path: string;
  root?: Root;
  write: boolean;
  suggestion: string;
}) {
  if (!path)
    return (
      <span className="folder-state quiet">{suggestion ? "Suggested" : ""}</span>
    );
  if (root?.available && !(write && root.writable === false))
    return (
      <span className="folder-state">
        <Tick /> {root.writable === false ? "Read only" : "Ready"}
      </span>
    );
  return (
    <span className="folder-state problem-text">
      {root?.available ? "Can’t save here" : "Not found"}
    </span>
  );
}

export default function Storage({
  onboarding = false,
  onChanged,
}: {
  onboarding?: boolean;
  onChanged?: () => void | Promise<void>;
} = {}) {
  const nodes = useResource(() => api<NodeInfo[]>("/nodes"));
  const cfg = useResource(() => api<Config>("/admin/config"));
  const [pairing, setPairing] = useState<{ node?: NodeInfo; fix?: boolean } | null>(null);
  const [jobsFor, setJobsFor] = useState<NodeInfo | null>(null);
  const [sheet, setSheet] = useState<
    { kind: "local" | "extra" } | { kind: "node"; node: NodeInfo } | null
  >(null);
  const [highlight, setHighlight] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const [scan, setScan] = useState<Scan | null>(null);
  const suggested = useResource(() =>
    api<{ library: string; incoming: string }>("/admin/storage/suggestions"),
  );
  useEffect(() => {
    if (!highlight || !nodes.data) return;
    document
      .getElementById(`device-${highlight}`)
      ?.scrollIntoView({ block: "center", behavior: "smooth" });
    const timer = window.setTimeout(() => setHighlight(""), 4000);
    return () => window.clearTimeout(timer);
  }, [highlight, nodes.data]);
  // A computer setting itself up or updating reports back every few seconds.
  const settling = Boolean(
    nodes.data?.some(
      (n) =>
        n.online &&
        (n.health?.rows.some((r) => r.state === "waiting") ||
          ["starting", "updating"].includes(n.health?.update?.state || "")),
    ),
  );
  useEffect(() => {
    if (!settling) return;
    const timer = window.setInterval(() => void nodes.refresh(), 5000);
    return () => window.clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [settling]);
  async function paired(id: string) {
    setPairing(null);
    await Promise.all([nodes.refresh(), onChanged?.()]);
    setHighlight(id);
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
  function chooseLocal() {
    setError("");
    setSheet({ kind: "local" });
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
  function startPairing(node?: NodeInfo, fix = false) {
    setError("");
    setPairing({ node, fix });
  }
  /** Save this server's folders; a sheet shows its own errors, so it rethrows. */
  async function saveFolders(
    library: string,
    staging: string,
    key: string,
    rethrow = false,
  ) {
    setBusy(key);
    setError("");
    try {
      await patch("/admin/config", {
        library_dir: library,
        staging_dir: staging,
      });
      setSheet(null);
      await Promise.all([nodes.refresh(), cfg.refresh()]);
      await onChanged?.();
    } catch (e) {
      if (rethrow) throw e;
      setError((e as Error).message);
    } finally {
      setBusy("");
    }
  }
  async function saveExtra(folders: string[], rethrow = false) {
    setBusy("extra");
    setError("");
    try {
      await patch("/admin/config", { extra_library_dirs: folders });
      setSheet(null);
      await Promise.all([nodes.refresh(), cfg.refresh()]);
      await onChanged?.();
    } catch (e) {
      if (rethrow) throw e;
      setError((e as Error).message);
    } finally {
      setBusy("");
    }
  }
  async function saveNodeFolders(node: NodeInfo, library: string, staging: string) {
    await post(`/admin/nodes/${node.id}/folders`, {
      libraries: library ? [library] : [],
      staging,
    });
    setSheet(null);
    await Promise.all([nodes.refresh(), onChanged?.()]);
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
  async function updateNow(node: NodeInfo) {
    setBusy(`update-${node.id}`);
    setError("");
    try {
      await post(`/admin/nodes/${node.id}/update`);
      await nodes.refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy("");
    }
  }
  function fix(node: NodeInfo, what: Fix) {
    setError("");
    if (what === "folders")
      setSheet(node.id === "local" ? { kind: "local" } : { kind: "node", node });
    else if (what === "repair") startPairing(node, true);
  }
  return (
    <Page
      embedded={onboarding}
      title={onboarding ? "" : "Computers"}
      action={
        !onboarding && (
          <button className="btn primary" onClick={() => startPairing()}>
            <Plus size={18} strokeWidth={2.5} />
            Add a computer
          </button>
        )
      }
    >
      <ErrorNote
        error={(!pairing && !sheet && !jobsFor && error) || nodes.error || cfg.error}
        retry={() => {
          void nodes.refresh();
          void cfg.refresh();
        }}
      />
      {nodes.loading && !nodes.data ? (
        <Loading label="Loading computers" />
      ) : (
        <section aria-labelledby={onboarding ? undefined : "devices"}>
          {!onboarding && (
            <Bar id="devices" title="All computers">
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
              const local = node.id === "local";
              const health = node.health;
              const rowOf = (job: JobRow["job"]) =>
                health?.rows.find((r) => r.job === job);
              const offline = health?.state === "offline";
              const disks = node.capabilities.roots.filter((r) =>
                /^library(-\d+)?$/.test(r.id),
              );
              const meters = disksOf(disks);
              // Red is for something that broke; a folder not chosen yet is just the next step.
              const problem = node.disabled
                ? "Revoked"
                : !node.online
                  ? "Offline"
                  : meters.length && meters.every(nearlyFull)
                    ? "Nearly full"
                    : "";
              const state = (job: JobRow["job"]) => (
                <JobState
                  row={rowOf(job)}
                  offline={offline}
                  onFix={(what) => fix(node, what)}
                />
              );
              const library = local
                ? cfg.data?.library_dir || ""
                : rootOf("library")?.label || "";
              const incoming = local
                ? cfg.data?.staging_dir || ""
                : rootOf("staging")?.label || "";
              // While this server's folders are being chosen, rows show the folders themselves.
              const choosing =
                local &&
                (onboarding ||
                  !library ||
                  !incoming ||
                  !rootOf("library")?.available ||
                  !rootOf("staging")?.available);
              const extras = local
                ? (cfg.data?.extra_library_dirs || []).map((path, n) => ({
                    path,
                    root: rootOf(`library-${n + 2}`),
                    label: `Library ${n + 2}`,
                    extra: n as number | undefined,
                  }))
                : disks.slice(1).map((root, n) => ({
                    path: root.label || "",
                    root,
                    label: `Library ${n + 2}`,
                    extra: undefined as number | undefined,
                  }));
              const lines: Line[] = [
                {
                  key: "storage",
                  label: "Storage",
                  path: library || (local ? suggested.data?.library || "" : ""),
                  suggested: !library,
                  end: choosing ? (
                    <FolderState
                      path={library}
                      root={rootOf("library")}
                      write={false}
                      suggestion={suggested.data?.library || ""}
                    />
                  ) : (
                    state("storage")
                  ),
                },
                ...extras.map((extra) => ({
                  key: extra.label,
                  label: extra.label,
                  path: extra.path,
                  end: (
                    <FolderState
                      path={extra.path}
                      root={extra.root}
                      write={false}
                      suggestion=""
                    />
                  ),
                  action:
                    extra.extra !== undefined ? (
                      <button
                        className="link"
                        disabled={!!busy}
                        onClick={() =>
                          void saveExtra(
                            (cfg.data?.extra_library_dirs || []).filter(
                              (_, n) => n !== extra.extra,
                            ),
                          )
                        }
                      >
                        Stop using
                      </button>
                    ) : undefined,
                })),
              ];
              if (!onboarding && rowOf("playback"))
                lines.push({ key: "playback", label: "Playback", end: state("playback") });
              lines.push({
                key: "downloads",
                label: "Downloads",
                path: incoming || (local ? suggested.data?.incoming || "" : ""),
                suggested: !incoming,
                end: choosing ? (
                  <FolderState
                    path={incoming}
                    root={rootOf("staging")}
                    write
                    suggestion={suggested.data?.incoming || ""}
                  />
                ) : (
                  state("downloads")
                ),
              });
              const subtitles = rowOf("subtitles");
              if (!onboarding && subtitles)
                lines.push({
                  key: "subtitles",
                  label: "Subtitles",
                  path: subtitles.state === "ready" ? subtitles.line : "",
                  end: state("subtitles"),
                });
              const update = health?.update;
              if (!onboarding && update)
                lines.push({
                  key: "version",
                  label: "Version",
                  path: releaseLabel(update.running),
                  end: (
                    <UpdateState
                      update={update}
                      offline={offline}
                      busy={busy === `update-${node.id}`}
                      onUpdate={() => void updateNow(node)}
                      onFix={() => startPairing(node, true)}
                    />
                  ),
                });
              const missing =
                local &&
                ((library && !rootOf("library")?.available) ||
                  (incoming && !rootOf("staging")?.available));
              const unset = local && (!library || !incoming);
              const seen = ago(node.last_seen);
              const keeps = Boolean(health?.jobs.storage);
              const downloads = Boolean(health?.jobs.downloads);
              const needsFolders = (keeps || downloads) && !disks.length;
              return (
                <li
                  className={`device-card ${highlight === node.id ? "just-paired" : ""}`}
                  key={node.id}
                  id={`device-${node.id}`}
                >
                  <HardDrive size={26} strokeWidth={2} aria-hidden="true" />
                  <div className="device-body">
                    <div className="device-head">
                      <h2>{node.name}</h2>
                      {problem && <span className="flag problem">{problem}</span>}
                      {health?.runs_sparrow && !onboarding && (
                        <span className="meta device-role">Runs Sparrow</span>
                      )}
                      {offline && node.last_seen > 0 && (
                        <span className="meta num">
                          Last seen {seen === "now" ? "just now" : `${seen} ago`}
                        </span>
                      )}
                    </div>
                    {meters.length > 0 && (
                      <div className="disk-meters">
                        {meters.map((disk) => (
                          <DiskMeter
                            key={disk.names.join()}
                            disk={disk}
                            named={meters.length > 1}
                          />
                        ))}
                      </div>
                    )}
                    <ul className="folder-rows job-rows">
                      {lines.map((line) => (
                        <li key={line.key}>
                          <span className="folder-label">{line.label}</span>
                          {line.path ? (
                            <span className={`path ${line.suggested ? "suggested" : ""}`}>
                              {line.path}
                            </span>
                          ) : null}
                          <span className="leader" aria-hidden="true" />
                          {line.action}
                          {line.end}
                        </li>
                      ))}
                    </ul>
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
                            <button className="btn quiet" onClick={chooseLocal}>
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
                              {busy === "recreate" ? "Creating folders…" : "Create folders"}
                            </button>
                            <button className="btn quiet" onClick={chooseLocal}>
                              Choose other folders
                            </button>
                          </>
                        ) : (
                          <>
                            {!onboarding && (
                              <button className="btn quiet" onClick={() => setJobsFor(node)}>
                                Change jobs
                              </button>
                            )}
                            <button
                              className={`btn ${unset ? "primary" : "quiet"}`}
                              onClick={chooseLocal}
                            >
                              {unset ? "Choose folders" : "Change folders"}
                            </button>
                            {!unset && (
                              <button
                                className="btn quiet"
                                onClick={() => {
                                  setError("");
                                  setSheet({ kind: "extra" });
                                }}
                              >
                                <Plus size={16} strokeWidth={2.5} />
                                Library folder
                              </button>
                            )}
                          </>
                        )
                      ) : (
                        <>
                          {!node.disabled && !onboarding && (
                            <button className="btn quiet" onClick={() => setJobsFor(node)}>
                              Change jobs
                            </button>
                          )}
                          {node.capabilities.folders &&
                            node.online &&
                            !node.disabled &&
                            (keeps || downloads || disks.length > 0 || incoming) && (
                              <button
                                className={`btn ${needsFolders ? "primary" : "quiet"}`}
                                onClick={() => {
                                  setError("");
                                  setSheet({ kind: "node", node });
                                }}
                              >
                                {disks.length || incoming ? "Change folders" : "Choose folders"}
                              </button>
                            )}
                          <button
                            className="btn quiet"
                            onClick={() => startPairing(node, !node.disabled)}
                          >
                            {node.disabled ? "Reconnect" : "Set up again"}
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
                      {!onboarding && (local || disks.length > 0) && (
                        <button
                          className="btn"
                          disabled={
                            busy === node.id ||
                            !node.online ||
                            !rootOf("library")?.available
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
            Add it
          </button>
        </p>
      ) : (
        <p className="muted storage-note">
          Import shows what it found before adding anything. Files aren’t
          moved.
        </p>
      )}
      {!onboarding && <Placement />}
      {pairing && (
        <PairComputer
          node={pairing.node}
          fix={pairing.fix}
          all={nodes.data || []}
          onClose={() => {
            setPairing(null);
            void nodes.refresh();
            void onChanged?.();
          }}
          onPaired={(id) => void paired(id)}
        />
      )}
      {jobsFor && (
        <JobsDialog
          node={jobsFor}
          all={nodes.data || []}
          onClose={() => setJobsFor(null)}
          onSaved={async () => {
            setJobsFor(null);
            await nodes.refresh();
          }}
        />
      )}
      {sheet?.kind === "local" && (
        <FolderSheet
          title="Folders on this server"
          nodeId="local"
          slots={(() => {
            const here = nodes.data?.find((n) => n.id === "local");
            const plan = here
              ? planFolders(here)
              : { library: cfg.data?.library_dir || "", staging: cfg.data?.staging_dir || "" };
            return [
              { key: "library", label: "Library", value: plan.library },
              { key: "staging", label: "Incoming", value: plan.staging, write: true },
            ];
          })()}
          onSave={(v) => saveFolders(v.library, v.staging, "local", true)}
          onClose={() => setSheet(null)}
        />
      )}
      {sheet?.kind === "extra" && (
        <FolderSheet
          title="Another library folder"
          nodeId="local"
          slots={[
            {
              key: "extra",
              label: `Library ${(cfg.data?.extra_library_dirs.length || 0) + 2}`,
              value: "",
            },
          ]}
          onSave={(v) =>
            saveExtra([...(cfg.data?.extra_library_dirs || []), v.extra], true)
          }
          onClose={() => setSheet(null)}
        />
      )}
      {sheet?.kind === "node" && (
        <FolderSheet
          title={`Folders on ${sheet.node.name}`}
          nodeId={sheet.node.id}
          slots={folderSlots(sheet.node)}
          onSave={(v) => saveNodeFolders(sheet.node, v.library || "", v.staging || "")}
          onClose={() => setSheet(null)}
        />
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
      title={done === null ? "Import" : "Imported"}
      onClose={onClose}
      footer={
        done === null &&
        scan.candidates.length > 0 && (
          <button
            className="btn primary"
            disabled={busy || !Object.keys(selected).length}
            onClick={confirm}
          >
            {busy
              ? "Checking and importing…"
              : !Object.keys(selected).length
                ? "Choose files to import"
                : `Import ${Object.keys(selected).length} ${Object.keys(selected).length === 1 ? "file" : "files"}`}
          </button>
        )
      }
    >
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
                {scan.candidates.length > 100 ? "Select the first 100" : "Select all"}
              </button>
              <button
                className="btn quiet"
                disabled={!Object.keys(selected).length}
                onClick={() => setSelected({})}
              >
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
