import { useEffect, useState } from "react";
import { Check, Copy } from "lucide-react";
import { api, post } from "./api";
import { Field, Loading, useResource } from "./ui";
import { Tick } from "./Brand";

type Brand = "transmission" | "qbittorrent";
type Status = {
  type: Brand | "none";
  host: string;
  port: number;
  managed: boolean;
  reachable: boolean;
  version: string;
  managed_available: boolean;
  install_hint: string;
  in_container: boolean;
};
type Found = {
  type: Brand;
  host: string;
  port: number;
  version: string;
  needs_login: boolean;
  problem: string;
};
const NAMES: Record<Brand, string> = {
  transmission: "Transmission",
  qbittorrent: "qBittorrent",
};
const PORTS: Record<Brand, number> = { transmission: 9091, qbittorrent: 8080 };

/** Find the download app, connect one by hand, or have Sparrow run Transmission. */
export default function DownloadApp({
  onChanged,
}: {
  onChanged?: () => void | Promise<void>;
}) {
  const status = useResource(() => api<Status>("/admin/downloader"));
  const [found, setFound] = useState<Found[] | null>(null);
  const [looked, setLooked] = useState<Brand | null>(null);
  const [searching, setSearching] = useState(false);
  const [changing, setChanging] = useState(false);
  const [login, setLogin] = useState<string>("");
  const [manual, setManual] = useState(false);
  const [form, setForm] = useState({
    type: "transmission" as Brand,
    host: "",
    port: 9091,
    username: "",
    password: "",
  });
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);
  const current = status.data;
  const connected = !!current && current.type !== "none" && current.reachable;

  async function discover(brand: Brand | null) {
    setSearching(true);
    setError("");
    setLogin("");
    try {
      const result = await post<{ found: Found[] }>(
        "/admin/downloader/discover",
        { type: brand },
      );
      setFound(result.found);
      setLooked(brand);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSearching(false);
    }
  }
  // Look once, as soon as there is no working app (or the owner wants another).
  useEffect(() => {
    if (current && (!connected || changing) && found === null && !searching)
      void discover(null);
  }, [current, connected, changing]);

  async function done(next: Status) {
    status.setData(next);
    setChanging(false);
    setManual(false);
    setLogin("");
    setFound(null);
    await onChanged?.();
  }
  async function connect(values: typeof form, key: string) {
    setBusy(key);
    setError("");
    try {
      await done(await post<Status>("/admin/downloader/connect", values));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy("");
    }
  }
  async function runManaged() {
    setBusy("managed");
    setError("");
    try {
      await done(await post<Status>("/admin/downloader/managed"));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy("");
    }
  }

  if (!current)
    return status.error ? (
      <p className="field-error" role="alert">
        {status.error}
      </p>
    ) : (
      <Loading label="Checking downloads" />
    );

  if (connected && !changing)
    return (
      <div className="dl-current">
        <Tick />
        <div>
          <h3>{NAMES[current.type as Brand]} is connected</h3>
          <p>
            {current.managed
              ? "Sparrow runs it for you."
              : `${current.host}:${current.port}`}
          </p>
        </div>
        <button className="btn quiet" onClick={() => setChanging(true)}>
          Change
        </button>
      </div>
    );

  const brandName = looked ? NAMES[looked] : "a download app";
  const offline = current.type !== "none" && !current.reachable;
  return (
    <div className="dl">
      {offline && (
        <p className="dl-note">
          Can’t reach {NAMES[current.type as Brand]} at {current.host}:
          {current.port}.
        </p>
      )}
      {searching ? (
        <Loading label={`Looking for ${brandName}`} />
      ) : (
        found !== null && (
          <>
            {found.length ? (
              <ul className="dl-found" aria-label="Download apps found">
                {found.map((app) => {
                  const key = `${app.type}@${app.host}:${app.port}`;
                  return (
                    <li key={key}>
                      <div className="dl-found-line">
                        <div>
                          <h3>{NAMES[app.type]}</h3>
                          <p className="meta num">
                            {app.host}:{app.port}
                            {app.needs_login &&
                              !app.problem &&
                              " · needs its password"}
                          </p>
                          {app.problem && (
                            <p className="problem-text">{app.problem}</p>
                          )}
                        </div>
                        {login !== key && !app.problem && (
                          <button
                            className="btn primary"
                            disabled={!!busy}
                            onClick={() =>
                              app.needs_login
                                ? (setLogin(key),
                                  setForm({
                                    ...form,
                                    type: app.type,
                                    host: app.host,
                                    port: app.port,
                                  }))
                                : void connect(
                                    {
                                      type: app.type,
                                      host: app.host,
                                      port: app.port,
                                      username: "",
                                      password: "",
                                    },
                                    key,
                                  )
                            }
                          >
                            {busy === key ? "Connecting…" : "Use this"}
                          </button>
                        )}
                      </div>
                      {login === key && (
                        <form
                          className="dl-login"
                          onSubmit={(e) => {
                            e.preventDefault();
                            void connect(form, key);
                          }}
                        >
                          <Field label="Username">
                            <input
                              autoComplete="off"
                              value={form.username}
                              onChange={(e) =>
                                setForm({ ...form, username: e.target.value })
                              }
                            />
                          </Field>
                          <Field label="Password">
                            <input
                              type="password"
                              autoComplete="off"
                              value={form.password}
                              onChange={(e) =>
                                setForm({ ...form, password: e.target.value })
                              }
                            />
                          </Field>
                          <button
                            className="btn primary"
                            disabled={!!busy}
                            type="submit"
                          >
                            {busy === key ? "Connecting…" : "Connect"}
                          </button>
                        </form>
                      )}
                    </li>
                  );
                })}
              </ul>
            ) : (
              <p className="dl-none" role="status">
                No {looked ? NAMES[looked] : "download app"} found on this
                server.
              </p>
            )}
            <div className="dl-managed">
              {current.managed_available ? (
                <>
                  <button
                    className={`btn ${found.length ? "" : "primary"}`}
                    disabled={!!busy}
                    onClick={runManaged}
                  >
                    {busy === "managed"
                      ? "Setting up Transmission…"
                      : "Set up Transmission for me"}
                  </button>
                  <p className="muted">Sparrow installs and runs it.</p>
                </>
              ) : (
                current.install_hint && (
                  <>
                    <p className="muted">
                      To have Sparrow run one, install Transmission, then look
                      again:
                    </p>
                    <div className="dl-command">
                      <code>{current.install_hint}</code>
                      <button
                        className="btn quiet"
                        onClick={async () => {
                          try {
                            await navigator.clipboard.writeText(
                              current.install_hint,
                            );
                            setCopied(true);
                          } catch {
                            setError("Couldn’t copy. Select the command.");
                          }
                        }}
                      >
                        {copied ? (
                          <Check size={16} strokeWidth={2.5} />
                        ) : (
                          <Copy size={16} strokeWidth={2.25} />
                        )}
                        {copied ? "Copied" : "Copy"}
                      </button>
                    </div>
                  </>
                )
              )}
            </div>
          </>
        )
      )}
      {error && (
        <p className="field-error" role="alert">
          {error}
        </p>
      )}
      {!searching && found !== null && (
        <p className="dl-more">
          Have one already? Look for{" "}
          <button className="link" onClick={() => void discover("transmission")}>
            Transmission
          </button>{" "}
          or{" "}
          <button className="link" onClick={() => void discover("qbittorrent")}>
            qBittorrent
          </button>
          , or{" "}
          <button
            className="link"
            aria-expanded={manual}
            onClick={() => setManual(!manual)}
          >
            enter its address
          </button>
          .
        </p>
      )}
      {manual && (
        <form
          className="dl-manual"
          onSubmit={(e) => {
            e.preventDefault();
            void connect(form, "manual");
          }}
        >
          <div className="form-grid">
            <Field label="Download app">
              <select
                value={form.type}
                onChange={(e) => {
                  const type = e.target.value as Brand;
                  setForm({ ...form, type, port: PORTS[type] });
                }}
              >
                <option value="transmission">Transmission</option>
                <option value="qbittorrent">qBittorrent</option>
              </select>
            </Field>
            <Field
              label="Address"
              hint={
                current.in_container
                  ? "Its network address. In Docker, localhost is Sparrow itself."
                  : undefined
              }
            >
              <input
                value={form.host}
                placeholder="192.168.1.20"
                onChange={(e) => setForm({ ...form, host: e.target.value })}
              />
            </Field>
            <Field label="Port">
              <input
                type="number"
                min={1}
                max={65535}
                value={form.port}
                onChange={(e) =>
                  setForm({ ...form, port: Number(e.target.value) })
                }
              />
            </Field>
            <Field label="Username">
              <input
                autoComplete="off"
                value={form.username}
                onChange={(e) => setForm({ ...form, username: e.target.value })}
              />
            </Field>
            <Field label="Password">
              <input
                type="password"
                autoComplete="off"
                value={form.password}
                onChange={(e) => setForm({ ...form, password: e.target.value })}
              />
            </Field>
          </div>
          {current.in_container && (
            <p className="muted">
              It has to save into the incoming folder at the same path Sparrow
              uses.
            </p>
          )}
          <div className="actions">
            <button
              className="btn primary"
              type="submit"
              disabled={!!busy || !form.host.trim()}
            >
              {busy === "manual" ? "Connecting…" : "Connect"}
            </button>
          </div>
        </form>
      )}
      {changing && (
        <div className="actions">
          <button className="btn quiet" onClick={() => setChanging(false)}>
            Keep {NAMES[current.type as Brand]}
          </button>
        </div>
      )}
    </div>
  );
}
