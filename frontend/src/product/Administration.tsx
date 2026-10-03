import { useState } from "react";
import { Check, Copy, Plus, Scissors } from "lucide-react";
import {
  api,
  patch,
  post,
  type User,
  type Preferences,
  type Policy,
  type NodeInfo,
} from "./api";
import {
  Dialog,
  ErrorNote,
  Field,
  Loading,
  Page,
  Section,
  useResource,
} from "./ui";
import { Tick } from "./Brand";
import SubtitleProviderSettings from "./SubtitleProviderSettings";
import { PreferenceFields } from "./Preferences";
import { KEYS, KeyField } from "./Keys";
import DownloadApp from "./DownloadApp";

export function Defaults() {
  const resource = useResource(() =>
    api<{ defaults: Preferences; policy: Policy }>("/admin/defaults"),
  );
  const [draft, setDraft] = useState<Partial<Preferences>>({});
  const [policy, setPolicy] = useState<Partial<Policy>>({});
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);
  async function save() {
    setBusy(true);
    setError("");
    try {
      await api("/admin/defaults", {
        method: "PUT",
        body: JSON.stringify({
          defaults: { ...resource.data!.defaults, ...draft },
          policy: { ...resource.data!.policy, ...policy },
        }),
      });
      setSaved(true);
      await resource.refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Page
      title="Defaults"
      lede="Everyone starts with these and can change their own."
    >
      <ErrorNote error={error || resource.error} retry={resource.refresh} />
      {resource.data ? (
        <>
          <PreferenceFields
            values={{ ...resource.data.defaults, ...draft }}
            onChange={(key, value) => {
              setDraft({ ...draft, [key]: value });
              setSaved(false);
            }}
          />
          <Section
            title="Server limits"
            description="No one can go past these, including Sparrow."
          >
            <div className="form-grid">
              <Field label="Highest quality">
                <select
                  value={policy.max_quality || resource.data.policy.max_quality}
                  onChange={(e) =>
                    setPolicy({ ...policy, max_quality: e.target.value })
                  }
                >
                  {["480p", "720p", "1080p", "2160p"].map((q) => (
                    <option key={q}>{q}</option>
                  ))}
                </select>
              </Field>
              <Field label="File size limit (GB)" hint="0 means no limit.">
                <input
                  type="number"
                  min={0}
                  max={1000}
                  value={
                    policy.max_file_size_gb ??
                    resource.data.policy.max_file_size_gb
                  }
                  onChange={(e) =>
                    setPolicy({
                      ...policy,
                      max_file_size_gb: Number(e.target.value),
                    })
                  }
                />
              </Field>
              <Field
                label="Agent steps per turn"
                hint="An agent stops and waits after this many."
              >
                <input
                  type="number"
                  min={1}
                  max={200}
                  value={
                    policy.max_agent_calls ??
                    resource.data.policy.max_agent_calls
                  }
                  onChange={(e) =>
                    setPolicy({
                      ...policy,
                      max_agent_calls: Number(e.target.value),
                    })
                  }
                />
              </Field>
              <Field
                label="Spending limit (USD)"
                hint="Per request. Each followed title gets one limit in total. Raise it if following stops."
              >
                <input
                  type="number"
                  min={0.01}
                  max={100}
                  step={0.25}
                  value={
                    policy.max_agent_dollars ??
                    resource.data.policy.max_agent_dollars
                  }
                  onChange={(e) =>
                    setPolicy({
                      ...policy,
                      max_agent_dollars: Number(e.target.value),
                    })
                  }
                />
              </Field>
            </div>
          </Section>
          <div className="savebar">
            <span className="done-note" role="status">
              {saved && (
                <>
                  <Tick /> Saved
                </>
              )}
            </span>
            <button className="btn primary" disabled={busy} onClick={save}>
              {busy ? "Saving…" : "Save defaults"}
            </button>
          </div>
        </>
      ) : (
        resource.loading && <Loading label="Loading defaults" />
      )}
    </Page>
  );
}

