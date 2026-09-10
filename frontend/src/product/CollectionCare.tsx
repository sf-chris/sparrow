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
  return (
    <Section
      title="Collection care"
      description="Your subscriptions decide what Sparrow may keep up to date. Existing copies are preserved when you opt into upgrades."
    >
      <ErrorNote error={error || resource.error} retry={resource.refresh} />
      {!resource.data ? (
        resource.loading ? (
          <Loading label="Opening collection care…" />
        ) : null
      ) : rows.length > 0 ? (
        <div className="sp-panel">
          {rows.map((row) => (
            <div className="sp-row" key={row.id}>
              <div>
                <h3>
                  {row.data.title || title || "Saved title"} ·{" "}
                  {row.data.enabled ? "Following" : "Paused"}
                </h3>
                <p>{row.data.message}</p>
                <p>
                  {row.data.mandate.mode === "keep_current"
                    ? "New episodes from when you followed"
                    : row.data.mandate.mode === "backfill"
                      ? "All aired episodes"
                      : row.data.mandate.mode === "seasons"
                        ? `Seasons ${row.data.mandate.seasons.join(", ")}`
                        : "Only explicitly requested items"}
                  {row.data.upgrades ? " · Quality upgrades enabled" : ""}
                </p>
              </div>
              <div className="sp-actions">
                <button
                  className="sp-button secondary"
                  onClick={() => edit(row)}
                >
                  Edit care
                </button>
                {row.data.enabled && (
                  <button
                    className="sp-button quiet"
                    onClick={() => void check(row)}
                  >
                    Check now
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      ) : (
        <p className="sp-muted">
          {tmdbId
            ? "Follow this title to choose its future care."
            : "Follow a title from its page, or enable monitoring when you request episodes."}
        </p>
      )}
      {tmdbId && resource.data && !rows.length && (
        <button className="sp-button secondary" onClick={() => edit(null)}>
          {mediaType === "tv" ? "Follow this show" : "Manage this movie"}
        </button>
      )}
      {open && (
        <Dialog
          title={`Care for ${editing?.data.title || title || "this title"}`}
          onClose={() => setOpen(false)}
          footer={
            <button
              className="sp-button primary"
              disabled={busy}
              onClick={save}
            >
              {busy ? "Saving…" : "Save collection care"}
            </button>
          }
        >
          <div className="sp-form">
            <ErrorNote error={error} />
            <label className="sp-checkbox">
              <input
                type="checkbox"
                checked={enabled}
                onChange={(e) => setEnabled(e.target.checked)}
              />
              Enable automatic care
            </label>
            {(editing?.media_type || mediaType) === "tv" && (
              <>
                <Field
                  label="Episode scope"
                  hint={
                    mode === "backfill"
                      ? "Includes missing episodes from older seasons."
                      : mode === "keep_current"
                        ? "Only episodes that air after you start following."
                        : undefined
                  }
                >
                  <select
                    value={mode}
                    onChange={(e) => setMode(e.target.value)}
                  >
                    <option value="exact">Requested episodes only</option>
                    <option value="keep_current">New episodes</option>
                    <option value="seasons">Selected seasons</option>
                    <option value="backfill">All aired episodes</option>
                  </select>
                </Field>
                {mode === "seasons" && (
                  <Field
                    label="Season numbers"
                    hint="Separate season numbers with commas, for example 1, 2."
                  >
                    <input
                      value={seasons}
                      onChange={(e) => setSeasons(e.target.value)}
                    />
                  </Field>
                )}
              </>
            )}
            <Field label="Storage destination">
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
            <label className="sp-checkbox">
              <input
                type="checkbox"
                checked={upgrades}
                onChange={(e) => setUpgrades(e.target.checked)}
              />
              Look for better picture quality up to my preferred quality
            </label>
            <p className="sp-muted">
              Saving applies your current preferences to future care. Existing
              requests keep their saved preferences. Pausing care stops new
              automatic requests; control existing work in{" "}
              <Link to="/activity">Activity</Link>.
            </p>
          </div>
        </Dialog>
      )}
    </Section>
  );
}
