import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ArrowLeft, ArrowRight, RefreshCw } from "lucide-react";
import { api, type User } from "./api";
import { Empty, ErrorNote, Field, Loading, Page, useResource } from "./ui";

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
const categories = [
  "request",
  "download",
  "import",
  "storage",
  "playback",
  "subtitle",
];
const severities: Record<string, string> = {
  info: "Update",
  success: "Success",
  warning: "Needs attention",
  error: "Error",
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
    <Page
      title="Activity log"
      description={user.role === "admin" ? "Includes everyone on this server." : undefined}
    >
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
        className="sp-log-filters"
        onSubmit={(event) => {
          event.preventDefault();
          change("q", q.trim());
        }}
      >
        <div className="sp-field">
          <label htmlFor="log-search">Title or request ID</label>
          <div className="sp-log-search">
            <input
              id="log-search"
              type="search"
              value={q}
              maxLength={300}
              onChange={(event) => setQ(event.target.value)}
            />
            <button className="sp-btn sp-btn-line">Search</button>
          </div>
        </div>
        <Field label="Category">
          <select
            value={params.get("category") || ""}
            onChange={(event) => change("category", event.target.value)}
          >
            <option value="">All categories</option>
            {categories.map((category) => (
              <option value={category} key={category}>
                {category === "import"
                  ? "Imports"
                  : category.charAt(0).toUpperCase() + category.slice(1)}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Severity">
          <select
            value={params.get("severity") || ""}
            onChange={(event) => change("severity", event.target.value)}
          >
            <option value="">All severities</option>
            {Object.entries(severities).map(([value, label]) => (
              <option value={value} key={value}>
                {label}
              </option>
            ))}
          </select>
        </Field>
        <details
          className="sp-log-time"
          open={params.has("since") || params.has("until")}
        >
          <summary>
            Time range
            {params.has("since") || params.has("until") ? " · filtered" : ""}
          </summary>
          <div>
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
      </form>
      <div className="sp-log-tools">
        <p className="sp-hint">
          Last 90 days. Repeats within five minutes are grouped.
        </p>
        <div className="sp-actions">
          {filtering && (
            <button
              className="sp-btn sp-btn-ghost"
              onClick={() => setParams(new URLSearchParams())}
            >
              Clear filters
            </button>
          )}
          <button
            className="sp-btn sp-btn-line"
            disabled={resource.loading}
            onClick={refresh}
          >
            <RefreshCw size={15} aria-hidden="true" />
            Refresh
          </button>
        </div>
      </div>
      <ErrorNote error={resource.error} retry={resource.refresh} />
      {resource.loading && !data ? (
        <Loading label="Loading logs…" />
      ) : (
        data &&
        !resource.error && (
          <>
            {data.entries.length ? (
              <ol className="sp-log-list">
                {data.entries.map((entry) => (
                  <li className="sp-log-entry" key={entry.id}>
                    <div className="sp-log-meta">
                      <span className={`sp-log-severity ${entry.severity}`}>
                        {severities[entry.severity]}
                      </span>
                      <span className="sp-log-category">{entry.category}</span>
                      <time
                        dateTime={new Date(entry.last_ts * 1000).toISOString()}
                      >
                        {new Date(entry.last_ts * 1000).toLocaleString()}
                      </time>
                    </div>
                    {entry.title && <h2>{entry.title}</h2>}
                    <p>{entry.summary}</p>
                    <div className="sp-log-actions">
                      {entry.action && (
                        <Link
                          className="sp-btn sp-btn-ghost"
                          to={entry.action.href}
                        >
                          {entry.action.label}
                          <ArrowRight size={14} />
                        </Link>
                      )}
                      {entry.repeats > 1 && (
                        <span className="sp-hint">
                          Repeated {entry.repeats} times
                        </span>
                      )}
                    </div>
                    {(entry.repeats > 1 ||
                      entry.job_id ||
                      (user.role === "admin" && entry.detail)) && (
                      <details className="sp-log-detail">
                        <summary>Event details</summary>
                        <dl>
                          <dt>First recorded</dt>
                          <dd>
                            {new Date(entry.first_ts * 1000).toLocaleString()}
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
                  </li>
                ))}
              </ol>
            ) : (
              <Empty
                title={
                  filtering ? "No events match these filters." : "No events yet."
                }
                action={
                  filtering ? (
                    <button
                      className="sp-btn sp-btn-line"
                      onClick={() => setParams(new URLSearchParams())}
                    >
                      Clear filters
                    </button>
                  ) : undefined
                }
              />
            )}
            {(data.total > 0 || data.page > 1) && (
              <nav className="sp-log-pagination" aria-label="Log pages">
                <button
                  className="sp-btn sp-btn-line"
                  disabled={data.page <= 1 || resource.loading}
                  onClick={() => page(data.page - 1)}
                >
                  <ArrowLeft size={15} />
                  Previous
                </button>
                <span role="status">
                  Page {data.page} of{" "}
                  {Math.max(1, Math.ceil(data.total / data.limit))} ·{" "}
                  {data.total} events
                </span>
                <button
                  className="sp-btn sp-btn-line"
                  disabled={
                    data.page * data.limit >= data.total || resource.loading
                  }
                  onClick={() => page(data.page + 1)}
                >
                  Next
                  <ArrowRight size={15} />
                </button>
              </nav>
            )}
          </>
        )
      )}
    </>
  );
}
