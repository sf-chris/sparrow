import CollectionCare from "./CollectionCare";
import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Pause, Play, RotateCcw } from "lucide-react";
import { api, post, type Job } from "./api";
import { Empty, ErrorNote, Loading, Page, Status, useResource } from "./ui";
import { useWebSocket } from "../hooks/useWebSocket";

function scope(job: Job) {
  if (job.media_type === "movie") return "Feature film";
  return Object.entries(job.wanted_episodes)
    .map(([season, eps]) => `Season ${season} · ${eps.length} episode${eps.length === 1 ? "" : "s"}`)
    .join(" / ");
}
const when = (ts: number) =>
  new Date(ts * 1000).toLocaleString([], { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });

export default function Activity() {
  const [params, setParams] = useSearchParams();
  const selectedRequest = params.get("request");
  const resource = useResource(() => api<Job[]>("/jobs"));
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const [filter, setFilter] = useState("current");
  useWebSocket(() => {
    void resource.refresh();
  });
  async function control(job: Job, action: string) {
    setBusy(job.id);
    setError("");
    try {
      await post(`/jobs/${job.id}/${action}`);
      await resource.refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy("");
    }
  }
  const jobs = (resource.data || []).filter((j) =>
    selectedRequest ? j.id === selectedRequest : filter === "all" || j.status !== "complete",
  );
  const open = (resource.data || []).filter((j) => j.status !== "complete").length;
  return (
    <Page
      className="sp-requests"
      kicker="On order"
      title="Requests"
      description="What Sparrow is fetching for you, and everything it noticed along the way."
      action={
        <Link className="sp-btn sp-btn-ghost" to="/settings/logs">
          Activity log
        </Link>
      }
    >
      <ErrorNote error={error || resource.error} retry={resource.refresh} />
      <div className="sp-requests-bar">
        <div className="sp-tabs" role="tablist" aria-label="Request history">
          {[
            ["current", `Open${resource.data ? ` · ${open}` : ""}`],
            ["all", "All requests"],
          ].map(([value, label]) => (
            <button
              key={value}
              role="tab"
              aria-selected={!selectedRequest && filter === value}
              onClick={() => {
                setFilter(value);
                if (selectedRequest) setParams({});
              }}
            >
              {label}
            </button>
          ))}
        </div>
        {selectedRequest && (
          <button className="sp-btn sp-btn-ghost" onClick={() => setParams({})}>
            Show all requests
          </button>
        )}
      </div>
      {!resource.data && resource.error ? null : resource.loading && !resource.data ? (
        <Loading label="Loading requests…" />
      ) : jobs.length ? (
        <ol className="sp-orders">
          {jobs.map((job) => (
            <li id={`request-${job.id}`} className={`sp-order is-${job.status}`} key={job.id}>
              <div className="sp-order-main">
                <div className="sp-order-meta">
                  <Status value={job.needs_attention ? "abandoned" : job.status} />
                  <span className="sp-label">{scope(job)}</span>
                  <span className="sp-label">Updated {when(job.updated_at)}</span>
                </div>
                <h2>
                  <Link to={`/title/${job.media_type}/${job.tmdb_id}`}>{job.title}</Link>
                </h2>
                <p className="sp-order-line">
                  {job.state_line || "Sparrow is working out what this request needs."}
                </p>
              </div>
              <div className="sp-actions sp-order-actions">
                {job.needs_attention && (
                  <button
                    className="sp-btn sp-btn-solid sp-btn-small"
                    disabled={busy === job.id}
                    onClick={() => control(job, "retry")}
                  >
                    <RotateCcw size={14} aria-hidden="true" />
                    Check again
                  </button>
                )}
                {job.status === "active" ? (
                  <button
                    className="sp-btn sp-btn-line sp-btn-small"
                    disabled={busy === job.id}
                    onClick={() => control(job, "pause")}
                  >
                    <Pause size={14} aria-hidden="true" />
                    Pause
                  </button>
                ) : (
                  job.status !== "complete" && (
                    <button
                      className="sp-btn sp-btn-line sp-btn-small"
                      disabled={busy === job.id}
                      onClick={() => control(job, job.status === "paused" ? "resume" : "retry")}
                    >
                      <Play size={14} aria-hidden="true" />
                      {job.status === "paused" ? "Resume" : "Try again"}
                    </button>
                  )
                )}
                {["active", "paused"].includes(job.status) && (
                  <button
                    className="sp-btn sp-btn-ghost sp-btn-small"
                    disabled={busy === job.id}
                    onClick={() => control(job, "cancel")}
                  >
                    Cancel request
                  </button>
                )}
              </div>
              <Journal job={job} />
            </li>
          ))}
        </ol>
      ) : (
        <Empty
          title={selectedRequest ? "This request is no longer here." : "Nothing on order."}
          action={
            <Link to="/discover" className="sp-btn sp-btn-line">
              Find something to watch
            </Link>
          }
        >
          {selectedRequest
            ? "It may have been removed, or your access may have changed. Use Show all requests to go back."
            : "When you ask for a film or series, you can follow its progress here."}
        </Empty>
      )}
      <CollectionCare />
    </Page>
  );
}

function Journal({ job }: { job: Job }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="sp-order-journal">
      <button className="sp-journal-toggle" aria-expanded={open} onClick={() => setOpen(!open)}>
        <span aria-hidden="true">{open ? "–" : "+"}</span>
        What Sparrow checked
      </button>
      {open && <JournalEntries id={job.id} />}
    </div>
  );
}

function JournalEntries({ id }: { id: string }) {
  const resource = useResource(
    () =>
      api<{
        journal: { id: string; text: string; ts: number }[];
        downloads: { id: string; progress: number; error: string; status: string }[];
      }>(`/jobs/${id}`),
    [id],
  );
  if (resource.loading) return <Loading label="Opening the journal…" />;
  return (
    <div className="sp-journal">
      <ErrorNote error={resource.error} retry={resource.refresh} />
      {resource.data?.journal.length ? (
        <ol>
          {resource.data.journal.map((entry) => (
            <li key={entry.id}>
              <time dateTime={new Date(entry.ts * 1000).toISOString()}>
                {new Date(entry.ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
              </time>
              <p>{entry.text}</p>
            </li>
          ))}
        </ol>
      ) : (
        <p className="sp-journal-empty">The first entry appears here as soon as Sparrow starts checking.</p>
      )}
    </div>
  );
}
