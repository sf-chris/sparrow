import { useEffect, useMemo, useState } from "react";
import { Check, Copy } from "lucide-react";
import qrcode from "qrcode-generator";
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

/** A scannable code for a link: black on white, with the quiet border a
 *  phone camera needs, whatever the theme. */
function QrCode({ text, label }: { text: string; label: string }) {
  const { size, path } = useMemo(() => {
    const qr = qrcode(0, "M");
    qr.addData(text);
    qr.make();
    const count = qr.getModuleCount(),
      quiet = 4;
    let d = "";
    for (let row = 0; row < count; row++)
      for (let col = 0; col < count; col++)
        if (qr.isDark(row, col)) d += `M${col + quiet} ${row + quiet}h1v1h-1z`;
    return { size: count + quiet * 2, path: d };
  }, [text]);
  return (
    <svg
      className="qr"
      viewBox={`0 0 ${size} ${size}`}
      role="img"
      aria-label={label}
      shapeRendering="crispEdges"
    >
      <rect width={size} height={size} fill="#fff" />
      <path d={path} fill="#000" />
    </svg>
  );
}

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
              <div className="connect-phone">
                <QrCode
                  text={status.link}
                  label={`QR code for ${status.link}`}
                />
                <div>
                  <p>Scan with your phone’s camera, once.</p>
                  <div className="connect-link">
                    <a href={status.link}>{status.link}</a>
                    <button
                      className="btn"
                      onClick={() => void copy(status.link!)}
                    >
                      {copied ? (
                        <Check size={16} strokeWidth={2.5} />
                      ) : (
                        <Copy size={16} strokeWidth={2.25} />
                      )}
                      {copied ? "Copied" : "Copy"}
                    </button>
                  </div>
                </div>
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
