import { useEffect, useMemo, useState } from "react";
import qrcode from "qrcode-generator";
import { api, post } from "./api";
import { ErrorNote, Loading, Section, useResource } from "./ui";

type Status = {
  enabled: boolean;
  working: string | null;
  link: string | null;
  port: number;
  reachable: boolean;
  problem: string | null;
};
type SignIn = { code: string; expires: number };

/** A scannable code: black on white with the quiet border a phone camera
 *  needs, whatever the theme. */
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

/** Sparrow Connect: this server's secure address, so a phone or TV reaches
 *  it from anywhere. Off until an administrator turns it on. */
export default function ConnectSettings() {
  const resource = useResource(() => api<Status>("/admin/connect"));
  const status = resource.data;
  const [error, setError] = useState("");
  const [port, setPort] = useState<string | null>(null);
  const [signin, setSignin] = useState<SignIn | null>(null);
  // While it switches, show each step as it happens.
  useEffect(() => {
    if (!status?.working) return;
    const timer = window.setTimeout(() => void resource.refresh(), 1500);
    return () => clearTimeout(timer);
  }, [status]);
  // A sign-in code is a key: hide it when it lapses.
  useEffect(() => {
    if (!signin) return;
    const left = signin.expires * 1000 - Date.now();
    const timer = window.setTimeout(() => setSignin(null), Math.max(0, left));
    return () => clearTimeout(timer);
  }, [signin]);
  async function run(action: () => Promise<unknown>) {
    setError("");
    try {
      await action();
      await resource.refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  }
  const change = (enabled: boolean, nextPort?: number) =>
    run(() =>
      api("/admin/connect", {
        method: "PUT",
        body: JSON.stringify({ enabled, port: nextPort ?? status?.port }),
      }),
    );
  async function showCode() {
    setError("");
    try {
      setSignin(await post<SignIn>("/auth/device-link"));
    } catch (e) {
      setError((e as Error).message);
    }
  }
  function savePort() {
    const value = Number(port);
    setPort(null);
    if (value && value !== status?.port) void change(true, value);
  }
  const on = !!status?.enabled;
  const ready = on && status!.reachable && !status!.working && !!status!.link;
  const portEditor = (
    <span className="connect-port">
      <label>
        Port{" "}
        <input
          type="number"
          min={1}
          max={65535}
          value={port ?? ""}
          autoFocus
          onChange={(e) => setPort(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && savePort()}
        />
      </label>
      <button className="link" onClick={savePort}>
        Save
      </button>
    </span>
  );
  return (
    <Section
      title="Sparrow Connect"
      description="Reach this server from anywhere, on your phone and your TVs. Video goes straight from this server."
      action={
        status && (
          <button
            className="switch"
            role="switch"
            aria-checked={on}
            aria-label="Sparrow Connect"
            disabled={!!status.working}
            onClick={() => void change(!on)}
          >
            <span aria-hidden="true" />
          </button>
        )
      }
    >
      <ErrorNote error={error || resource.error} retry={resource.refresh} />
      {!status && resource.loading && <Loading label="Loading" />}
      {status?.working && <Loading label={`${status.working}…`} />}
      {ready &&
        (signin ? (
          <div className="connect-signin">
            <QrCode
              text={`${status!.link}#signin=${signin.code}`}
              label="Sign-in code for your phone"
            />
            <div>
              <p className="connect-lead">Scan with your phone’s camera.</p>
              <p className="meta">It signs you in once, within ten minutes.</p>
              <button className="link" onClick={() => setSignin(null)}>
                Done
              </button>
            </div>
          </div>
        ) : (
          <button className="btn primary" onClick={() => void showCode()}>
            Sign in on your phone
          </button>
        ))}
      {on && !status!.working && (
        <p className={`connect-state meta${status!.reachable ? " good" : ""}`}>
          {status!.reachable ? (
            <>
              <span className="dot" aria-hidden="true" />
              Reachable from anywhere · Port {status!.port}{" "}
              {port === null ? (
                <button
                  className="link"
                  onClick={() => setPort(String(status!.port))}
                >
                  Change
                </button>
              ) : (
                portEditor
              )}
            </>
          ) : (
            <>
              <span className="dot problem" aria-hidden="true" />
              <span className="connect-problem" role="alert">
                {status!.problem || "Not reachable yet."}
              </span>{" "}
              {port === null ? (
                <>
                  <button
                    className="link"
                    onClick={() => void run(() => post("/admin/connect/check"))}
                  >
                    Check again
                  </button>
                  {" · "}
                  <button
                    className="link"
                    onClick={() => setPort(String(status!.port))}
                  >
                    Change port
                  </button>
                </>
              ) : (
                portEditor
              )}
            </>
          )}
        </p>
      )}
    </Section>
  );
}
