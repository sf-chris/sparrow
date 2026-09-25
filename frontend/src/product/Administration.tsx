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
      description="The starting point for everyone. Personal overrides stay personal; server limits always apply."
    >
      <ErrorNote error={error || resource.error} retry={resource.refresh} />
      {resource.data ? (
        <>
          <Section title="Default preferences">
            <div className="sp-panel">
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
            description="These apply to every request, including work managed by agents."
          >
            <div className="sp-panel sp-form-grid">
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
                hint="Zero means no server size limit."
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
                hint="Each request has its own allowance. Each followed title also shares this amount across all collection reviews, without an automatic reset. Raise the limit and retry if collection care stops."
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
              className="sp-button primary"
              disabled={busy}
              onClick={save}
            >
              {busy ? "Saving…" : "Save defaults"}
            </button>
          </div>
        </>
      ) : (
        resource.loading && <Loading label="Loading household defaults…" />
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
          ? "Connect title search and agents"
          : setupSection === "downloads"
            ? "Connect your download app"
            : "Server settings"
      }
      description={
        setupSection === "providers"
          ? "TMDB supplies title and episode information. Anthropic powers discovery and collection care. Keys are stored on your server."
          : setupSection === "downloads"
            ? "Use an existing Transmission or qBittorrent app. Sparrow does not install a download app for you."
            : "Connect Sparrow to title information and its reasoning service. Storage lives in Storage & import."
      }
    >
      <ErrorNote error={error || resource.error} retry={resource.refresh} />
      {resource.data ? (
        <div className="sp-form">
          {setupSection !== "downloads" && (
            <Section title="Connections">
              {setupSection && (
                <p className="sp-muted">
                  Create a key in{" "}
                  <a
                    href="https://www.themoviedb.org/settings/api"
                    target="_blank"
                    rel="noreferrer"
                  >
                    TMDB API settings
                  </a>{" "}
                  and{" "}
                  <a
                    href="https://platform.claude.com/settings/keys"
                    target="_blank"
                    rel="noreferrer"
                  >
                    Claude Console
                  </a>
                  , then paste them below. Anthropic API usage is billed by the
                  provider.
                </p>
              )}
              <div className="sp-panel sp-form-grid">
                <Field
                  label="TMDB API key"
                  hint={
                    values.tmdb_api_key_configured
                      ? "Configured. Leave blank to keep the current key."
                      : "Used to find movies, shows and episode information."
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
                      ? "Configured. Leave blank to keep the current key."
                      : "Used by discovery and management agents. Watching existing media works without it."
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
                      hint="This release includes one source. Extra source connectors are a later feature."
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
              description="For Windows acquisition, configure the download app on the Windows node instead."
            >
              <div className="sp-panel sp-form-grid">
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
                  hint="Leave blank to keep a saved password."
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
                  hint="Zero means no ratio limit."
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
                  hint="Zero means no time limit."
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
            <p className="sp-muted">
              If Sparrow runs in Docker, the host must be reachable from its
              container. Use the download machine’s LAN address; localhost
              refers to the Sparrow container.
            </p>
          )}
          <div className="sp-savebar">
            <span className="sp-success" role="status">
              {saved ? "Server settings saved." : ""}
            </span>
            <button
              className="sp-button primary"
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
        resource.loading && <Loading label="Loading server settings…" />
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
      description="Good stories are better shared. Give everyone a space of their own."
      action={
        <button
          className="sp-button primary"
          onClick={() => {
            setDialog(true);
            setInvite("");
          }}
        >
          <Plus size={16} />
          Invite someone
        </button>
      }
    >
      <ErrorNote error={error || resource.error} retry={resource.refresh} />
      {resource.data ? (
        <div className="sp-people-grid">
          {resource.data.map((user) => (
            <article className="sp-person-card" key={user.id}>
              <span className="sp-avatar" aria-hidden="true">
                {user.name.slice(0, 1).toUpperCase()}
              </span>
              <div>
                <h3>
                  {user.name}
                  {user.id === currentUser.id ? " · You" : ""}
                </h3>
                <p>
                  {user.username} ·{" "}
                  {user.role === "requester"
                    ? "Can watch and request"
                    : user.role === "admin"
                      ? "Administrator"
                      : "Can watch"}
                  {user.disabled ? " · Access disabled" : ""}
                </p>
              </div>
              <div className="sp-actions">
                {user.id !== currentUser.id && (
                  <button
                    className="sp-button secondary"
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
                )}
                {user.id !== currentUser.id && (
                  <button
                    className={`sp-button ${user.disabled ? "secondary" : "quiet"}`}
                    onClick={() => disable(user)}
                  >
                    {user.disabled ? "Restore access" : "Disable access"}
                  </button>
                )}
              </div>
            </article>
          ))}
        </div>
      ) : (
        resource.loading && <Loading label="Loading household accounts…" />
      )}
      {editing && (
        <Dialog
          title={`Access for ${editing.name}`}
          onClose={() => setEditing(null)}
          footer={
            <button
              className="sp-button primary"
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
            <p className="sp-muted">
              Changing access signs this person out so the new permissions apply
              immediately.
            </p>
          </div>
        </Dialog>
      )}
      {dialog && (
        <Dialog title="Invite someone" onClose={() => setDialog(false)}>
          <div className="sp-form">
            <ErrorNote error={error} />
            {invite ? (
              <>
                <p className="sp-muted">
                  Share this invitation link with the person you want to invite.
                </p>
                <Field label="Invitation link">
                  <input
                    readOnly
                    value={invite}
                    onFocus={(e) => e.target.select()}
                  />
                </Field>
                <button
                  className="sp-button primary"
                  onClick={async () => {
                    try {
                      await navigator.clipboard.writeText(invite);
                      setCopied(true);
                    } catch {
                      setError(
                        "Select the invitation link and copy it manually.",
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
                <p className="sp-muted">
                  A personal invitation, valid for seven days and usable once.
                </p>
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
                <Field label="Which storage libraries?">
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
                  className="sp-button primary"
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
