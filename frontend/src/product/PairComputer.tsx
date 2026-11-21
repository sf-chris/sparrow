import { useEffect, useRef, useState } from "react";
import { Copy } from "lucide-react";
import { post, api, type NodeInfo } from "./api";
import { FolderPicker, overlap, type Slot } from "./FolderBrowser";
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

function libraries(node: NodeInfo) {
  return node.capabilities.roots.filter((r) => /^library(-\d+)?$/.test(r.id));
}

/** Pair a computer from one pasted command, then choose its folders. */
export default function PairComputer({
  node,
  onClose,
  onPaired,
}: {
  node?: NodeInfo;
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
  const [slots, setSlots] = useState<Slot[]>([]);
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
          if (!libraries(next.node).length && next.node.capabilities.folders) {
            setSlots([
              { key: "library", label: "Library", value: "" },
              { key: "staging", label: "Incoming", value: "", write: true },
            ]);
          } else onPaired(next.node.id);
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
  if (paired && slots.length) {
    const problem = overlap(slots);
    return (
      <Dialog
        title={`Folders on ${paired.name}`}
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
              disabled={saving || !!problem || !slots.every((s) => s.value)}
              onClick={async () => {
                setSaving(true);
                setError("");
                try {
                  await post(`/admin/nodes/${paired.id}/folders`, {
                    libraries: [slots[0].value],
                    staging: slots[1].value,
                  });
                  onPaired(paired.id);
                } catch (e) {
                  setError((e as Error).message);
                } finally {
                  setSaving(false);
                }
              }}
            >
              {saving ? "Saving…" : "Save folders"}
            </button>
          </>
        }
      >
        <p className="muted picker-intro">
          Paired. Choose where its films and series go, and a separate folder
          for downloads.
        </p>
        <FolderPicker nodeId={paired.id} slots={slots} onChange={setSlots} />
      </Dialog>
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
    <Dialog title={node ? `Reconnect ${node.name}` : "Pair a computer"} onClose={onClose}>
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
