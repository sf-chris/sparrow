import { useState } from "react";
import { api, type JobKey, type Jobs, type NodeInfo } from "./api";
import { Dialog } from "./ui";

export const JOB_NAMES: Record<JobKey | "playback", string> = {
  storage: "Storage",
  playback: "Playback",
  downloads: "Downloads",
  subtitles: "Subtitles",
};

/** What turning a job on or off does that people can't see. */
function hint(job: JobKey, node: NodeInfo) {
  const local = node.id === "local";
  const gpu = node.capabilities.speech?.gpu;
  if (job === "storage")
    return "Keeps films and series. Turned off, it keeps what’s here and takes nothing new.";
  if (job === "downloads")
    return local
      ? "Uses the download app in Connections."
      : "Runs Sparrow’s own download app here. Titles kept elsewhere move there when they finish.";
  return gpu
    ? `Times and writes subtitles by listening to the soundtrack. Uses the ${gpu}.`
    : local
      ? "Times and writes subtitles by listening to the soundtrack."
      : "Times and writes subtitles by listening to the soundtrack. Sets itself up the first time. Slow without a graphics card.";
}

export const defaultJobs = (node: NodeInfo): Jobs =>
  node.health?.jobs || {
    storage: true,
    downloads: true,
    subtitles: node.id === "local" || Boolean(node.capabilities.speech?.gpu),
  };

/** The problem with a choice of jobs, given the household's other computers. */
export function jobsProblem(jobs: Jobs, node: NodeInfo, all: NodeInfo[]) {
  const others = all.filter((n) => n.id !== node.id && !n.disabled);
  if (!jobs.storage && !others.some((n) => n.health?.jobs.storage))
    return "Another computer needs Storage on first.";
  return "";
}

/** A computer's jobs as a coupon of checkboxes, each with what it does. */
export function JobChoices({
  node,
  jobs,
  onChange,
  all,
}: {
  node: NodeInfo;
  jobs: Jobs;
  onChange: (jobs: Jobs) => void;
  all: NodeInfo[];
}) {
  const downloading = all.some(
    (n) => !n.disabled && (n.id === node.id ? jobs.downloads : n.health?.jobs.downloads),
  );
  return (
    <fieldset className="job-choices">
      <legend className="sr-only">Jobs for {node.name}</legend>
      {(["storage", "downloads", "subtitles"] as JobKey[]).map((job) => (
        <label className="check job-choice" key={job}>
          <input
            type="checkbox"
            checked={jobs[job]}
            onChange={(e) => onChange({ ...jobs, [job]: e.target.checked })}
            aria-describedby={`job-${node.id}-${job}`}
          />
          <span>
            <strong>{JOB_NAMES[job]}</strong>
            <span className="muted" id={`job-${node.id}-${job}`}>
              {hint(job, node)}
            </span>
          </span>
        </label>
      ))}
      {!downloading && (
        <p className="meta job-note">Nothing will download new titles.</p>
      )}
    </fieldset>
  );
}

export async function saveJobs(node: NodeInfo, jobs: Jobs) {
  return api<NodeInfo>(`/admin/nodes/${node.id}/jobs`, {
    method: "PUT",
    body: JSON.stringify(jobs),
  });
}

/** Change what one computer does. */
export default function JobsDialog({
  node,
  all,
  onClose,
  onSaved,
}: {
  node: NodeInfo;
  all: NodeInfo[];
  onClose: () => void;
  onSaved: () => void | Promise<void>;
}) {
  const [jobs, setJobs] = useState<Jobs>(defaultJobs(node));
  const updatable = node.id !== "local" && node.health?.update?.auto != null;
  const wasAuto = node.health?.update?.auto ?? true;
  const [auto, setAuto] = useState(wasAuto);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const problem = jobsProblem(jobs, node, all);
  const same =
    JSON.stringify(jobs) === JSON.stringify(defaultJobs(node)) && auto === wasAuto;
  return (
    <Dialog
      title={`Jobs for ${node.name}`}
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
            disabled={busy || !!problem || same}
            onClick={async () => {
              setBusy(true);
              setError("");
              try {
                await saveJobs(node, jobs);
                if (updatable && auto !== wasAuto)
                  await api(`/admin/nodes/${node.id}/settings`, {
                    method: "PUT",
                    body: JSON.stringify({ auto_update: auto }),
                  });
                await onSaved();
              } catch (e) {
                setError((e as Error).message);
              } finally {
                setBusy(false);
              }
            }}
          >
            {busy ? "Saving…" : "Save jobs"}
          </button>
        </>
      }
    >
      <JobChoices node={node} jobs={jobs} onChange={setJobs} all={all} />
      {updatable && (
        <fieldset className="job-choices job-updates">
          <legend className="sr-only">Updates</legend>
          <label className="check job-choice">
            <input
              type="checkbox"
              checked={auto}
              onChange={(e) => setAuto(e.target.checked)}
              aria-describedby={`job-${node.id}-updates`}
            />
            <span>
              <strong>Update by itself</strong>
              <span className="muted" id={`job-${node.id}-updates`}>
                Takes each new version when this server gets one, then restarts.
                Downloads pause for a moment and carry on.
              </span>
            </span>
          </label>
        </fieldset>
      )}
    </Dialog>
  );
}
