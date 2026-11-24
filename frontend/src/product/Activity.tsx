import CollectionCare from "./CollectionCare";
import { useState } from "react";
import {
  Link,
  Navigate,
  useLocation,
  useNavigate,
  useParams,
  useSearchParams,
} from "react-router-dom";
import { ArrowRight, Info, Pause, Play } from "lucide-react";
import { api, post, type Job, type User } from "./api";
import { Circled } from "./Brand";
import {
  BackLink,
  Bar,
  Cover,
  Dialog,
  Empty,
  ErrorNote,
  Flag,
  Loading,
  Page,
  RequestProgress,
  bytes,
  useResource,
} from "./ui";
import { Age, scope } from "./Collection";
import { useWebSocket } from "../hooks/useWebSocket";

type Spending = {
  alerts: { kind: string; measure: string; text: string; job_id?: string }[];
};

/** Administrators only: titles and recent runs whose cost to find (or to
 * subtitle) is over its target. Titles and money only. */
function SpendingAlerts({ open }: { open: (id: string) => void }) {
  const resource = useResource(() => api<Spending>("/admin/spending"));
  const alerts = resource.data?.alerts || [];
  if (!alerts.length) return null;
  return (
    <section className="spending" aria-labelledby="spending">
      <Bar id="spending" title="Over target" />
      <ul className="rows">
        {alerts.map((alert) => (
          <li className="row" key={`${alert.measure}:${alert.job_id || ""}`}>
            <p>{alert.text}</p>
            {alert.job_id && (
              <button className="btn quiet" onClick={() => open(alert.job_id!)}>
                Open request
              </button>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

export default function Activity({ user }: { user: User }) {
  const [params] = useSearchParams();
  const selectedRequest = params.get("request");
  const resource = useResource(() => api<Job[]>("/jobs"));
  const [error, setError] = useState("");
  const [filter, setFilter] = useState("current");
  const location = useLocation();
  const navigate = useNavigate();
  const from = { from: location.pathname + location.search };
  useWebSocket(() => {
    void resource.refresh();
  });
  const jobs = (resource.data || [])
    .filter((j) => filter === "all" || j.status !== "complete")
    .sort((a, b) => b.updated_at - a.updated_at);
  // Older links (alerts, logs) name one request: open its details page.
  if (selectedRequest)
    return <Navigate to={`/activity/${selectedRequest}`} replace />;
  const titles = Object.fromEntries(
    (resource.data || []).map((j) => [`${j.media_type}:${j.tmdb_id}`, j.title]),
  );
  return (
    <Page
      className="requests-page"
      title="Requests"
      action={
        <Link className="btn quiet" to="/settings/logs">
          Logs
        </Link>
      }
    >
      <ErrorNote error={error || resource.error} retry={resource.refresh} />
      {user.role === "admin" && (
        <SpendingAlerts
          open={(id) => navigate(`/activity/${id}`, { state: from })}
        />
      )}
      <div className="request-tools">
        <div className="tabs" role="tablist" aria-label="Which requests">
          {[
            ["current", "Current"],
            ["all", "All"],
          ].map(([value, label]) => (
            <button
              key={value}
              role="tab"
              aria-selected={filter === value}
              onClick={() => setFilter(value)}
            >
              {label}
            </button>
          ))}
        </div>
      </div>
      {!resource.data && resource.error ? null : resource.loading &&
        !resource.data ? (
        <Loading page label="Loading requests" />
      ) : jobs.length ? (
        <ol className="requests">
          {jobs.map((job) => {
            const open = job.status !== "complete";
            return (
              <li
                id={`request-${job.id}`}
                className={`request ${job.status}`}
                key={job.id}
              >
                <Age seconds={job.updated_at} />
                <div className="request-body">
                  <div className="request-top">
                    <h2>
                      <Link
                        to={`/title/${job.media_type}/${job.tmdb_id}`}
                        state={from}
                      >
                        {open ? <Circled>{job.title}</Circled> : job.title}
                      </Link>
                    </h2>
                    <Flag
                      value={job.needs_attention ? "attention" : job.status}
                    />
                  </div>
                  <p className="meta">{scope(job)}</p>
                  <RequestProgress job={job} />
                  {job.status !== "paused" && (
                    <p className="request-note">
                      {job.state_line || "Starting…"}
                    </p>
                  )}
                  <div className="actions">
                    {open && (
                      <RequestActions
                        job={job}
                        onDone={resource.refresh}
                        onError={setError}
                      />
                    )}
                    <Link
                      className="details-link"
                      to={`/activity/${job.id}`}
                      state={from}
                    >
                      Details
                      <ArrowRight size={16} strokeWidth={2.5} />
                    </Link>
                  </div>
                </div>
              </li>
            );
          })}
        </ol>
      ) : (
        <Empty
          title="Nothing coming up."
          action={
            <Link to="/discover" className="btn">
              Find something
            </Link>
          }
        />
      )}
      <CollectionCare titles={titles} />
    </Page>
  );
}
/** Retry, pause/resume and cancel for an open request. */
function RequestActions({
  job,
  onDone,
  onError,
}: {
  job: Job;
  onDone: () => unknown;
  onError: (message: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  async function control(action: string) {
    setBusy(true);
    onError("");
    try {
      await post(`/jobs/${job.id}/${action}`);
      await onDone();
    } catch (e) {
      onError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      {job.needs_attention && (
        <button className="btn" disabled={busy} onClick={() => control("retry")}>
          Check again
        </button>
      )}
      {job.status === "active" ? (
        <button className="btn" disabled={busy} onClick={() => control("pause")}>
          <Pause size={16} strokeWidth={2.5} />
          Pause
        </button>
      ) : (
        <button
          className="btn"
          disabled={busy}
          onClick={() => control(job.status === "paused" ? "resume" : "retry")}
        >
          <Play size={16} strokeWidth={2.5} />
          {job.status === "paused" ? "Resume" : "Try again"}
        </button>
      )}
      {["active", "paused"].includes(job.status) && (
        <button
          className="btn quiet"
          disabled={busy}
          onClick={() => control("cancel")}
        >
          Cancel request
        </button>
      )}
    </>
  );
}

type Picked = {
  id: string;
  name: string;
  quality: string;
  status: string;
  progress: number;
  size_bytes: number;
  added_at: number;
  files: string[];
  skipped: number;
  selected_bytes: number;
  reused: boolean;
  error: string;
};
type Detail = {
  job: Job;
  journal: { id: string; text: string; ts: number }[];
  downloads: Picked[];
};

const pickedState: Record<string, string> = {
  queued: "Starting",
  downloading: "Downloading",
  paused: "Paused",
  completed: "Downloaded, being checked",
  seeding: "Downloaded, being checked",
  organizing: "Being added to your collection",
  organized: "In your collection",
  error: "Set aside",
};

/** The ⓘ on a request: the copy Sparrow chose and the files it is taking. */
function PickedInfo({ downloads, requestedBy }: { downloads: Picked[]; requestedBy?: string }) {
  const [shown, setShown] = useState(false);
  const ordered = [...downloads].sort(
    (a, b) => Number(a.status === "error") - Number(b.status === "error") || b.added_at - a.added_at,
  );
  return (
    <>
      <button
        className="info-button"
        aria-label="What Sparrow picked"
        title="What Sparrow picked"
        onClick={() => setShown(true)}
      >
        <Info size={18} strokeWidth={2} />
      </button>
      {shown && (
        <Dialog title="What Sparrow picked" onClose={() => setShown(false)}>
          {requestedBy && <p className="meta">Requested by {requestedBy}</p>}
          {!ordered.length ? (
            <p className="muted">Sparrow hasn’t picked a copy yet.</p>
          ) : (
            ordered.map((d) => (
              <dl className="info-list picked" key={d.id}>
                <div>
                  <dt>Copy</dt>
                  <dd className="release">{d.name}</dd>
                </div>
                {d.quality && (
                  <div>
                    <dt>Quality</dt>
                    <dd>{d.quality}</dd>
                  </div>
                )}
                <div>
                  <dt>Taking</dt>
                  <dd>
                    {d.files.length
                      ? `${d.files.length} of ${d.files.length + d.skipped} files${d.selected_bytes ? ` · ${bytes(d.selected_bytes)}` : ""}`
                      : d.size_bytes
                        ? `The whole copy · ${bytes(d.size_bytes)}`
                        : "The whole copy"}
                  </dd>
                </div>
                {!!d.files.length && (
                  <div>
                    <dt>Files</dt>
                    <dd>
                      {d.files.map((file) => (
                        <span className="info-line release" key={file}>
                          {file}
                        </span>
                      ))}
                    </dd>
                  </div>
                )}
                <div>
                  <dt>Status</dt>
                  <dd>
                    {pickedState[d.status] || d.status}
                    {d.status === "downloading" ? ` · ${Math.floor(100 * d.progress)}%` : ""}
                    {d.status === "error" && d.error ? ` · ${d.error}` : ""}
                  </dd>
                </div>
                {d.reused && (
                  <div>
                    <dt>Note</dt>
                    <dd>Reuses a copy an earlier request already started, so finished parts aren’t fetched twice.</dd>
                  </div>
                )}
              </dl>
            ))
          )}
        </Dialog>
      )}
    </>
  );
}

const timeOf = (ts: number) =>
  new Date(ts * 1000).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  });

/** One request: where it is now, what can be done, and Sparrow's notes. */
export function RequestDetail() {
  const { jobId = "" } = useParams();
  const location = useLocation();
  const resource = useResource(() => api<Detail>(`/jobs/${jobId}`), [jobId]);
  const [error, setError] = useState("");
  useWebSocket(() => {
    void resource.refresh();
  });
  const back = <BackLink from={location.state?.from} fallback="/activity" />;
  const detail = resource.data;
  if (!detail)
    return (
      <Page title="" className="request-detail">
        {back}
        <ErrorNote error={resource.error} retry={resource.refresh} />
        {resource.loading && <Loading page label="Opening this request" />}
      </Page>
    );
  const { job, journal, downloads } = detail;
  const open = job.status !== "complete";
  const from = { from: location.pathname };
  return (
    <Page title="" className="request-detail">
      {back}
      <header className="request-head">
        <Link
          to={`/title/${job.media_type}/${job.tmdb_id}`}
          state={from}
          aria-label={`Open ${job.title}`}
        >
          <Cover
            title={job.title}
            src={
              job.poster_path
                ? `https://image.tmdb.org/t/p/w342${job.poster_path}`
                : undefined
            }
          />
        </Link>
        <div className="request-head-copy">
          <h1 className="display">{job.title}</h1>
          <p className="meta">{scope(job)}</p>
          <Flag value={job.needs_attention ? "attention" : job.status} />
        </div>
      </header>
      <ErrorNote error={error} />
      <section className="request-now" aria-label="Now">
        <RequestProgress job={job} />
        {job.status !== "paused" && (
          <p className="request-note">{job.state_line || "Starting…"}</p>
        )}
        <div className="actions">
          {open && (
            <RequestActions
              job={job}
              onDone={resource.refresh}
              onError={setError}
            />
          )}
          <Link
            className={open ? "btn quiet" : "btn"}
            to={`/title/${job.media_type}/${job.tmdb_id}`}
            state={from}
          >
            Open title
          </Link>
          <PickedInfo downloads={downloads || []} requestedBy={job.requested_by} />
        </div>
      </section>
      <section aria-labelledby="notes">
        <Bar id="notes" title="Notes" />
        {journal.length ? (
          <ol className="notes-timeline">
            {[...journal].reverse().map((entry) => (
              <li key={entry.id}>
                <time
                  className="num"
                  dateTime={new Date(entry.ts * 1000).toISOString()}
                >
                  {timeOf(entry.ts)}
                </time>
                <p>{entry.text}</p>
              </li>
            ))}
          </ol>
        ) : (
          <p className="muted notes-empty">No notes yet.</p>
        )}
      </section>
    </Page>
  );
}
