import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, post, type NodeInfo } from "./api";
import { Dialog, ErrorNote, Field, Loading, Section, useResource } from "./ui";
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
export default function CollectionCare({
  mediaType,
  tmdbId,
  title,
}: {
  mediaType?: "tv" | "movie";
  tmdbId?: number;
  title?: string;
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
    [busy, setBusy] = useState(false);
  useEffect(() => {
    const timer = setInterval(() => void resource.refresh(), 30000);
    return () => clearInterval(timer);
  }, []);
  const rows =
    resource.data?.filter(
      (r) => !tmdbId || (r.tmdb_id === tmdbId && r.media_type === mediaType),
    ) || [];
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
      await resource.refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  }
  const describe = (row: Subscription) =>
    row.data.mandate.mode === "keep_current"
      ? "New episodes"
      : row.data.mandate.mode === "backfill"
        ? "All aired episodes"
        : row.data.mandate.mode === "seasons"
          ? `Seasons ${row.data.mandate.seasons.join(", ")}`
          : "Requested episodes only";
  return (
    <Section
      className="sp-care"
      title={tmdbId ? "Keep up to date" : "Followed titles"}
      description={
        tmdbId
          ? undefined
          : "Sparrow keeps these up to date, and can upgrade their picture if you allow it."
      }
      action={
        tmdbId && resource.data && !rows.length ? (
          <button className="sp-btn sp-btn-line" onClick={() => edit(null)}>
            {mediaType === "tv" ? "Follow this series" : "Follow this film"}
          </button>
        ) : undefined
      }
    >
      <ErrorNote error={error || resource.error} retry={resource.refresh} />
      {!resource.data ? (
        resource.loading ? (
          <Loading />
        ) : null
      ) : rows.length > 0 ? (
        <ul className="sp-rows sp-care-rows">
          {rows.map((row) => (
            <li className="sp-row" key={row.id}>
              <div>
                <p className="sp-care-state">
                  <span className={`sp-status ${row.data.enabled ? "is-ok" : "is-quiet"}`}>
                    {row.data.enabled ? "Following" : "Paused"}
                  </span>
                  <span className="sp-label">
                    {describe(row)}
                    {row.data.upgrades ? " · Upgrades on" : ""}
                  </span>
                </p>
                <h3>{row.data.title || title || "Saved title"}</h3>
                {row.data.message && <p>{row.data.message}</p>}
              </div>
              <div className="sp-actions">
                {row.data.enabled && (
                  <button className="sp-btn sp-btn-ghost sp-btn-small" onClick={() => void check(row)}>
                    Check now
                  </button>
                )}
                <button className="sp-btn sp-btn-line sp-btn-small" onClick={() => edit(row)}>
                  Edit
                </button>
              </div>
            </li>
          ))}
        </ul>
      ) : (
        !tmdbId && <p className="sp-hint">Follow a series from its page to get new episodes automatically.</p>
      )}
      {open && (
        <Dialog
          title={`Follow ${editing?.data.title || title || "this title"}`}
          onClose={() => setOpen(false)}
          footer={
            <button className="sp-btn sp-btn-solid" disabled={busy} onClick={save}>
              {busy ? "Saving…" : "Save"}
            </button>
          }
        >
          <div className="sp-form">
            <ErrorNote error={error} />
            <label className="sp-check">
              <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
              Follow this title
            </label>
            {(editing?.media_type || mediaType) === "tv" && (
              <>
                <Field
                  label="Which episodes"
                  hint={
                    mode === "backfill"
                      ? "Includes missing episodes from older seasons."
                      : mode === "keep_current"
                        ? "Episodes that air from now on."
                        : undefined
                  }
                >
                  <select value={mode} onChange={(e) => setMode(e.target.value)}>
                    <option value="exact">Requested episodes only</option>
                    <option value="keep_current">New episodes</option>
                    <option value="seasons">Selected seasons</option>
                    <option value="backfill">All aired episodes</option>
                  </select>
                </Field>
                {mode === "seasons" && (
                  <Field label="Seasons" hint="For example: 1, 2">
                    <input value={seasons} onChange={(e) => setSeasons(e.target.value)} />
                  </Field>
                )}
              </>
            )}
            <Field label="Store on">
              <select disabled={!!editing} value={node} onChange={(e) => setNode(e.target.value)}>
                {nodes.data
                  ?.filter((n) => !n.disabled)
                  .map((n) => (
                    <option value={n.id} key={n.id}>
                      {n.name}
                    </option>
                  ))}
              </select>
            </Field>
            <label className="sp-check">
              <input type="checkbox" checked={upgrades} onChange={(e) => setUpgrades(e.target.checked)} />
              Upgrade to my preferred quality when possible
            </label>
            <p className="sp-hint">
              Changes only affect new downloads. Manage running ones in{" "}
              <Link className="sp-link" to="/activity">
                Requests
              </Link>
              .
            </p>
          </div>
        </Dialog>
      )}
    </Section>
  );
}
