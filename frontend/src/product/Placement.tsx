import { useState } from "react";
import { Plus } from "lucide-react";
import { api, post } from "./api";
import { Bar, Dialog, ErrorNote, Field, useResource } from "./ui";
import {
  placeName,
  places,
  rules,
  useDestinations,
  type Destinations,
  type Rule,
} from "./StoragePicker";

type PoolRow = { id: string; name: string; rule: Rule; members: string[] };
type Move = {
  id: string;
  title: string;
  to: string;
  state: string;
  message: string;
  updated: number;
};

const ruleName = (rule: Rule) => rules.find(([r]) => r === rule)?.[1] || "";

/** Where new titles go when nobody chooses: the rule, pools and moves. */
export default function Placement() {
  const destinations = useDestinations();
  const pools = useResource(() => api<PoolRow[]>("/admin/storage/pools"));
  const moves = useResource(() => api<Move[]>("/admin/moves"));
  const [editing, setEditing] = useState<PoolRow | "new" | null>(null);
  const [error, setError] = useState("");
  if (places(destinations.data).count < 2) return null;
  async function setRule(rule: string) {
    setError("");
    try {
      await api("/admin/storage/rule", {
        method: "PUT",
        body: JSON.stringify({ rule }),
      });
      await destinations.refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  }
  const recent = (moves.data || []).filter(
    (m) => m.state !== "done" || Date.now() / 1000 - m.updated < 7 * 86400,
  );
  return (
    <section aria-labelledby="new-titles" className="placement">
      <Bar id="new-titles" title="New titles" />
      <ErrorNote error={error || pools.error} />
      <div className="form-grid">
        <Field
          label="Automatic picks"
          hint="A show stays where it started unless that disk is full."
        >
          <select
            value={destinations.data?.rule}
            onChange={(e) => void setRule(e.target.value)}
          >
            {rules.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </Field>
      </div>
      <ul className="folder-rows pool-rows">
        {pools.data?.map((pool) => (
          <li key={pool.id}>
            <span className="folder-label">{pool.name}</span>
            <span className="path">
              {pool.members
                .map((m) => placeName(destinations.data, m))
                .join(", ")}
            </span>
            <span className="leader" aria-hidden="true" />
            <span className="folder-state quiet">{ruleName(pool.rule)}</span>
            <button className="link" onClick={() => setEditing(pool)}>
              Edit
            </button>
          </li>
        ))}
      </ul>
      <div className="actions">
        <button className="btn quiet" onClick={() => setEditing("new")}>
          <Plus size={16} strokeWidth={2.5} />
          Pool
        </button>
      </div>
      {recent.length > 0 && (
        <>
          <Bar id="moves" title="Moves" />
          <ul className="folder-rows">
            {recent.map((m) => (
              <li key={m.id}>
                <span className="folder-label">{m.title}</span>
                <span className="path">{m.message}</span>
                <span className="leader" aria-hidden="true" />
                <span className="folder-state quiet">
                  {m.state === "done" ? "Moved" : "Moving"}
                </span>
              </li>
            ))}
          </ul>
        </>
      )}
      {editing && (
        <PoolDialog
          pool={editing === "new" ? null : editing}
          destinations={destinations.data}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            void pools.refresh();
            void destinations.refresh();
          }}
        />
      )}
    </section>
  );
}

function PoolDialog({
  pool,
  destinations,
  onClose,
  onSaved,
}: {
  pool: PoolRow | null;
  destinations: Destinations | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [name, setName] = useState(pool?.name || "");
  const [rule, setRule] = useState<Rule>(pool?.rule || "most_free");
  const [members, setMembers] = useState<string[]>(pool?.members || []);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const disks = destinations?.computers.flatMap((c) => c.disks) || [];
  async function save() {
    setBusy(true);
    setError("");
    try {
      const body = { name, rule, members };
      if (pool)
        await api(`/admin/storage/pools/${pool.id}`, {
          method: "PUT",
          body: JSON.stringify(body),
        });
      else await post("/admin/storage/pools", body);
      onSaved();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function remove() {
    if (!pool) return;
    setBusy(true);
    try {
      await api(`/admin/storage/pools/${pool.id}`, { method: "DELETE" });
      onSaved();
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }
  return (
    <Dialog
      title={pool ? pool.name : "New pool"}
      onClose={onClose}
      footer={
        <>
          {pool && (
            <button className="btn quiet" disabled={busy} onClick={remove}>
              Delete
            </button>
          )}
          <button
            className="btn primary"
            disabled={busy || !name.trim() || !members.length}
            onClick={save}
          >
            Save pool
          </button>
        </>
      }
    >
      <div className="form">
        <ErrorNote error={error} />
        <Field label="Name">
          <input
            value={name}
            maxLength={60}
            onChange={(e) => setName(e.target.value)}
            placeholder="Kids’ shows"
          />
        </Field>
        <Field label="Picks by">
          <select
            value={rule}
            onChange={(e) => setRule(e.target.value as Rule)}
          >
            {rules.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </Field>
        <fieldset className="checklist">
          <legend className="checklist-head">
            <span>Disks, in order</span>
          </legend>
          <div className="checklist-rows">
            {[
              ...members
                .map((m) => disks.find((d) => d.value === m))
                .filter((d) => d !== undefined),
              ...disks.filter((d) => !members.includes(d.value)),
            ].map((disk) => (
              <label className="check checklist-row" key={disk.value}>
                <input
                  type="checkbox"
                  checked={members.includes(disk.value)}
                  onChange={(e) =>
                    setMembers(
                      e.target.checked
                        ? [...members, disk.value]
                        : members.filter((m) => m !== disk.value),
                    )
                  }
                />
                <span>{disk.name}</span>
                {!disk.online && <span className="meta">offline</span>}
              </label>
            ))}
          </div>
        </fieldset>
      </div>
    </Dialog>
  );
}
