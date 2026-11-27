import { useEffect, useRef, useState } from "react";
import { Copy } from "lucide-react";
import { post, api, type Jobs, type NodeInfo } from "./api";
import { JobChoices, defaultJobs, jobsProblem, saveJobs } from "./ComputerJobs";
import { FolderChooser } from "./FolderBrowser";
import { Dialog } from "./ui";

type System = "macos" | "windows" | "linux";
type Pairing = {
  stage: string | null;
  paired: boolean;
  scripted: boolean;
  expires_in_seconds: number;
  node: NodeInfo | null;
};

const systems: [System, string][] = [
  ["macos", "Mac"],
  ["windows", "Windows"],
  ["linux", "Linux"],
];

function guess(): System {
  const agent = navigator.userAgent;
  if (/Windows/i.test(agent)) return "windows";
  if (/Mac/i.test(agent) && !/iPhone|iPad/i.test(agent)) return "macos";
  return "linux";
}

const stages: Record<string, string> = {
  fetched: "Setting up…",
  started: "Setting up…",
  installing: "Downloading Sparrow Node…",
  tools: "Installing media tools…",
  python: "Getting Python…",
  pairing: "Pairing…",
  paired: "Paired. Starting it…",
  starting: "Paired. Starting it…",
  running: "Connecting…",
};
const failures: Record<string, string> = {
  system: "That computer can’t run this command. Check you chose the right system.",
  download: "That computer couldn’t download Sparrow Node from here.",
  python: "That computer couldn’t get Python.",
  pair: "Pairing didn’t work.",
  service: "Sparrow Node couldn’t start in the background.",
};

function command(system: System, server: string, code: string) {
  const url = `${server}/api/v1/node/setup/${code}.${system === "windows" ? "ps1" : "sh"}`;
  return system === "windows" ? `irm ${url} | iex` : `curl -fsSL ${url} | sh`;
}

async function copy(text: string, field: HTMLTextAreaElement | null) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    // Plain-HTTP addresses have no clipboard API.
    field?.select();
    return document.execCommand("copy");
  }
}

/** Add a computer from one pasted command, then choose its jobs and the
 *  folders those jobs need. With `fix`, the same command sets a paired
 *  computer up again: it updates and adds what's missing, keeping its folders
 *  and jobs. */
