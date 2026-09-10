import CollectionCare from "./CollectionCare";
import { useState } from "react";
import { Link } from "react-router-dom";
import {
  Pause,
  Play,
  Check,
  CircleDot,
  AlertCircle,
  ChevronDown,
} from "lucide-react";
import { api, post, type Job } from "./api";
import { Empty, ErrorNote, Loading, Page, Status, useResource } from "./ui";
import { useWebSocket } from "../hooks/useWebSocket";

export default function Activity() {
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
  const jobs = (resource.data || []).filter(
    (j) => filter === "all" || j.status !== "complete",
  );
  return (
    <Page
      className="sp-activity-page"
      title="Activity"
      eyebrow="BEHIND THE SCENES"
      description="You pick the stories. Sparrow takes care of the rest."
    >
      <ErrorNote error={error || resource.error} retry={resource.refresh} />
      {resource.data && (
        <div className="sp-activity-summary" aria-label="Request overview">
          <div>
            <strong>
              {resource.data.filter((job) => job.status === "active").length}
            </strong>
            <span>
              <CircleDot />
              In progress
            </span>
          </div>
          <div>
            <strong>
              {
                resource.data.filter(
                  (job) =>
                    job.needs_attention ||
                    ["abandoned", "failed"].includes(job.status),
                ).length
              }
            </strong>
            <span>
              <AlertCircle />
              Needs attention
            </span>
          </div>
          <div>
            <strong>
              {resource.data.filter((job) => job.status === "complete").length}
            </strong>
            <span>
              <Check />
              Completed
            </span>
          </div>
        </div>
      )}
      <div className="sp-tabs" role="tablist" aria-label="Request history">
        {[
          ["current", "Current & needs attention"],
          ["all", "All requests"],
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
      {!resource.data && resource.error ? null : resource.loading &&
        !resource.data ? (
        <Loading label="Loading requests…" />
      ) : jobs.length ? (
        <div className="sp-form">
          {jobs.map((job) => (
            <article className={`sp-panel sp-job ${job.status}`} key={job.id}>
              <div className="sp-row" style={{ paddingTop: 0 }}>
                <div>
                  <Status value={job.status} />
                  <h2 className="mt-2">
                    <Link to={`/title/${job.media_type}/${job.tmdb_id}`}>
                      {job.title}
                    </Link>
                  </h2>
                  <p>
                    {job.state_line ||
                      "Sparrow is checking what this request needs."}
                  </p>
                  <p className="mt-2 sp-job-scope">
                    {job.media_type === "movie"
                      ? "Movie"
                      : Object.entries(job.wanted_episodes)
                          .map(
                            ([season, eps]) =>
                              `Season ${season} · ${eps.length} episode${eps.length === 1 ? "" : "s"}`,
                          )
                          .join(" / ")}
                  </p>
                </div>
                <div className="sp-actions">
                  {job.needs_attention && (
                    <button
                      className="sp-button secondary"
                      disabled={busy === job.id}
                      onClick={() => control(job, "retry")}
                    >
                      Check again
                    </button>
                  )}
                  {job.status === "active" ? (
                    <button
                      className="sp-button secondary"
                      disabled={busy === job.id}
                      onClick={() => control(job, "pause")}
                    >
                      <Pause size={15} />
                      Pause
                    </button>
                  ) : (
                    job.status !== "complete" && (
                      <button
                        className="sp-button secondary"
                        disabled={busy === job.id}
                        onClick={() =>
                          control(
                            job,
                            job.status === "paused" ? "resume" : "retry",
                          )
                        }
                      >
                        <Play size={15} />
                        {job.status === "paused" ? "Resume" : "Try again"}
                      </button>
                    )
                  )}
                  {["active", "paused"].includes(job.status) && (
                    <button
                      className="sp-button quiet"
                      disabled={busy === job.id}
                      onClick={() => control(job, "cancel")}
                    >
                      Cancel request
                    </button>
                  )}
                </div>
              </div>
              <Journal job={job} />
            </article>
          ))}
        </div>
      ) : (
        <Empty
          title="Nothing needs your attention."
          action={
            <Link to="/discover" className="sp-button secondary">
              Find something to watch
            </Link>
          }
        >
          New requests and their progress will appear here.
        </Empty>
      )}
      <CollectionCare />
    </Page>
  );
}
function Journal({ job }: { job: Job }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="sp-journal">
      <button
        className="sp-button quiet"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        <ChevronDown size={16} />
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
        downloads: {
          id: string;
          progress: number;
          error: string;
          status: string;
        }[];
      }>(`/jobs/${id}`),
    [id],
  );
  if (resource.loading) return <Loading label="Loading progress…" />;
  return (
    <div className="sp-journal-entries">
      <ErrorNote error={resource.error} retry={resource.refresh} />
      {resource.data?.journal.length ? (
        resource.data.journal.map((entry) => (
          <div className="sp-row" key={entry.id}>
            <p>{entry.text}</p>
            <time
              className="sp-muted"
              dateTime={new Date(entry.ts * 1000).toISOString()}
            >
              {new Date(entry.ts * 1000).toLocaleTimeString([], {
                hour: "2-digit",
                minute: "2-digit",
              })}
            </time>
          </div>
        ))
      ) : (
        <p className="sp-muted">
          The first update will appear here when Sparrow begins checking.
        </p>
      )}
    </div>
  );
}
