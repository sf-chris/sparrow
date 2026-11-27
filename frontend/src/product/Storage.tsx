import { useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { HardDrive, Plus, RefreshCw } from "lucide-react";
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
import JobsDialog from "./ComputerJobs";
import { Tick } from "./Brand";
import { FolderChooser, folderName, overlap, type Question } from "./FolderBrowser";
import { FoundSheet, foundLine } from "./Found";
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
  /** Something other than a path in the middle of the line. */
  detail?: ReactNode;
  suggested?: boolean;
  /** Choose another folder for this line, by clicking its path. */
  change?: { onClick: () => void; label: string };
  end: ReactNode;
  action?: ReactNode;
};
/** Which folder of a computer is being chosen. */
type Choosing = { node: NodeInfo; kind: "library" | "staging" | "extra" };

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
  const [choosing, setChoosing] = useState<Choosing | null>(null);
  // This server's folders while they're first chosen, saved together.
  const [draft, setDraft] = useState<{ library?: string; staging?: string }>({});
  const [highlight, setHighlight] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const [foundOn, setFoundOn] = useState<NodeInfo | null>(null);
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
          Boolean(n.relocation) ||
          Boolean(n.found?.looking) ||
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
  /** Folders chosen just now, else saved ones this server can see, else suggested ones. */
  function planFolders(node: NodeInfo) {
    const usable = (id: string) =>
      node.capabilities.roots.find((r) => r.id === id)?.available;
    return {
      library:
        draft.library ||
        (usable("library") && cfg.data?.library_dir) ||
        suggested.data?.library ||
        "",
      staging:
        draft.staging ||
        (usable("staging") && cfg.data?.staging_dir) ||
        suggested.data?.incoming ||
        "",
    };
  }
  /** Whether this server's folders are still being chosen: they're saved together.
   *  Folders already working change one at a time, asking about titles kept. */
  function settingUp(node: NodeInfo) {
    const root = (id: string) => node.capabilities.roots.find((r) => r.id === id);
    return (
      node.id === "local" &&
      (!cfg.data?.library_dir ||
        !cfg.data?.staging_dir ||
        !root("library")?.available ||
        !root("staging")?.available)
    );
  }
  function choose(node: NodeInfo, kind: Choosing["kind"]) {
    setError("");
    setChoosing({ node, kind });
  }
  /** Use a chosen folder. A sheet shows its own errors, so this throws. */
  async function useFolder(node: NodeInfo, kind: Choosing["kind"], path: string, titles?: string) {
    if (settingUp(node) && kind !== "extra") {
      setDraft({ ...draft, [kind === "staging" ? "staging" : "library"]: path });
      setChoosing(null);
      return;
    }
    if (kind === "extra") await post(`/admin/nodes/${node.id}/libraries`, { path });
    else
      await api(`/admin/nodes/${node.id}/${kind === "staging" ? "incoming" : "library"}`, {
        method: "PUT",
        body: JSON.stringify(kind === "staging" ? { path } : { path, titles: titles || null }),
      });
    setChoosing(null);
    await Promise.all([nodes.refresh(), cfg.refresh(), onChanged?.()]);
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
      setDraft({});
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
      await Promise.all([nodes.refresh(), cfg.refresh()]);
      await onChanged?.();
    } catch (e) {
      if (rethrow) throw e;
      setError((e as Error).message);
    } finally {
      setBusy("");
    }
  }
  /** Look in a computer's library folders now, for files copied there. */
  async function look(node: NodeInfo) {
    setError("");
    try {
      await post(`/admin/found/${node.id}/look`);
      await nodes.refresh();
    } catch (e) {
      setError((e as Error).message);
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
  function fix(node: NodeInfo, what: Fix, job: JobRow["job"]) {
    setError("");
    if (what === "folders") choose(node, job === "downloads" ? "staging" : "library");
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
        error={(!pairing && !choosing && !jobsFor && error) || nodes.error || cfg.error}
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
                  onFix={(what) => fix(node, what, job)}
                />
              );
              // A path is a way to choose another folder, where Sparrow can reach the computer.
              const changeable =
                local || Boolean(node.capabilities.folders && node.online && !node.disabled);
              const who = local ? "this server" : node.name;
              const moving = node.relocation;
              const library = local
                ? cfg.data?.library_dir || ""
                : rootOf("library")?.label || "";
              const incoming = local
                ? cfg.data?.staging_dir || ""
                : rootOf("staging")?.label || "";
              // While this server's folders are being chosen, rows show the folders themselves.
              // During setup the lines say how each folder is, not each job.
              const choosing = settingUp(node) || (local && onboarding);
              const plan = choosing ? planFolders(node) : null;
              const extras = local
                ? (cfg.data?.extra_library_dirs || []).map((path, n) => ({
                    path,
                    root: rootOf(`library-${n + 2}`),
                    id: `library-${n + 2}`,
                    label: `Library ${n + 2}`,
                    extra: n as number | undefined,
                  }))
                : disks.slice(1).map((root, n) => ({
                    path: root.label || "",
                    root,
                    id: root.id,
                    label: `Library ${n + 2}`,
                    extra: undefined as number | undefined,
                  }));
              const lines: Line[] = [
                {
                  key: "storage",
                  label: "Storage",
                  path: plan?.library || library,
                  suggested: choosing ? plan?.library !== library : false,
                  change: changeable
                    ? {
                        onClick: () => choose(node, "library"),
                        label: `Change where ${who} keeps titles`,
                      }
                    : undefined,
                  end: choosing ? (
                    <FolderState
                      path={plan?.library === library ? library : ""}
                      root={rootOf("library")}
                      write={false}
                      suggestion={plan?.library || ""}
                    />
                  ) : (
                    state("storage")
                  ),
                },
                ...extras.map((extra) => ({
                  key: extra.label,
                  label: moving?.from_root === extra.id ? "Moving from" : extra.label,
                  path: extra.path,
                  end:
                    moving?.from_root === extra.id ? (
                      <span
                        className={`folder-state ${moving.problem ? "problem-text" : "working"}`}
                        title={moving.problem || undefined}
                      >
                        {moving.done} of {moving.total} moved
                      </span>
                    ) : (
                      <FolderState
                        path={extra.path}
                        root={extra.root}
                        write={false}
                        suggestion=""
                      />
                    ),
                  action:
                    extra.extra !== undefined && moving?.from_root !== extra.id ? (
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
              // What Sparrow found in its folders, by itself or waiting for a name.
              const found =
                !onboarding && health?.jobs.storage && disks.length > 0 && !choosing
                  ? foundLine(node, { onLook: () => void look(node), onReview: () => setFoundOn(node) })
                  : null;
              if (found) lines.push({ key: "found", label: "Found", detail: found.path, end: found.end });
              if (!onboarding && rowOf("playback"))
                lines.push({ key: "playback", label: "Playback", end: state("playback") });
              const downloading = Boolean(health?.jobs.downloads);
              lines.push({
                key: "downloads",
                label: "Downloads",
                // Where downloads go matters only while something downloads here.
                path: choosing ? plan?.staging : downloading ? incoming : "",
                suggested: choosing ? plan?.staging !== incoming : false,
                change:
                  changeable && (choosing || (downloading && incoming))
                    ? {
                        onClick: () => choose(node, "staging"),
                        label: `Change where ${who} downloads to`,
                      }
                    : undefined,
                end: choosing ? (
                  <FolderState
                    path={plan?.staging === incoming ? incoming : ""}
                    root={rootOf("staging")}
                    write
                    suggestion={plan?.staging || ""}
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
                          {line.path && line.change ? (
                            <button
                              className={`path path-change ${line.suggested ? "suggested" : ""}`}
                              onClick={line.change.onClick}
                            >
                              {line.path}
                              <span className="sr-only">. {line.change.label}</span>
                            </button>
                          ) : line.path ? (
                            <span className={`path ${line.suggested ? "suggested" : ""}`}>
                              {line.path}
                            </span>
                          ) : (
                            line.detail || null
                          )}
                          <span className="leader" aria-hidden="true" />
                          {line.action}
                          {line.end}
                        </li>
                      ))}
                    </ul>
                    <div className="actions">
                      {local ? (
                        (unset || draft.library || draft.staging) && suggested.data ? (
                          <button
                            className="btn primary"
                            disabled={!!busy}
                            onClick={() => void useSuggested(node)}
                          >
                            {busy === "suggested" ? "Creating folders…" : "Use these folders"}
                          </button>
                        ) : missing && !unset ? (
                          <button
                            className="btn primary"
                            disabled={!!busy}
                            onClick={() => void recreate()}
                          >
                            {busy === "recreate" ? "Creating folders…" : "Create folders"}
                          </button>
                        ) : (
                          <>
                            {!onboarding && (
                              <button className="btn quiet" onClick={() => setJobsFor(node)}>
                                Change jobs
                              </button>
                            )}
                            {!unset && !moving && (
                              <button className="btn quiet" onClick={() => choose(node, "extra")}>
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
                          {changeable && keeps && disks.length > 0 && !moving && !onboarding && (
                            <button className="btn quiet" onClick={() => choose(node, "extra")}>
                              <Plus size={16} strokeWidth={2.5} />
                              Library folder
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
          Sparrow looks in library folders by itself and adds the titles it’s
          sure of. Files stay where they are.
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
      {choosing && (
        <Chooser
          node={nodes.data?.find((n) => n.id === choosing.node.id) || choosing.node}
          kind={choosing.kind}
          plan={settingUp(choosing.node) ? planFolders(choosing.node) : null}
          extras={cfg.data?.extra_library_dirs || []}
          config={cfg.data}
          onChoose={(path, titles) => useFolder(choosing.node, choosing.kind, path, titles)}
          onClose={() => setChoosing(null)}
        />
      )}
      {foundOn && (
        <FoundSheet
          node={foundOn}
          onClose={() => setFoundOn(null)}
          onChanged={() => nodes.refresh()}
        />
      )}
    </Page>
  );
}

const plural = (n: number, one: string) => `${n} ${n === 1 ? one : `${one}s`}`;

/** Choose one of a computer's folders: where it keeps titles, another place
 *  for them, or where it downloads to. */
function Chooser({
  node,
  kind,
  plan,
  extras: localExtras,
  config,
  onChoose,
  onClose,
}: {
  node: NodeInfo;
  kind: Choosing["kind"];
  /** This server's folders while they're first chosen. */
  plan: { library: string; staging: string } | null;
  extras: string[];
  config: Config | null;
  onChoose: (path: string, titles?: string) => Promise<void>;
  onClose: () => void;
}) {
  const local = node.id === "local";
  const roots = node.capabilities.roots;
  const root = (id: string) => roots.find((r) => r.id === id);
  const saved = {
    library: local ? config?.library_dir || "" : root("library")?.label || "",
    staging: local ? config?.staging_dir || "" : root("staging")?.label || "",
  };
  const library = plan?.library || saved.library;
  const incoming = plan?.staging || saved.staging;
  const extras = local
    ? localExtras
    : roots.filter((r) => /^library-\d+$/.test(r.id)).map((r) => r.label || "");
  const folders = [
    { label: "Library", value: library },
    ...extras.map((value, n) => ({ label: `Library ${n + 2}`, value })),
    { label: "Incoming", value: incoming },
  ];
  /** Whether a folder in this place works with the computer's other folders. */
  const fits = (label: string) => (path: string) =>
    overlap(
      kind === "extra"
        ? [...folders, { label: `Library ${extras.length + 2}`, value: path }]
        : folders.map((f) => (f.label === label ? { ...f, value: path } : f)),
    );
  const who = local ? "this server" : node.name;
  if (kind === "staging")
    return (
      <FolderChooser
        nodeId={node.id}
        title={`Where ${who} downloads to`}
        intro="Downloads stay here until they’re checked, then move to the library."
        current={saved.staging}
        start={plan?.staging || library}
        verb={(name) => `Download to ${name}`}
        write
        problem={fits("Incoming")}
        onChoose={(path) => onChoose(path)}
        onClose={onClose}
      />
    );
  if (kind === "extra")
    return (
      <FolderChooser
        nodeId={node.id}
        title={`Where else ${who} keeps titles`}
        intro="New titles go wherever there’s most room, as Automatic picks decides."
        start={library}
        verb={(name) => `Keep titles in ${name} too`}
        problem={fits("")}
        onChoose={(path) => onChoose(path)}
        onClose={onClose}
      />
    );
  const kept = root("library");
  const moving = node.relocation;
  const question = (path: string): Question | null => {
    const count = kept?.library_titles || 0;
    if (plan || !count || !kept?.available || moving) return null;
    const from = folderName(saved.library);
    return {
      text: `${from} has ${plural(count, "title")} (${size(kept.library_bytes || 0)}).`,
      choices: [
        {
          value: "move",
          label: `Move them to ${folderName(path)}`,
          hint: "They keep playing while they move. Files Sparrow doesn’t know stay where they are.",
          button: `Move ${plural(count, "title")}`,
        },
        {
          value: "both",
          label: "Use both folders",
          hint: `Titles stay in ${from}. New titles can go in either.`,
          button: "Use both folders",
        },
      ],
    };
  };
  return (
    <FolderChooser
      nodeId={node.id}
      title={`Where ${who} keeps titles`}
      current={plan ? "" : saved.library}
      start={plan?.library || saved.library}
      verb={(name) => `Keep titles in ${name}`}
      problem={fits("Library")}
      question={question}
      onChoose={onChoose}
      onClose={onClose}
    />
  );
}