export function ServerSettings() {
  const resource = useResource(() => api<Record<string, any>>("/admin/config"));
  const [draft, setDraft] = useState<Record<string, any>>({});
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);
  const values = { ...resource.data, ...draft };
  const set = (key: string, value: unknown) => {
    setDraft({ ...draft, [key]: value });
    setSaved(false);
  };
  async function save() {
    setBusy(true);
    setError("");
    try {
      await patch("/admin/config", draft);
      setDraft({});
      setSaved(true);
      await resource.refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Page title="Connections">
      <ErrorNote error={error || resource.error} retry={resource.refresh} />
      {resource.data ? (
        <div className="form">
          <Section
            title="Keys"
            description="Kept on this server and never shown again."
          >
            <div className="form-grid keys-grid">
              {KEYS.map((spec) => (
                <KeyField
                  key={spec.name}
                  spec={spec}
                  saved={!!resource.data![`${spec.name}_configured`]}
                  value={draft[spec.name] || ""}
                  onChange={(value) => set(spec.name, value)}
                />
              ))}
            </div>
          </Section>
          <Section title="Models">
            <div className="form-grid">
              <Field label="Search source" hint="Only one source is available.">
                <select
                  value={values.preferred_search_engines?.[0] || "apibay"}
                  onChange={(e) =>
                    set("preferred_search_engines", [e.target.value])
                  }
                >
                  <option value="apibay">Built-in source</option>
                  {values.preferred_search_engines?.[0] &&
                    values.preferred_search_engines[0] !== "apibay" && (
                      <option value={values.preferred_search_engines[0]} disabled>
                        Previous source (unavailable)
                      </option>
                    )}
                </select>
              </Field>
              <Field label="Everyday model">
                <input
                  value={values.cheap_model || ""}
                  onChange={(e) => set("cheap_model", e.target.value)}
                />
              </Field>
              <Field label="Hard-case model">
                <input
                  value={values.smart_model || ""}
                  onChange={(e) => set("smart_model", e.target.value)}
                />
              </Field>
            </div>
          </Section>
          <SubtitleProviderSettings />
          <Section
            title="Download app"
            description="On this server. A paired Windows machine sets its own in Sparrow Node."
          >
            <DownloadApp />
            <div className="form-grid download-limits">
              <Field label="Downloads at once">
                <input
                  type="number"
                  min={1}
                  max={50}
                  value={values.max_active_transfers || 1}
                  onChange={(e) =>
                    set("max_active_transfers", Number(e.target.value))
                  }
                />
              </Field>
              <Field label="Seeding ratio limit" hint="0 means no limit.">
                <input
                  type="number"
                  min={0}
                  step={0.1}
                  value={values.seeding_ratio_limit ?? 2}
                  onChange={(e) =>
                    set("seeding_ratio_limit", Number(e.target.value))
                  }
                />
              </Field>
              <Field label="Seeding time limit (hours)" hint="0 means no limit.">
                <input
                  type="number"
                  min={0}
                  value={values.seeding_time_hours ?? 0}
                  onChange={(e) =>
                    set("seeding_time_hours", Number(e.target.value))
                  }
                />
              </Field>
            </div>
          </Section>
          <div className="savebar">
            <span className="done-note" role="status">
              {saved && (
                <>
                  <Tick /> Saved
                </>
              )}
            </span>
            <button className="btn primary" disabled={busy} onClick={save}>
              {busy ? "Saving…" : "Save"}
            </button>
          </div>
        </div>
      ) : (
        resource.loading && <Loading label="Loading connections" />
      )}
    </Page>
  );
}

