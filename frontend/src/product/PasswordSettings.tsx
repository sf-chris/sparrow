import { useState } from "react";
import PasswordField from "./PasswordField";
import { post } from "./api";
import { Dialog, ErrorNote } from "./ui";
export default function PasswordSettings({
  onChanged,
}: {
  onChanged?: () => void;
}) {
  const [open, setOpen] = useState(false),
    [current, setCurrent] = useState(""),
    [next, setNext] = useState(""),
    [again, setAgain] = useState(""),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [saved, setSaved] = useState(false);
  async function save(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setSaved(false);
    if (next !== again) {
      setError("The new passwords do not match.");
      return;
    }
    setBusy(true);
    try {
      await post("/auth/password", {
        current_password: current,
        new_password: next,
      });
      setCurrent("");
      setNext("");
      setAgain("");
      setSaved(true);
      onChanged?.();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  function close() {
    setOpen(false);
    setCurrent("");
    setNext("");
    setAgain("");
    setError("");
  }
  return (
    <>
      <section className="sp-section sp-password-setting" aria-labelledby="password-heading">
        <div>
          <h2 id="password-heading">Password</h2>
          <p className="sp-hint">Changing it signs out your other browsers.</p>
          {!open && saved && (
            <p className="sp-success" role="status">
              Password changed.
            </p>
          )}
        </div>
        <button
          className="sp-btn sp-btn-line"
          onClick={() => {
            setSaved(false);
            setOpen(true);
          }}
        >
          Change your password
        </button>
      </section>
      {open && (
        <Dialog title="Change password" onClose={close}>
          <form className="sp-form" onSubmit={save}>
            <ErrorNote error={error} />
            <p className="sp-hint">
              Changing your password signs out your other browser sessions.
            </p>
            <PasswordField
              label="Current password"
              value={current}
              onChange={setCurrent}
            />
            <PasswordField
              label="New password"
              creating
              value={next}
              onChange={setNext}
              hint="8 characters or more."
            />
            <PasswordField
              label="Repeat new password"
              creating
              value={again}
              onChange={setAgain}
            />
            <div className="sp-savebar">
              <span className="sp-success" role="status">
                {saved ? "Password changed." : ""}
              </span>
              <button className="sp-btn sp-btn-solid" disabled={busy}>
                {busy ? "Saving…" : "Change password"}
              </button>
            </div>
          </form>
        </Dialog>
      )}
    </>
  );
}
