import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, post, type NodeInfo } from "./api";
import { Bar, Dialog, ErrorNote, Field, Loading, useResource } from "./ui";
type Subscription = {
  id: string;
  media_type: "tv" | "movie";
  tmdb_id: number;
  node_id: string;
  data: {
    enabled: boolean;
    upgrades: boolean;
    title?: string;
    message: string;
    mandate: {
      mode: string;
      seasons: number[];
      requested_episodes: Record<string, number[]>;
    };
  };
};
const reach = (row: Subscription) =>
  row.data.mandate.mode === "keep_current"
    ? "New episodes as they air"
    : row.data.mandate.mode === "backfill"
      ? "Every aired episode"
      : row.data.mandate.mode === "seasons"
        ? `Seasons ${row.data.mandate.seasons.join(", ")}`
        : "Only what you requested";

/** Following: what Sparrow may keep up to date for a title. */
export default function CollectionCare({
  mediaType,
  tmdbId,
  title,
  titles = {},
}: {
  mediaType?: "tv" | "movie";
  tmdbId?: number;
  title?: string;
  titles?: Record<string, string>;
}) {
  const resource = useResource(() => api<Subscription[]>("/subscriptions"));
  const nodes = useResource(() => api<NodeInfo[]>("/nodes"));
  const [open, setOpen] = useState(false),
    [editing, setEditing] = useState<Subscription | null>(null),
    [mode, setMode] = useState("keep_current"),
    [enabled, setEnabled] = useState(true),
    [upgrades, setUpgrades] = useState(false),
    [node, setNode] = useState("local"),
    [seasons, setSeasons] = useState(""),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [checked, setChecked] = useState("");
  useEffect(() => {
    const timer = setInterval(() => void resource.refresh(), 30000);
    return () => clearInterval(timer);
  }, []);
  const rows =
    resource.data?.filter(
      (r) => !tmdbId || (r.tmdb_id === tmdbId && r.media_type === mediaType),
    ) || [];
  const name = (row: Subscription | null) =>
    row?.data.title ||
    (row && titles[`${row.media_type}:${row.tmdb_id}`]) ||
    title ||
    "this title";
  function edit(row: Subscription | null) {
    setEditing(row);
    setMode(
      row?.data.mandate.mode || (mediaType === "tv" ? "keep_current" : "exact"),
    );
    setEnabled(row?.data.enabled ?? true);
    setUpgrades(row?.data.upgrades ?? false);
    setNode(row?.node_id || nodes.data?.[0]?.id || "local");
    setSeasons(row?.data.mandate.seasons.join(", ") || "");
    setError("");
    setOpen(true);
  }
  async function save() {
    setBusy(true);
    setError("");
    try {
      await api("/subscriptions", {
        method: "PUT",
        body: JSON.stringify({
          tmdb_id: editing?.tmdb_id || tmdbId,
          media_type: editing?.media_type || mediaType,
          node_id: node,
          mode,
          enabled,
          upgrades,
          seasons: seasons
            .split(",")
            .filter((s) => s.trim())
            .map(Number),
        }),
      });
      setOpen(false);
      await resource.refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function check(row: Subscription) {
    try {
      await post(`/subscriptions/${row.id}/check`);
      setChecked(row.id);
      await resource.refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  }
  const controls = (row: Subscription) => (
    <div className="actions">
      <button className="btn" onClick={() => edit(row)}>
        Edit
      </button>
      {row.data.enabled && (
        <button className="btn quiet" onClick={() => void check(row)}>
          {checked === row.id ? "Check started" : "Check now"}
        </button>
      )}
    </div>
  );
  const dialog = open && (
    <Dialog
      title={`Follow ${name(editing)}`}
      onClose={() => setOpen(false)}
      footer={
        <button className="btn primary" disabled={busy} onClick={save}>
          {busy ? "Saving…" : "Save"}
        </button>
      }
    >
      <div className="form">
        <ErrorNote error={error} />
        <label className="check">
          <input
            type="checkbox"
            checked={enabled}
            onChange={(e) => setEnabled(e.target.checked)}
          />
          Keep it up to date
        </label>
        {(editing?.media_type || mediaType) === "tv" && (
          <>
            <Field
              label="Which episodes"
              hint={
                mode === "backfill"
                  ? "Includes missing episodes from older seasons."
                  : mode === "keep_current"
                    ? "Only episodes that air after you follow."
                    : undefined
              }
            >
              <select value={mode} onChange={(e) => setMode(e.target.value)}>
                <option value="exact">Only what I request</option>
                <option value="keep_current">New episodes</option>
                <option value="seasons">Chosen seasons</option>
                <option value="backfill">Every aired episode</option>
              </select>
            </Field>
            {mode === "seasons" && (
              <Field label="Seasons" hint="Separate with commas, like 1, 2.">
                <input
                  value={seasons}
                  onChange={(e) => setSeasons(e.target.value)}
                />
              </Field>
            )}
          </>
        )}
        <Field label="Store on">
          <select
            disabled={!!editing}
            value={node}
            onChange={(e) => setNode(e.target.value)}
          >
            {nodes.data
              ?.filter((n) => !n.disabled)
              .map((n) => (
                <option value={n.id} key={n.id}>
                  {n.name}
                </option>
              ))}
          </select>
        </Field>
        <label className="check">
          <input
            type="checkbox"
            checked={upgrades}
            onChange={(e) => setUpgrades(e.target.checked)}
          />
          Upgrade to my preferred quality
        </label>
        <p className="muted">
          {enabled ? (
            "A copy is only replaced once a better one has been checked."
          ) : (
            <>
              Open requests keep going. Cancel them in{" "}
              <Link to="/activity">Requests</Link>.
            </>
          )}
        </p>
      </div>
    </Dialog>
  );
  if (tmdbId)
    return (
      <div className="care">
        <ErrorNote error={error || resource.error} retry={resource.refresh} />
        {!resource.data ? null : rows.length ? (
          rows.map((row) => (
            <div className="care-line" key={row.id}>
              <p>
                <strong>
                  {row.data.enabled ? "Following" : "Following paused"}
                </strong>
                <span>
                  {reach(row)}
                  {row.data.upgrades ? " · upgrades on" : ""}
                </span>
              </p>
              {controls(row)}
            </div>
          ))
        ) : (
          <button className="btn quiet care-follow" onClick={() => edit(null)}>
            {mediaType === "tv" ? "Follow this series" : "Follow this film"}
          </button>
        )}
        {dialog}
      </div>
    );
  return (
    <section className="following" aria-labelledby="following">
      <Bar id="following" title="Following" />
      <ErrorNote error={error || resource.error} retry={resource.refresh} />
      {!resource.data ? (
        resource.loading && <Loading label="Loading" />
      ) : rows.length ? (
        <ul className="rows">
          {rows.map((row) => (
            <li className="row" key={row.id}>
              <div>
                <h3>
                  <Link to={`/title/${row.media_type}/${row.tmdb_id}`}>
                    {name(row)}
                  </Link>
                  {!row.data.enabled && <span className="flag">Paused</span>}
                </h3>
                <p>
                  {reach(row)}
                  {row.data.upgrades ? " · upgrades on" : ""}
                </p>
                {row.data.message && !row.data.message.startsWith("Saved.") && (
                  <p className="meta">{row.data.message}</p>
                )}
              </div>
              {controls(row)}
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted following-empty">
          Follow a series to get new episodes as they air.
        </p>
      )}
      {dialog}
    </section>
  );
}