export default function PairComputer({
  node,
  fix = false,
  all = [],
  onClose,
  onPaired,
}: {
  node?: NodeInfo;
  fix?: boolean;
  all?: NodeInfo[];
  onClose: () => void;
  onPaired: (id: string) => void;
}) {
  const [system, setSystem] = useState<System>(guess);
  const local = /^(localhost|127\.|\[::1\])/.test(location.hostname);
  const [server, setServer] = useState(location.origin);
  const [editing, setEditing] = useState(local);
  const [code, setCode] = useState("");
  const [nodeId, setNodeId] = useState(node?.id || "");
  const [status, setStatus] = useState<Pairing | null>(null);
  const [renewed, setRenewed] = useState(false);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState("");
  const [paired, setPaired] = useState<NodeInfo | null>(null);
  // Storage and Downloads need a library folder, chosen once the jobs are.
  const [needsFolder, setNeedsFolder] = useState(false);
  const [jobs, setJobs] = useState<Jobs | null>(null);
  const [saving, setSaving] = useState(false);
  const field = useRef<HTMLTextAreaElement>(null);
  const started = useRef(false);

  async function enroll(again = false) {
    setError("");
    try {
      const result = await post<{ code: string; node_id: string }>(
        "/admin/nodes/enroll",
        { name: node?.name || "", node_id: node?.id || nodeId || undefined },
      );
      setCode(result.code);
      setNodeId(result.node_id);
      setStatus(null);
      setCopied(false);
      setRenewed(again);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    void enroll();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Watch the other computer set itself up, and move on when it connects.
  useEffect(() => {
    if (!nodeId || !code || paired) return;
    let stop = false;
    const tick = async () => {
      try {
        const next = await api<Pairing>(`/admin/nodes/pairing/${nodeId}`);
        if (stop) return;
        setStatus(next);
        const running = !next.scripted || next.stage === "running";
        if (next.paired && next.node?.online && running) {
          setPaired(next.node);
          // A new computer chooses what it's for; one set up again keeps its jobs.
          if (!node) setJobs(defaultJobs(next.node));
          else onPaired(next.node.id);
        } else if (!next.paired && !next.stage && next.expires_in_seconds <= 0) {
          void enroll(true);
        }
      } catch {
        /* Keep watching; the next check usually answers. */
      }
    };
    void tick();
    const timer = window.setInterval(tick, 1500);
    return () => {
      stop = true;
      window.clearInterval(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodeId, code, paired]);

  const name = paired?.name || node?.name || "the computer";
  if (paired && jobs && !needsFolder) {
    const problem = jobsProblem(jobs, paired, all);
    return (
      <Dialog
        title={`What ${paired.name} does`}
        onClose={onClose}
        footer={
          <>
            {(problem || error) && (
              <p className="sheet-problem problem-text" role="alert">
                {problem || error}
              </p>
            )}
            <button
              className="btn primary"
              disabled={saving || !!problem}
              onClick={async () => {
                setSaving(true);
                setError("");
                try {
                  const saved = await saveJobs(paired, jobs);
                  const label = (id: string) =>
                    saved.capabilities.roots.find((r) => r.id === id)?.label || "";
                  if (!paired.capabilities.folders || !(jobs.storage || jobs.downloads))
                    onPaired(paired.id);
                  else if (!label("library")) setNeedsFolder(true);
                  else {
                    if (jobs.downloads && !label("staging"))
                      // Set up again with Downloads newly on: Incoming goes beside the library.
                      await post(`/admin/nodes/${paired.id}/folders`, {
                        libraries: [label("library")],
                        staging: "auto",
                      });
                    onPaired(paired.id);
                  }
                } catch (e) {
                  setError((e as Error).message);
                } finally {
                  setSaving(false);
                }
              }}
            >
              {saving ? "Saving…" : "Next"}
            </button>
          </>
        }
      >
        <p className="muted picker-intro">Added. Choose what it does.</p>
        <JobChoices node={paired} jobs={jobs} onChange={setJobs} all={all} />
      </Dialog>
    );
  }
  if (paired && needsFolder) {
    return (
      <FolderChooser
        nodeId={paired.id}
        title={`Where ${paired.name} keeps titles`}
        intro={
          !jobs?.downloads
            ? undefined
            : jobs.storage
              ? "Downloads go in a folder beside it."
              : "Finished downloads wait here, then move to where titles are kept. Downloads in progress go in a folder beside it."
        }
        verb={(name) => `Keep titles in ${name}`}
        onChoose={async (path) => {
          await post(`/admin/nodes/${paired.id}/folders`, {
            libraries: [path],
            staging: jobs?.downloads ? "auto" : "",
          });
          onPaired(paired.id);
        }}
        onClose={onClose}
      />
    );
  }

  const stage = status?.stage || "";
  const failed = stage.startsWith("failed:")
    ? failures[stage.slice(7)] || "Setup stopped on that computer."
    : "";
  const shell = system === "windows" ? "PowerShell" : "Terminal";
  const line = code ? command(system, server.replace(/\/+$/, ""), code) : "";
  const minutes = Math.ceil((status?.expires_in_seconds ?? 600) / 60);
  return (
    <Dialog
      title={
        !node
          ? "Add a computer"
          : fix
            ? `Set up ${node.name} again`
            : `Reconnect ${node.name}`
      }
      onClose={onClose}
    >
      <div className="form pair">
        <fieldset className="segmented">
          <legend className="sr-only">Its system</legend>
          {systems.map(([value, label]) => (
            <label key={value}>
              <input
                type="radio"
                name="system"
                value={value}
                checked={system === value}
                onChange={() => {
                  setSystem(value);
                  setCopied(false);
                }}
              />
              <span>{label}</span>
            </label>
          ))}
        </fieldset>
        <p>
          On {node ? node.name : "that computer"}, paste this into {shell}.
          {fix && " It updates Sparrow Node and adds anything missing. Its folders and jobs stay as they are."}
        </p>
        <div className="pair-command">
          <textarea
            ref={field}
            readOnly
            rows={3}
            aria-label={`Command for ${shell}`}
            value={line || "Making a command…"}
            onFocus={(e) => e.target.select()}
          />
          <button
            className="btn primary"
            disabled={!line}
            onClick={async () => setCopied(await copy(line, field.current))}
          >
            <Copy size={17} strokeWidth={2.25} />
            {copied ? "Copied" : "Copy"}
          </button>
        </div>
        {editing ? (
          <label className="pair-address">
            <span>Address that computer uses for Sparrow</span>
            <input
              value={server}
              inputMode="url"
              onChange={(e) => setServer(e.target.value.trim())}
            />
            {local && server === location.origin && (
              <span className="problem-text">
                This address only works on this computer.
              </span>
            )}
          </label>
        ) : (
          <button className="link pair-change" onClick={() => setEditing(true)}>
            Uses {server}
          </button>
        )}
        <p className="pair-status" role="status" aria-live="polite">
          {error ? (
            <span className="problem-text">{error}</span>
          ) : failed ? (
            <span className="problem-text">
              {failed} The message on {name} says why. Run the command again
              when it’s fixed.
            </span>
          ) : (
            <>
              <span className="pulse" aria-hidden="true" />
              {stages[stage] ||
                (renewed
                  ? "That command expired, so here’s a fresh one. Waiting…"
                  : `Waiting for ${name}…`)}
              {!stage && code && (
                <span className="meta num"> Works for {minutes} min.</span>
              )}
            </>
          )}
        </p>
      </div>
    </Dialog>
  );
}
