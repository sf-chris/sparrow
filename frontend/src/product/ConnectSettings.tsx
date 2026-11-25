import { useEffect, useState } from "react";
import { Check, Copy } from "lucide-react";
import { api, post } from "./api";
import { ErrorNote, Field, Loading, Section, useResource } from "./ui";

type Status = {
  enabled: boolean;
  working: string | null;
  name: string | null;
  link: string | null;
  address: string | null;
  port: number;
  reachable: boolean;
  problem: string | null;
};

/** Sparrow Connect: an HTTPS address for this server, so phones and TVs
 *  reach it from anywhere. Off until an administrator turns it on. */
export default function ConnectSettings() {
  const resource = useResource(() => api<Status>("/admin/connect"));
  const status = resource.data;
  const [port, setPort] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);
  // While it switches, show each step as it happens.
  useEffect(() => {
    if (!status?.working) return;
    const timer = window.setTimeout(() => void resource.refresh(), 1500);
    return () => clearTimeout(timer);
  }, [status]);
  async function change(enabled: boolean) {
    setError("");
    try {
      await api("/admin/connect", {
        method: "PUT",
        body: JSON.stringify({ enabled, port: port ?? status?.port }),
      });
      await resource.refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function check() {
    setError("");
    try {
      await post("/admin/connect/check", {});
      await resource.refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function copy(text: string) {
    await navigator.clipboard?.writeText(text).catch(() => {});
    setCopied(true);
    window.setTimeout(() => setCopied(false), 2000);
  }
  return (
    <Section
      title="Sparrow Connect"
      description="A secure address for this server, so your phone and TVs reach it from anywhere. No video goes through Sparrow."
    >
      <ErrorNote error={error || resource.error} retry={resource.refresh} />
      {status ? (
        <div className="form-grid">
          <Field label="Connect">
            <label className="check">
              <input
                type="checkbox"
                checked={status.enabled}
                disabled={!!status.working}
                onChange={(e) => void change(e.target.checked)}
              />
              Reach this server from anywhere
            </label>
          </Field>
          <Field
            label="Outside port"
            hint="The port your router sends to this server."
          >
            <input
              type="number"
              min={1}
              max={65535}
              value={port ?? status.port}
              disabled={!!status.working}
              onChange={(e) => setPort(Number(e.target.value))}
              onBlur={() =>
                status.enabled &&
                port !== null &&
                port !== status.port &&
                void change(true)
              }
            />
          </Field>
          {status.working && (
            <div className="meta" role="status">
              <Loading label={`${status.working}…`} />
            </div>
          )}
          {!status.working && status.enabled && status.reachable && status.link && (
            <div className="connect-ready" role="status">
              <p>
                <Check size={16} strokeWidth={2.5} aria-hidden="true" /> Reachable
                at <span className="num">{status.address}</span>
              </p>
              <p className="meta">Open this on your phone once:</p>
              <div className="connect-link">
                <a href={status.link}>{status.link}</a>
                <button
                  className="control"
                  aria-label={copied ? "Copied" : "Copy link"}
                  onClick={() => void copy(status.link!)}
                >
                  {copied ? <Check size={16} /> : <Copy size={16} />}
                </button>
              </div>
            </div>
          )}
          {!status.working && status.enabled && status.problem && (
            <div className="connect-problem" role="alert">
              <p>{status.problem}</p>
              <button className="btn" onClick={() => void check()}>
                Check again
              </button>
            </div>
          )}
        </div>
      ) : (
        resource.loading && <Loading label="Loading Sparrow Connect" />
      )}
    </Section>
  );
}
