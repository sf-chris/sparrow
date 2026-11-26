import { useEffect, useId, useState } from "react";
import { api, post } from "./api";
import { Tick } from "./Brand";
import { ErrorNote, Section, useResource } from "./ui";

type Status = {
  enabled: boolean;
  working: string | null;
  link: string | null;
  port: number;
  reachable: boolean;
  problem: string | null;
};

/** Sparrow Connect: this server's secure address, so a phone or TV reaches
 *  it from anywhere. Off until an administrator turns it on. */
export default function ConnectSettings() {
  const resource = useResource(() => api<Status>("/admin/connect"));
  const status = resource.data;
  const [error, setError] = useState("");
  const [port, setPort] = useState<string | null>(null);
  const id = useId();
  // While it switches, show each step as it happens.
  useEffect(() => {
    if (!status?.working) return;
    const timer = window.setTimeout(() => void resource.refresh(), 1500);
    return () => clearTimeout(timer);
  }, [status]);
  async function run(action: () => Promise<unknown>) {
    setError("");
    try {
      await action();
      setPort(null);
      await resource.refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  }
  const change = (enabled: boolean) =>
    run(() =>
      api("/admin/connect", {
        method: "PUT",
        body: JSON.stringify({
          enabled,
          port: Number(port ?? status?.port) || status?.port,
        }),
      }),
    );
  const busy = !!status?.working;
  const on = !!status?.enabled;
  const problem = on && !busy && !status?.reachable;
  const changed = !!status && port !== null && Number(port) !== status.port;
  return (
    <Section
      title="Sparrow Connect"
      description="Use Sparrow away from home, on your phone and TV. Video comes straight from this server."
    >
      <div className="form">
        <ErrorNote error={error || resource.error} retry={resource.refresh} />
        {status && (
          <>
            <div className="form-grid">
              <div className={`field key-field${problem ? " invalid" : ""}`}>
                <div className="key-label">
                  <label htmlFor={id}>Port</label>
                  <span
                    className={`key-state${on && status.reachable && !busy ? " saved" : ""}`}
                    id={id + "-state"}
                  >
                    {busy ? (
                      `${status.working}…`
                    ) : !on ? (
                      "Off"
                    ) : status.reachable ? (
                      <>
                        <Tick /> Reachable
                      </>
                    ) : (
                      "Not reachable"
                    )}
                  </span>
                </div>
                <input
                  id={id}
                  type="number"
                  min={1}
                  max={65535}
                  value={port ?? status.port}
                  disabled={busy}
                  aria-invalid={problem || undefined}
                  aria-describedby={`${id}-state ${id}-hint`}
                  onChange={(e) => setPort(e.target.value)}
                />
                <small id={id + "-hint"} role={problem ? "alert" : undefined}>
                  {problem ? (
                    status.problem || "Not reachable yet."
                  ) : on && status.link && !busy ? (
                    <a href={status.link} target="_blank" rel="noreferrer">
                      {status.link.replace(/^https:\/\//, "")}
                    </a>
                  ) : (
                    "Forward this port on your router to this server."
                  )}
                </small>
              </div>
            </div>
            <div className="actions end">
              {on && (changed || problem) && (
                <button
                  className="btn quiet"
                  disabled={busy}
                  onClick={() => void change(false)}
                >
                  Turn off
                </button>
              )}
              {!on ? (
                <button
                  className="btn"
                  disabled={busy}
                  onClick={() => void change(true)}
                >
                  Turn on
                </button>
              ) : changed ? (
                <button
                  className="btn"
                  disabled={busy}
                  onClick={() => void change(true)}
                >
                  Save
                </button>
              ) : problem ? (
                <button
                  className="btn"
                  onClick={() => void run(() => post("/admin/connect/check"))}
                >
                  Check again
                </button>
              ) : (
                <button
                  className="btn"
                  disabled={busy}
                  onClick={() => void change(false)}
                >
                  Turn off
                </button>
              )}
            </div>
          </>
        )}
      </div>
    </Section>
  );
}
