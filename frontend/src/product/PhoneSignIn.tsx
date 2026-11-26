import { useEffect, useId, useMemo, useRef, useState } from "react";
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

/** Account › Devices: sign this person in on a phone by scanning, with no
 *  password to type. The dialog notices the phone arrive and says so. */
export default function AddPhone({
  onSignedIn,
  admin = true,
}: {
  onSignedIn?: () => void;
  admin?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const { code, done, error, fresh, unreachable } = usePhoneCode(
    open,
    onSignedIn,
  );
  const close = () => setOpen(false);
  return (
    <>
      <button className="btn" onClick={() => setOpen(true)}>
        <Plus size={18} strokeWidth={2.5} aria-hidden="true" />
        Add a phone
      </button>
      {open && (
        <Dialog
          title="Add a phone"
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
                  text={`${new URL("/", code.link ?? location.origin).href}#signin=${code.code}`}
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

/** The masthead's phone: one round button beside the account. It drops a
 *  small code that opens `path` on the phone, signed in. */
export function OpenOnPhone({ admin, path }: { admin: boolean; path: string }) {
  const [open, setOpen] = useState(false);
  const { code, done, error, fresh, unreachable } = usePhoneCode(open);
  const box = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const id = useId();
  // Escape or a click elsewhere puts it away.
  useEffect(() => {
    if (!open) return;
    const away = (e: PointerEvent) => {
      if (!box.current?.contains(e.target as Node)) setOpen(false);
    };
    const escape = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      setOpen(false);
      button.current?.focus();
    };
    document.addEventListener("pointerdown", away);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", away);
      document.removeEventListener("keydown", escape);
    };
  }, [open]);
  // The phone is open: put the code away a moment later.
  useEffect(() => {
    if (!done) return;
    const timer = window.setTimeout(() => setOpen(false), 2500);
    return () => clearTimeout(timer);
  }, [done]);
  const target = new URL(path, code?.link ?? location.origin).href;
  return (
    <div className="phone-menu" ref={box}>
      <button
        ref={button}
        className="open-on-phone"
        aria-label="Open on phone"
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        onClick={() => setOpen(!open)}
      >
        <Smartphone size={18} strokeWidth={2.25} aria-hidden="true" />
      </button>
      {!open && (
        <span className="phone-tip" aria-hidden="true">
          Open on phone
        </span>
      )}
      {open && (
        <div className="phone-pop" id={id} role="dialog" aria-label="Open on phone">
          <ErrorNote error={error} retry={() => void fresh()} />
          {done ? (
            <div className="phone-pop-done" role="status">
              <Tick />
              Open on your phone
            </div>
          ) : unreachable ? (
            <Unreachable admin={admin} onClose={() => setOpen(false)} />
          ) : code ? (
            <>
              <QrCode
                text={`${target}#signin=${code.code}`}
                label="Code that opens this page on your phone"
              />
              <p>Scan with your phone</p>
              <p className="meta">
                {path === "/" ? "Opens Sparrow" : "Opens this page"}, signed in
                as you.
              </p>
            </>
          ) : (
            !error && <div className="phone-pop-wait" aria-hidden="true" />
          )}
        </div>
      )}
    </div>
  );
}
