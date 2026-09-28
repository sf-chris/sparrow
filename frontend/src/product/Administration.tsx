import { useState } from "react";
import { Link } from "react-router-dom";
import { Check, Copy, Plus, Save, Users, HardDrive } from "lucide-react";
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
  Status,
  bytes,
  useResource,
} from "./ui";
import SubtitleProviderSettings from "./SubtitleProviderSettings";
import { PreferenceFields } from "./Preferences";

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
      title="Household defaults"
      description="Everyone starts with these. People can change their own, within the server limits."
    >
      <ErrorNote error={error || resource.error} retry={resource.refresh} />
      {resource.data ? (
        <>
          <Section title="Default preferences">
            <div className="sp-sheet">
              <PreferenceFields
                values={{ ...resource.data.defaults, ...draft }}
                onChange={(key, value) => {
                  setDraft({ ...draft, [key]: value });
                  setSaved(false);
                }}
              />
            </div>
          </Section>
          <Section
            title="Server limits"
            description="These apply to every request."
          >
            <div className="sp-sheet sp-form-grid">
              <Field label="Maximum picture quality">
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
              <Field
                label="Maximum file size (GB)"
                hint="0 for no limit."
              >
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
              <Field label="Maximum agent steps per wake">
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
                label="Reasoning budget (USD)"
                hint="Per request. Each followed title gets this amount once for all its checks, with no reset. If following stops, raise it and retry."
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
          <div className="sp-savebar">
            <span className="sp-success" role="status">
              {saved ? "Household defaults saved." : ""}
            </span>
            <button
              className="sp-btn sp-btn-solid"
              disabled={busy}
              onClick={save}
            >
              {busy ? "Saving…" : "Save defaults"}
            </button>
          </div>
        </>
      ) : (
        resource.loading && <Loading />
      )}
    </Page>
  );
}

