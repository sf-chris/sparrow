import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ArrowLeft, ArrowRight, RefreshCw } from "lucide-react";
import { api, type User } from "./api";
import { Empty, ErrorNote, Field, Loading, Page, useResource } from "./ui";
import { clock } from "./Collection";

type LogEntry = {
  id: number;
  category: string;
  severity: string;
  title: string;
  summary: string;
  first_ts: number;
  last_ts: number;
  repeats: number;
  job_id: string;
  action: { label: string; href: string } | null;
  detail?: Record<string, string>;
};
type History = {
  entries: LogEntry[];
  total: number;
  page: number;
  limit: number;
  snapshot: number;
};
const categories: Record<string, string> = {
  request: "Requests",
  download: "Downloads",
  import: "Imports",
  storage: "Storage",
  playback: "Playback",
  subtitle: "Subtitles",
};
const severities: Record<string, string> = {
  info: "Info",
  success: "Success",
  warning: "Warning",
  error: "Error",
};
const tones: Record<string, string> = {
  success: "done",
  warning: "problem",
  error: "problem",
};
function localDate(value: string | null) {
  if (!value || !Number.isFinite(Number(value))) return "";
  const date = new Date(Number(value) * 1000);
  if (!Number.isFinite(date.getTime())) return "";
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000)
    .toISOString()
    .slice(0, 16);
}

