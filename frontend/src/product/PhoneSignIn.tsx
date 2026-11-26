import { useEffect, useMemo, useRef, useState } from "react";
import { Plus } from "lucide-react";
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

/** Sign this person in on their phone by scanning: no password to type.
 *  The dialog notices the phone arrive and says so. */
export default function AddPhone({ onSignedIn }: { onSignedIn: () => void }) {
  const [open, setOpen] = useState(false);
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
  async function start() {
    setOpen(true);
    setDone(false);
    setCode(null);
    known.current = null;
    try {
      const sessions = await api<{ id: string }[]>("/sessions");
      known.current = new Set(sessions.map((s) => s.id));
    } catch {
      /* Without the list the dialog still signs the phone in. */
    }
    await fresh();
  }
  // A code lasts ten minutes; keep one ready while the dialog is open.
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
          onSignedIn();
        }
      } catch {
        /* Try again on the next tick. */
      }
    }, 2000);
    return () => clearInterval(timer);
  }, [open, done]);
  const close = () => {
    setOpen(false);
    setCode(null);
  };
  const target = code?.link ?? `${location.origin}/`;
  const unreachable = !!code && !code.link && LOOPBACK.test(location.hostname);
  return (
    <>
      <button className="btn" onClick={() => void start()}>
        <Plus size={18} strokeWidth={2.5} />
        Add a phone
      </button>
      {open && (
        <Dialog
          title="Add a phone"
          onClose={close}
          footer={
            <button className={`btn${done ? " primary" : ""}`} onClick={close}>
              Done
            </button>
          }
        >
          <ErrorNote error={error} retry={() => void fresh()} />
          {done ? (
            <p className="phone-done" role="status">
              <Tick /> Your phone is signed in.
            </p>
          ) : unreachable ? (
            <p className="muted">
              Your phone can’t reach this address. An administrator can turn on
              Sparrow Connect in Connections.
            </p>
          ) : (
            code && (
              <div className="phone-signin">
                <QrCode
                  text={`${target}#signin=${code.code}`}
                  label="Sign-in code for your phone"
                />
                <p>Point your phone’s camera at the code.</p>
                <p className="meta">Each code signs in one phone.</p>
              </div>
            )
          )}
        </Dialog>
      )}
    </>
  );
}