export function ServerSettings({
  setupSection,
  onSaved,
}: {
  setupSection?: "providers" | "downloads";
  onSaved?: () => void | Promise<void>;
} = {}) {
  const resource = useResource(() => api<Record<string, any>>("/admin/config"));
  const [draft, setDraft] = useState<Record<string, any>>({});
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);
  const values = { ...resource.data, ...draft };
  const tc = {
    ...(resource.data?.torrent_client || {}),
    ...(draft.torrent_client || {}),
  };
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
      await onSaved?.();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Page
      embedded={!!setupSection}
      title={
        setupSection === "providers"
          ? "API keys"
          : setupSection === "downloads"
            ? "Download app"
            : "Server settings"
      }
      description={
        setupSection === "downloads"
          ? "Sparrow works with Transmission or qBittorrent. Install one first."
          : undefined
      }
    >
      <ErrorNote error={error || resource.error} retry={resource.refresh} />
      {resource.data ? (
        <div className="sp-form">
          {setupSection !== "downloads" && (
            <Section title="Connections">
              {setupSection && (
                <p className="sp-hint">
                  Get keys from{" "}
                  <a
                    href="https://www.themoviedb.org/settings/api"
                    target="_blank"
                    rel="noreferrer"
                  >
                    TMDB
                  </a>{" "}
                  and the{" "}
                  <a
                    href="https://platform.claude.com/settings/keys"
                    target="_blank"
                    rel="noreferrer"
                  >
                    Claude Console
                  </a>
                  . Keys are stored on this server. Anthropic bills API usage
                  separately.
                </p>
              )}
              <div className="sp-sheet sp-form-grid">
                <Field
                  label="TMDB API key"
                  hint={
                    values.tmdb_api_key_configured
                      ? "Saved. Leave blank to keep it."
                      : "For title and episode information."
                  }
                >
                  <input
                    type="password"
                    autoComplete="off"
                    value={draft.tmdb_api_key || ""}
                    onChange={(e) => set("tmdb_api_key", e.target.value)}
                  />
                </Field>
                <Field
                  label="Anthropic API key"
                  hint={
                    values.anthropic_api_key_configured
                      ? "Saved. Leave blank to keep it."
                      : "For finding and downloading titles. Not needed to watch what you have."
                  }
                >
                  <input
                    type="password"
                    autoComplete="off"
                    value={draft.anthropic_api_key || ""}
                    onChange={(e) => set("anthropic_api_key", e.target.value)}
                  />
                </Field>
                {!setupSection && (
                  <>
                    <Field
                      label="Acquisition source"
                      hint="Only one source is available for now."
                    >
                      <select
                        value={values.preferred_search_engines?.[0] || "apibay"}
                        onChange={(e) =>
                          set("preferred_search_engines", [e.target.value])
                        }
                      >
                        <option value="apibay">Built-in source</option>
                        {values.preferred_search_engines?.[0] &&
                          values.preferred_search_engines[0] !== "apibay" && (
                            <option
                              value={values.preferred_search_engines[0]}
                              disabled
                            >
                              Previous source unavailable
                            </option>
                          )}
                      </select>
                    </Field>
                    <Field label="Routine reasoning model">
                      <input
                        value={values.cheap_model || ""}
                        onChange={(e) => set("cheap_model", e.target.value)}
                      />
                    </Field>
                    <Field label="Difficult-task reasoning model">
                      <input
                        value={values.smart_model || ""}
                        onChange={(e) => set("smart_model", e.target.value)}
                      />
                    </Field>
                  </>
                )}
              </div>
            </Section>
          )}
          {!setupSection && <SubtitleProviderSettings />}
          {setupSection !== "providers" && (
            <Section
              title="Download app on this server"
              description="For a Windows node, set this up in Sparrow Node instead."
            >
              <div className="sp-sheet sp-form-grid">
                <Field label="Download app">
                  <select
                    value={tc.type || "none"}
                    onChange={(e) =>
                      set("torrent_client", {
                        ...tc,
                        type: e.target.value,
                        port: e.target.value === "transmission" ? 9091 : 8080,
                      })
                    }
                  >
                    <option value="none">Not configured</option>
                    <option value="transmission">Transmission</option>
                    <option value="qbittorrent">qBittorrent</option>
                  </select>
                </Field>
                <Field label="Host">
                  <input
                    value={tc.host || "localhost"}
                    onChange={(e) =>
                      set("torrent_client", { ...tc, host: e.target.value })
                    }
                  />
                </Field>
                <Field label="Port">
                  <input
                    type="number"
                    min={1}
                    max={65535}
                    value={tc.port || 8080}
                    onChange={(e) =>
                      set("torrent_client", {
                        ...tc,
                        port: Number(e.target.value),
                      })
                    }
                  />
                </Field>
                <Field label="Username">
                  <input
                    autoComplete="off"
                    value={tc.username || ""}
                    onChange={(e) =>
                      set("torrent_client", { ...tc, username: e.target.value })
                    }
                  />
                </Field>
                <Field
                  label="Password"
                  hint="Leave blank to keep it."
                >
                  <input
                    type="password"
                    autoComplete="off"
                    value={draft.torrent_client?.password || ""}
                    onChange={(e) =>
                      set("torrent_client", { ...tc, password: e.target.value })
                    }
                  />
                </Field>
                <Field label="Concurrent download limit">
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
                <Field
                  label="Seeding ratio limit"
                  hint="0 for no limit."
                >
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
                <Field
                  label="Seeding time limit (hours)"
                  hint="0 for no limit."
                >
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
          )}
          {setupSection === "downloads" && (
            <p className="sp-hint">
              In Docker, localhost means the Sparrow container. Use the
              download machine’s LAN address.
            </p>
          )}
          <div className="sp-savebar">
            <span className="sp-success" role="status">
              {saved ? "Server settings saved." : ""}
            </span>
            <button
              className="sp-btn sp-btn-solid"
              disabled={busy}
              onClick={save}
            >
              {busy
                ? "Saving…"
                : setupSection
                  ? "Save and continue"
                  : "Save settings"}
            </button>
          </div>
        </div>
      ) : (
        resource.loading && <Loading />
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
  return (
    <Page
      title="People"
      action={
        <button
          className="sp-btn sp-btn-solid"
          onClick={() => {
            setDialog(true);
            setInvite("");
          }}
        >
          <Plus size={16} aria-hidden="true" />
          Invite someone
        </button>
      }
    >
      <ErrorNote error={error || resource.error} retry={resource.refresh} />
      {resource.data ? (
        <ol className="sp-cast">
          {resource.data.map((user) => (
            <li className={`sp-cast-member ${user.disabled ? "is-disabled" : ""}`} key={user.id}>
              <span className="sp-avatar" aria-hidden="true">
                {user.name.slice(0, 1).toUpperCase()}
              </span>
              <div>
                <h3>
                  {user.name}
                  {user.id === currentUser.id && <span className="sp-stamp">You</span>}
                </h3>
                <p className="sp-label">
                  {user.username} ·{" "}
                  {user.role === "requester"
                    ? "Watches and requests"
                    : user.role === "admin"
                      ? "Administrator"
                      : "Watches"}
                  {user.library_scope !== null &&
                    ` · ${user.library_scope.length ? `${user.library_scope.length} ${user.library_scope.length === 1 ? "library" : "libraries"}` : "No libraries"}`}
                </p>
                {user.disabled && <span className="sp-status is-bad">Access disabled</span>}
              </div>
              {user.id !== currentUser.id && (
                <div className="sp-actions">
                  <button
                    className="sp-btn sp-btn-line sp-btn-small"
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
                  <button
                    className={`sp-btn sp-btn-small ${user.disabled ? "sp-btn-line" : "sp-btn-ghost"}`}
                    onClick={() => disable(user)}
                  >
                    {user.disabled ? "Restore access" : "Disable access"}
                  </button>
                </div>
              )}
            </li>
          ))}
        </ol>
      ) : (
        resource.loading && <Loading />
      )}
      {editing && (
        <Dialog
          title={`Access for ${editing.name}`}
          onClose={() => setEditing(null)}
          footer={
            <button
              className="sp-btn sp-btn-solid"
              disabled={busy}
              onClick={saveAccess}
            >
              Save access
            </button>
          }
        >
          <div className="sp-form">
            <ErrorNote error={error} />
            <Field label="Role">
              <select value={role} onChange={(e) => setRole(e.target.value)}>
                <option value="viewer">Watch the collection</option>
                <option value="requester">Watch and request titles</option>
                <option value="admin">Manage the server</option>
              </select>
            </Field>
            <Field label="Storage access">
              <select value={scope} onChange={(e) => setScope(e.target.value)}>
                <option value="all">All current and future libraries</option>
                <option value="none">No libraries</option>
                {editing &&
                  editing.library_scope &&
                  editing.library_scope.length > 1 && (
                    <option value="keep">Keep current libraries</option>
                  )}
                {nodes.data?.map((n) => (
                  <option key={n.id} value={n.id}>
                    {n.name}
                  </option>
                ))}
              </select>
            </Field>
            <p className="sp-hint">Saving signs them out so the change applies right away.</p>
          </div>
        </Dialog>
      )}
      {dialog && (
        <Dialog title="Invite someone" onClose={() => setDialog(false)}>
          <div className="sp-form">
            <ErrorNote error={error} />
            {invite ? (
              <>
                <Field label="Invitation link">
                  <input
                    readOnly
                    value={invite}
                    onFocus={(e) => e.target.select()}
                  />
                </Field>
                <button
                  className="sp-btn sp-btn-solid"
                  onClick={async () => {
                    try {
                      await navigator.clipboard.writeText(invite);
                      setCopied(true);
                    } catch {
                      setError(
                        "Couldn’t copy. Select the link and copy it yourself.",
                      );
                    }
                  }}
                >
                  {copied ? <Check size={16} /> : <Copy size={16} />}{" "}
                  {copied ? "Copied" : "Copy invitation"}
                </button>
              </>
            ) : (
              <>
                <p className="sp-hint">The link works once and expires in 7 days.</p>
                <Field label="What can they do?">
                  <select
                    value={role}
                    onChange={(e) => setRole(e.target.value)}
                  >
                    <option value="viewer">Watch the collection</option>
                    <option value="requester">Watch and request titles</option>
                    <option value="admin">Manage the server</option>
                  </select>
                </Field>
                <Field label="Libraries">
                  <select
                    value={scope}
                    onChange={(e) => setScope(e.target.value)}
                  >
                    <option value="all">
                      All current and future libraries
                    </option>
                    {nodes.data?.map((node) => (
                      <option key={node.id} value={node.id}>
                        {node.name}
                      </option>
                    ))}
                  </select>
                </Field>
                <button
                  className="sp-btn sp-btn-solid"
                  disabled={busy}
                  onClick={create}
                >
                  {busy ? "Creating…" : "Create invitation"}
                </button>
              </>
            )}
          </div>
        </Dialog>
      )}
    </Page>
  );
}
