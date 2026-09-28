import CollectionCare from "./CollectionCare";
import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Pause, Play } from "lucide-react";
import { api, post, type Job } from "./api";
import { Circled } from "./Brand";
import { Empty, ErrorNote, Flag, Loading, Page, useResource } from "./ui";
import { Age, scope } from "./Collection";
import { useWebSocket } from "../hooks/useWebSocket";

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
  const jobs = (resource.data || [])
    .filter((j) =>
      selectedRequest
        ? j.id === selectedRequest
        : filter === "all" || j.status !== "complete",
    )
    .sort((a, b) => b.updated_at - a.updated_at);
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
      <div className="request-tools">
        <div className="tabs" role="tablist" aria-label="Which requests">
          {[
            ["current", "Current"],
            ["all", "All"],
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
          <button className="btn quiet" onClick={() => setParams({})}>
            Show all requests
          </button>
        )}
      </div>
      {!resource.data && resource.error ? null : resource.loading &&
        !resource.data ? (
        <Loading label="Loading requests" />
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
                      <Link to={`/title/${job.media_type}/${job.tmdb_id}`}>
                        {open ? <Circled>{job.title}</Circled> : job.title}
                      </Link>
                    </h2>
                    <Flag
                      value={job.needs_attention ? "attention" : job.status}
                    />
                  </div>
                  <p className="meta">{scope(job)}</p>
                  {job.status !== "paused" && (
                    <p className="request-note">
                      {job.state_line ||
                        "Sparrow is checking what this request needs."}
                    </p>
                  )}
                  {open && (
                    <div className="actions">
                      {job.needs_attention && (
                        <button
                          className="btn"
                          disabled={busy === job.id}
                          onClick={() => control(job, "retry")}
                        >
                          Check again
                        </button>
                      )}
                      {job.status === "active" ? (
                        <button
                          className="btn"
                          disabled={busy === job.id}
                          onClick={() => control(job, "pause")}
                        >
                          <Pause size={16} strokeWidth={2.5} />
                          Pause
                        </button>
                      ) : (
                        <button
                          className="btn"
                          disabled={busy === job.id}
                          onClick={() =>
                            control(
                              job,
                              job.status === "paused" ? "resume" : "retry",
                            )
                          }
                        >
                          <Play size={16} strokeWidth={2.5} />
                          {job.status === "paused" ? "Resume" : "Try again"}
                        </button>
                      )}
                      {["active", "paused"].includes(job.status) && (
                        <button
                          className="btn quiet"
                          disabled={busy === job.id}
                          onClick={() => control(job, "cancel")}
                        >
                          Cancel request
                        </button>
                      )}
                    </div>
                  )}
                  <Journal job={job} />
                </div>
              </li>
            );
          })}
        </ol>
      ) : (
        <Empty
          title={selectedRequest ? "Request not found." : "Nothing coming up."}
          action={
            <Link to="/discover" className="btn">
              Find something
            </Link>
          }
        >
          {selectedRequest
            ? "It may have been removed, or your access changed."
            : "New requests and their progress appear here."}
        </Empty>
      )}
      <CollectionCare titles={titles} />
    </Page>
  );
}
function Journal({ job }: { job: Job }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="journal">
      <button
        className="journal-toggle"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        Notes
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
        downloads: {
          id: string;
          progress: number;
          error: string;
          status: string;
        }[];
      }>(`/jobs/${id}`),
    [id],
  );
  if (resource.loading) return <Loading label="Loading notes" />;
  return (
    <div className="journal-entries">
      <ErrorNote error={resource.error} retry={resource.refresh} />
      {resource.data?.journal.length ? (
        <ol>
          {resource.data.journal.map((entry) => (
            <li key={entry.id}>
              <time
                className="num"
                dateTime={new Date(entry.ts * 1000).toISOString()}
              >
                {new Date(entry.ts * 1000).toLocaleTimeString([], {
                  hour: "2-digit",
                  minute: "2-digit",
                  hourCycle: "h23",
                })}
              </time>
              <p>{entry.text}</p>
            </li>
          ))}
        </ol>
      ) : (
        <p className="muted">
          The first note appears when Sparrow starts checking.
        </p>
      )}
    </div>
  );
}