export function People({ currentUser }: { currentUser: User }) {
  const resource = useResource(() => api<User[]>("/admin/users"));
  const nodes = useResource(() => api<NodeInfo[]>("/nodes"));
  const [editing, setEditing] = useState<User | null>(null);
  const [dialog, setDialog] = useState(false);
  const [role, setRole] = useState("viewer");
  const [scope, setScope] = useState("all");
  const [invite, setInvite] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  async function create() {
    setBusy(true);
    setError("");
    try {
      const result = await post<{ path: string }>("/admin/invitations", {
        role,
        library_scope:
          scope === "all"
            ? null
            : scope === "none"
              ? []
              : scope === "keep"
                ? editing?.library_scope
                : [scope],
      });
      setInvite(location.origin + result.path);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function saveAccess() {
    if (!editing) return;
    setBusy(true);
    setError("");
    try {
      await patch(`/admin/users/${editing.id}`, {
        role,
        library_scope:
          scope === "all"
            ? null
            : scope === "none"
              ? []
              : scope === "keep"
                ? editing?.library_scope
                : [scope],
        disabled: editing.disabled,
      });
      setEditing(null);
      await resource.refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function disable(user: User) {
    try {
      await patch(`/admin/users/${user.id}`, {
        role: user.role,
        library_scope: user.library_scope,
        disabled: !user.disabled,
      });
      await resource.refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  }
  const roleName = (value: string) =>
    value === "requester"
      ? "Watch and request"
      : value === "admin"
        ? "Admin"
        : "Watch only";
  return (
    <Page
      title="People"
      action={
        <button
          className="btn primary"
          onClick={() => {
            setDialog(true);
            setInvite("");
            setCopied(false);
          }}
        >
          <Plus size={18} strokeWidth={2.5} />
          Invite someone
        </button>
      }
    >
      <ErrorNote error={error || resource.error} retry={resource.refresh} />
      {resource.data ? (
        <ul className="cast">
          {resource.data.map((user) => (
            <li
              className={`cast-member ${user.disabled ? "off" : ""}`}
              key={user.id}
            >
              <div className="cast-line">
                <span className="cast-name">
                  {user.name}
                  <span className="meta">Signs in as {user.username}</span>
                </span>
                <span className="leader" aria-hidden="true" />
                <span className="cast-role">
                  {user.id === currentUser.id
                    ? `${roleName(user.role)} · you`
                    : roleName(user.role)}
                </span>
                {user.disabled && (
                  <span className="flag problem">No access</span>
                )}
              </div>
              {user.id !== currentUser.id && (
                <div className="actions">
                  <button
                    className="btn"
                    onClick={() => {
                      setEditing(user);
                      setRole(user.role);
                      setScope(
                        user.library_scope === null
                          ? "all"
                          : !user.library_scope.length
                            ? "none"
                            : user.library_scope.length > 1
                              ? "keep"
                              : user.library_scope[0],
                      );
                      setError("");
                    }}
                  >
                    Edit access
                  </button>
                  <button className="btn quiet" onClick={() => disable(user)}>
                    {user.disabled ? "Restore access" : "Disable access"}
                  </button>
                </div>
              )}
            </li>
          ))}
        </ul>
      ) : (
        resource.loading && <Loading label="Loading people" />
      )}
      {editing && (
        <Dialog
          title={`Access for ${editing.name}`}
          onClose={() => setEditing(null)}
          footer={
            <button
              className="btn primary"
              disabled={busy}
              onClick={saveAccess}
            >
              Save access
            </button>
          }
        >
          <div className="form">
            <ErrorNote error={error} />
            <Field label="Role">
              <select value={role} onChange={(e) => setRole(e.target.value)}>
                <option value="viewer">Watch only</option>
                <option value="requester">Watch and request</option>
                <option value="admin">Admin</option>
              </select>
            </Field>
            <Field label="Titles from">
              <select value={scope} onChange={(e) => setScope(e.target.value)}>
                <option value="all">All storage, including new</option>
                <option value="none">No storage</option>
                {editing &&
                  editing.library_scope &&
                  editing.library_scope.length > 1 && (
                    <option value="keep">Current storage (no change)</option>
                  )}
                {nodes.data?.map((n) => (
                  <option key={n.id} value={n.id}>
                    {n.name}
                  </option>
                ))}
              </select>
            </Field>
            <p className="muted">Saving signs them out everywhere.</p>
          </div>
        </Dialog>
      )}
      {dialog && (
        <Dialog title="Invite someone" onClose={() => setDialog(false)}>
          <div className="form">
            <ErrorNote error={error} />
            {invite ? (
              <div className="coupon ticket">
                <Scissors
                  className="coupon-cut"
                  size={18}
                  strokeWidth={2}
                  aria-hidden="true"
                />
                <Field
                  label="Invitation link"
                  hint="Works once, for 7 days. Send it privately."
                >
                  <input
                    readOnly
                    value={invite}
                    onFocus={(e) => e.target.select()}
                  />
                </Field>
                <button
                  className="btn primary"
                  onClick={async () => {
                    try {
                      await navigator.clipboard.writeText(invite);
                      setCopied(true);
                    } catch {
                      setError("Couldn’t copy. Select the link and copy it.");
                    }
                  }}
                >
                  {copied ? (
                    <Check size={18} strokeWidth={2.5} />
                  ) : (
                    <Copy size={18} strokeWidth={2.25} />
                  )}
                  {copied ? "Copied" : "Copy link"}
                </button>
              </div>
            ) : (
              <>
                <Field label="Role">
                  <select
                    value={role}
                    onChange={(e) => setRole(e.target.value)}
                  >
                    <option value="viewer">Watch only</option>
                    <option value="requester">Watch and request</option>
                    <option value="admin">Admin</option>
                  </select>
                </Field>
                <Field label="Titles from">
                  <select
                    value={scope}
                    onChange={(e) => setScope(e.target.value)}
                  >
                    <option value="all">All storage, including new</option>
                    {nodes.data?.map((node) => (
                      <option key={node.id} value={node.id}>
                        {node.name}
                      </option>
                    ))}
                  </select>
                </Field>
                <button
                  className="btn primary"
                  disabled={busy}
                  onClick={create}
                >
                  {busy ? "Creating…" : "Create link"}
                </button>
              </>
            )}
          </div>
        </Dialog>
      )}
    </Page>
  );
}
