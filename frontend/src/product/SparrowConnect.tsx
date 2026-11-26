import { useEffect, useId, useState } from "react";
import { Globe, RefreshCw } from "lucide-react";
import { api, post } from "./api";
import { Tick } from "./Brand";
import AddPhone from "./PhoneSignIn";
import { Bar, ErrorNote, Loading, Page, useResource } from "./ui";

type Status = {
  enabled: boolean;
  working: string | null;
  name: string | null;
  address: string | null;
  port: number;
  reachable: boolean;
  problem: string | null;
  checked: number | null;
  certificate_expires: number | null;
};

function age(seconds: number) {
  const gone = Math.max(0, Date.now() / 1000 - seconds);
  if (gone < 60) return "now";
  if (gone < 3600) return `${Math.floor(gone / 60)}m ago`;
  if (gone < 86400) return `${Math.floor(gone / 3600)}h ago`;
  return `${Math.floor(gone / 86400)}d ago`;
}

/** Sparrow Connect: this server's own secure address, so phones and TVs
 *  reach it from anywhere. Off until an administrator turns it on. */
export default function SparrowConnect() {
  const resource = useResource(() => api<Status>("/admin/connect"));
  const status = resource.data;
  const [error, setError] = useState("");
  const [editing, setEditing] = useState(false);
  const [port, setPort] = useState("");
  const [pending, setPending] = useState<number | null>(null);
  const id = useId();
  // While it switches, follow each step as it happens.
  useEffect(() => {
    if (!status?.working) return;
    const timer = window.setTimeout(() => void resource.refresh(), 1500);
    return () => clearTimeout(timer);
  }, [status]);
  async function run(action: () => Promise<unknown>) {
    setError("");
    try {
      await action();
      await resource.refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  }
  const outside = pending ?? status?.port ?? 8443;
  const turn = (enabled: boolean, chosen = outside) =>
    run(() =>
      api("/admin/connect", {
        method: "PUT",
        body: JSON.stringify({ enabled, port: chosen }),
      }),
    );
  function savePort() {
    const chosen = Number(port);
    if (!Number.isInteger(chosen) || chosen < 1 || chosen > 65535) {
      setError("Ports run from 1 to 65535.");
      return;
    }
    setEditing(false);
    setPending(chosen);
    if (status?.enabled) void turn(true, chosen).then(() => setPending(null));
  }

  const busy = !!status?.working;
  const on = !!status?.enabled;
  const live = on && !busy && !!status?.reachable;
  const stuck = on && !busy && !status?.reachable;
  // The four switching steps fold into the two lines below.
  const step = status?.working || "";
  const naming = /Starting$|Registering|certificate/.test(step);
  const checking = /HTTPS|Checking/.test(step);
  const host = status?.address?.replace(/^https:\/\//, "");

  const action = !status ? null : live ? (
    <AddPhone primary />
  ) : !on && !busy ? (
    <button className="btn primary" onClick={() => void turn(true)}>
      Turn on
    </button>
  ) : null;

  return (
    <Page
      title="Sparrow Connect"
      lede="Use Sparrow away from home. Video comes straight from this server."
      action={action}
      className="connect-page"
    >
      <ErrorNote error={error || resource.error} retry={resource.refresh} />
      {!status ? (
        resource.loading && <Loading label="Loading Sparrow Connect" />
      ) : (
        <section aria-labelledby={id + "-bar"}>
          <Bar id={id + "-bar"} title="This server">
            {live && (
              <button
                className="bar-button"
                onClick={() => void run(() => post("/admin/connect/check"))}
              >
                <RefreshCw size={14} strokeWidth={2.5} />
                Check now
              </button>
            )}
          </Bar>
          <ul className="devices">
            <li className="device-card">
              <Globe size={26} strokeWidth={2} aria-hidden="true" />
              <div className="device-body">
                <div className="device-head">
                  <h2 aria-live="polite">
                    {busy
                      ? step === "Turning off"
                        ? "Turning off"
                        : "Turning on"
                      : on
                        ? "On"
                        : "Off"}
                  </h2>
                  {stuck && <span className="flag problem">Not reachable</span>}
                  {on && !busy && status.checked && (
                    <span className="meta num device-free">
                      Checked {age(status.checked)}
                    </span>
                  )}
                </div>
                <ul className="folder-rows">
                  <li>
                    <span className="folder-label">Address</span>
                    {live && host ? (
                      <a
                        className="path"
                        href={status.address!}
                        target="_blank"
                        rel="noreferrer"
                      >
                        {host}
                      </a>
                    ) : (
                      <span className={`path ${status.name ? "" : "suggested"}`}>
                        {status.name || "Made when you turn it on"}
                      </span>
                    )}
                    <span className="leader" aria-hidden="true" />
                    {busy && naming ? (
                      <span className="folder-state working">
                        {step === "Starting" || /Registering/.test(step)
                          ? "Registering…"
                          : "Getting a certificate…"}
                      </span>
                    ) : on && (status.certificate_expires || checking) ? (
                      <span className="folder-state">
                        <Tick /> Secure
                      </span>
                    ) : null}
                  </li>
                  <li>
                    <span className="folder-label">
                      <label htmlFor={id + "-port"}>Port</label>
                    </span>
                    {editing ? (
                      <span className="port-edit">
                        <input
                          id={id + "-port"}
                          type="number"
                          inputMode="numeric"
                          min={1}
                          max={65535}
                          value={port}
                          autoFocus
                          onChange={(e) => setPort(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === "Enter") savePort();
                            if (e.key === "Escape") setEditing(false);
                          }}
                        />
                        <button className="btn" onClick={savePort}>
                          Save
                        </button>
                        <button
                          className="btn quiet"
                          onClick={() => setEditing(false)}
                        >
                          Cancel
                        </button>
                      </span>
                    ) : (
                      <span className="path num">{outside}</span>
                    )}
                    <span className="leader" aria-hidden="true" />
                    {busy && checking ? (
                      <span className="folder-state working">
                        Checking from outside…
                      </span>
                    ) : live ? (
                      <span className="folder-state">
                        <Tick /> Reachable
                      </span>
                    ) : stuck ? (
                      <span className="folder-state problem-text">
                        Not reachable
                      </span>
                    ) : busy ? (
                      <span className="folder-state quiet">Next</span>
                    ) : (
                      <span className="folder-state quiet">
                        Forward it on your router
                      </span>
                    )}
                  </li>
                </ul>
                {stuck && status.problem && (
                  <p className="connect-problem" role="alert">
                    {status.problem}
                  </p>
                )}
                {!busy && !editing && (
                  <div className="actions">
                    {stuck && (
                      <button
                        className="btn primary"
                        onClick={() =>
                          void run(() => post("/admin/connect/check"))
                        }
                      >
                        Check again
                      </button>
                    )}
                    <button
                      className="btn quiet"
                      onClick={() => {
                        setPort(String(outside));
                        setEditing(true);
                      }}
                    >
                      Change port
                    </button>
                    {on && (
                      <button
                        className="btn quiet"
                        onClick={() => void turn(false)}
                      >
                        Turn off
                      </button>
                    )}
                  </div>
                )}
              </div>
            </li>
          </ul>
          <p className="meta storage-note">
            {on
              ? "Turning off deletes this address. Phones signed in through it stop working."
              : "Nothing leaves this server until you turn it on."}
          </p>
        </section>
      )}
    </Page>
  );
}