export default function Logs({ user }: { user: User }) {
  const [params, setParams] = useSearchParams();
  const query = params.toString();
  return (
    <Page title="Logs">
      <HistoryPage
        key={query}
        user={user}
        params={params}
        setParams={setParams}
      />
    </Page>
  );
}
function HistoryPage({
  user,
  params,
  setParams,
}: {
  user: User;
  params: URLSearchParams;
  setParams: (params: URLSearchParams, options?: { replace: boolean }) => void;
}) {
  const [q, setQ] = useState(params.get("q") || "");
  const resource = useResource(() =>
    api<History>(`/logs?${params.toString()}`),
  );
  const data = resource.data;
  function change(key: string, value: string) {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    next.delete("page");
    next.delete("snapshot");
    setParams(next);
  }
  function page(number: number) {
    if (!data) return;
    const next = new URLSearchParams(params);
    next.set("page", String(number));
    next.set("snapshot", String(data.snapshot));
    setParams(next);
  }
  function refresh() {
    if (params.has("snapshot") || params.has("page")) {
      const next = new URLSearchParams(params);
      next.delete("snapshot");
      next.delete("page");
      setParams(next);
    } else void resource.refresh();
  }
  const filtering = [
    "q",
    "category",
    "severity",
    "since",
    "until",
    "job_id",
  ].some((key) => params.has(key));
  return (
    <>
      <form
        className="log-filters"
        onSubmit={(event) => {
          event.preventDefault();
          change("q", q.trim());
        }}
      >
        <div className="field log-search">
          <label htmlFor="log-search">Title or request</label>
          <div className="inline-search">
            <input
              id="log-search"
              type="search"
              value={q}
              maxLength={300}
              onChange={(event) => setQ(event.target.value)}
              placeholder="Title or request ID"
            />
            <button className="btn">Search</button>
          </div>
        </div>
        <Field label="Category">
          <select
            value={params.get("category") || ""}
            onChange={(event) => change("category", event.target.value)}
          >
            <option value="">All</option>
            {Object.entries(categories).map(([value, label]) => (
              <option value={value} key={value}>
                {label}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Level">
          <select
            value={params.get("severity") || ""}
            onChange={(event) => change("severity", event.target.value)}
          >
            <option value="">All</option>
            {Object.entries(severities).map(([value, label]) => (
              <option value={value} key={value}>
                {label}
              </option>
            ))}
          </select>
        </Field>
        <div className="log-tools">
          <details
            className="disclosure log-time"
            open={params.has("since") || params.has("until")}
          >
            <summary>
              Time range
              {params.has("since") || params.has("until") ? " · filtered" : ""}
            </summary>
            <div className="form-grid">
              <Field label="From">
                <input
                  type="datetime-local"
                  value={localDate(params.get("since"))}
                  onChange={(event) =>
                    change(
                      "since",
                      event.target.value
                        ? String(new Date(event.target.value).getTime() / 1000)
                        : "",
                    )
                  }
                />
              </Field>
              <Field label="Until">
                <input
                  type="datetime-local"
                  value={localDate(params.get("until"))}
                  onChange={(event) =>
                    change(
                      "until",
                      event.target.value
                        ? String(new Date(event.target.value).getTime() / 1000)
                        : "",
                    )
                  }
                />
              </Field>
            </div>
          </details>
          <div className="actions">
            {filtering && (
              <button
                type="button"
                className="btn quiet"
                onClick={() => setParams(new URLSearchParams())}
              >
                Clear filters
              </button>
            )}
            <button
              type="button"
              className="btn"
              disabled={resource.loading}
              onClick={refresh}
            >
              <RefreshCw size={16} strokeWidth={2.5} />
              Refresh
            </button>
          </div>
        </div>
      </form>
      <ErrorNote error={resource.error} retry={resource.refresh} />
      {resource.loading && !data ? (
        <Loading label="Loading logs" />
      ) : (
        data &&
        !resource.error && (
          <>
            {data.entries.length ? (
              <ol className="log">
                {data.entries.map((entry) => (
                  <li className="log-entry" key={entry.id}>
                    <time
                      className="slot-time"
                      dateTime={new Date(entry.last_ts * 1000).toISOString()}
                      title={new Date(entry.last_ts * 1000).toLocaleString()}
                    >
                      {clock(entry.last_ts)}
                    </time>
                    <div className="log-body">
                      <div className="log-head">
                        {entry.title && <h2>{entry.title}</h2>}
                        {tones[entry.severity] && (
                          <span className={`flag ${tones[entry.severity]}`}>
                            {severities[entry.severity]}
                          </span>
                        )}
                      </div>
                      <p>
                        {entry.summary}
                        {(!entry.summary
                          .toLowerCase()
                          .startsWith(entry.category) ||
                          entry.repeats > 1) && (
                          <span className="meta">
                            {" "}
                            {[
                              !entry.summary
                                .toLowerCase()
                                .startsWith(entry.category) &&
                                categories[entry.category],
                              entry.repeats > 1 && `${entry.repeats} times`,
                            ]
                              .filter(Boolean)
                              .join(" · ")}
                          </span>
                        )}
                      </p>
                      {(entry.repeats > 1 ||
                        entry.job_id ||
                        (user.role === "admin" && entry.detail)) && (
                        <details className="disclosure log-detail">
                          <summary>Details</summary>
                          <dl>
                            <dt>First recorded</dt>
                            <dd>
                              {new Date(entry.first_ts * 1000).toLocaleString()}
                            </dd>
                            <dt>Last recorded</dt>
                            <dd>
                              {new Date(entry.last_ts * 1000).toLocaleString()}
                            </dd>
                            {entry.job_id && (
                              <>
                                <dt>Request</dt>
                                <dd>{entry.job_id}</dd>
                              </>
                            )}
                            {Object.entries(entry.detail || {})
                              .filter(([, value]) => value)
                              .map(([key, value]) => (
                                <div key={key}>
                                  <dt>{key}</dt>
                                  <dd>{value}</dd>
                                </div>
                              ))}
                          </dl>
                        </details>
                      )}
                    </div>
                    {entry.action && (
                      <Link className="link log-action" to={entry.action.href}>
                        {entry.action.label}
                        <ArrowRight size={15} strokeWidth={2.5} />
                      </Link>
                    )}
                  </li>
                ))}
              </ol>
            ) : (
              <Empty
                title={filtering ? "Nothing matches." : "Nothing logged yet."}
                action={
                  filtering ? (
                    <button
                      className="btn"
                      onClick={() => setParams(new URLSearchParams())}
                    >
                      Clear filters
                    </button>
                  ) : undefined
                }
              />
            )}
            {(data.total > 0 || data.page > 1) && (
              <nav className="pages" aria-label="Log pages">
                <button
                  className="btn"
                  disabled={data.page <= 1 || resource.loading}
                  onClick={() => page(data.page - 1)}
                >
                  <ArrowLeft size={16} strokeWidth={2.5} />
                  Previous
                </button>
                <span role="status" className="num">
                  Page {data.page} of{" "}
                  {Math.max(1, Math.ceil(data.total / data.limit))} ·{" "}
                  {data.total} events
                </span>
                <button
                  className="btn"
                  disabled={
                    data.page * data.limit >= data.total || resource.loading
                  }
                  onClick={() => page(data.page + 1)}
                >
                  Next
                  <ArrowRight size={16} strokeWidth={2.5} />
                </button>
              </nav>
            )}
          </>
        )
      )}
    </>
  );
}
