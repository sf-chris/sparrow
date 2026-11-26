import { useEffect, useMemo, useRef, useState } from "react";
import { Plus, Smartphone } from "lucide-react";
import { Link } from "react-router-dom";
import qrcode from "qrcode-generator";
import { api, post } from "./api";
import { Tick } from "./Brand";
import { Dialog, ErrorNote } from "./ui";

type Code = { code: string; expires: number; link: string | null };

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

const LOOPBACK = /^(localhost|127\.|\[::1\])/;

/** While `open`, keep a fresh one-time code and notice the phone arrive. */
function usePhoneCode(open: boolean, onSignedIn?: () => void) {
  const [code, setCode] = useState<Code | null>(null);
  const [done, setDone] = useState(false);
  const [error, setError] = useState("");
  const known = useRef<Set<string> | null>(null);
  async function fresh() {
    setError("");
    try {
      setCode(await post<Code>("/auth/device-link"));
    } catch (e) {
      setError((e as Error).message);
    }
  }
  useEffect(() => {
    setDone(false);
    setCode(null);
    known.current = null;
    if (!open) return;
    let live = true;
    void (async () => {
      try {
        const sessions = await api<{ id: string }[]>("/sessions");
        if (live) known.current = new Set(sessions.map((s) => s.id));
      } catch {
        /* Without the list the code still signs the phone in. */
      }
      if (live) await fresh();
    })();
    return () => {
      live = false;
    };
  }, [open]);
  // A code lasts ten minutes; keep one ready while it's showing.
  useEffect(() => {
    if (!open || !code || done) return;
    const timer = window.setTimeout(
      () => void fresh(),
      Math.max(0, code.expires * 1000 - Date.now()),
    );
    return () => clearTimeout(timer);
  }, [open, code, done]);
  // Watch for the phone's new sign-in.
  useEffect(() => {
    if (!open || done) return;
    const timer = window.setInterval(async () => {
      if (!known.current) return;
      try {
        const sessions = await api<{ id: string }[]>("/sessions");
        if (sessions.some((s) => !known.current!.has(s.id))) {
          setDone(true);
          setCode(null);
          onSignedIn?.();
        }
      } catch {
        /* Try again on the next tick. */
      }
    }, 2000);
    return () => clearInterval(timer);
  }, [open, done]);
  const unreachable = !!code && !code.link && LOOPBACK.test(location.hostname);
  return { code, done, error, fresh, unreachable };
}

function Unreachable({ admin, onClose }: { admin: boolean; onClose: () => void }) {
  return (
    <div className="phone-away">
      <h3>Phones can’t reach this server yet</h3>
      <p className="muted">
        {admin
          ? "Turn on Sparrow Connect and your phone opens Sparrow from anywhere."
          : "Ask an administrator to turn on Sparrow Connect."}
      </p>
      {admin && (
        <Link className="btn primary" to="/settings/connect" onClick={onClose}>
          Set up Sparrow Connect
        </Link>
      )}
    </div>
  );
}

/** Sign this person in on their phone by scanning: no password to type.
 *  The phone opens at `path`. The dialog notices it arrive and says so.
 *  `round` is the masthead's version: a phone in a circle, named on hover. */
export default function AddPhone({
  onSignedIn,
  admin = true,
  round = false,
  path = "/",
}: {
  onSignedIn?: () => void;
  admin?: boolean;
  round?: boolean;
  path?: string;
}) {
  const [open, setOpen] = useState(false);
  const { code, done, error, fresh, unreachable } = usePhoneCode(
    open,
    onSignedIn,
  );
  const close = () => setOpen(false);
  const label = round ? "Open on phone" : "Add a phone";
  const target = new URL(path, code?.link ?? location.origin).href;
  return (
    <>
      {round ? (
        <div className="phone-menu">
          <button
            className="open-on-phone"
            aria-label={label}
            onClick={() => setOpen(true)}
          >
            <Smartphone size={18} strokeWidth={2.25} aria-hidden="true" />
          </button>
          <span className="phone-tip" aria-hidden="true">
            {label}
          </span>
        </div>
      ) : (
        <button className="btn" onClick={() => setOpen(true)}>
          <Plus size={18} strokeWidth={2.5} aria-hidden="true" />
          {label}
        </button>
      )}
      {open && (
        <Dialog
          title={label}
          onClose={close}
          footer={
            <button className={`btn${done ? " primary" : ""}`} onClick={close}>
              {unreachable ? "Close" : "Done"}
            </button>
          }
        >
          <ErrorNote error={error} retry={() => void fresh()} />
          {done ? (
            <div className="phone-done" role="status">
              <Tick />
              <h3>Your phone is signed in</h3>
              <p className="meta">It’s listed under Devices.</p>
            </div>
          ) : unreachable ? (
            <Unreachable admin={admin} onClose={close} />
          ) : (
            code && (
              <div className="phone-signin">
                <QrCode
                  text={`${target}#signin=${code.code}`}
                  label="Sign-in code for your phone"
                />
                <div>
                  <ol className="phone-steps">
                    <li>
                      <span className="step-no">1</span>Open your phone’s camera
                    </li>
                    <li>
                      <span className="step-no">2</span>Point it at the code
                    </li>
                    <li>
                      <span className="step-no">3</span>Tap the link
                    </li>
                  </ol>
                  <p className="meta phone-waiting" aria-live="polite">
                    Waiting for your phone…
                  </p>
                </div>
              </div>
            )
          )}
        </Dialog>
      )}
    </>
  );
}
