import { useState } from "react";
import PasswordField from "./PasswordField";
import { post } from "./api";
import { Dialog, ErrorNote, Section } from "./ui";
import { Tick } from "./Brand";
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
      setError("The new passwords don’t match.");
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
      <Section title="Password">
        <div className="row">
          <p className="muted">
            {!open && saved ? (
              <span className="done-note" role="status">
                <Tick /> Password changed.
              </span>
            ) : (
              "Changing it signs you out on other devices."
            )}
          </p>
          <button
            className="btn"
            onClick={() => {
              setSaved(false);
              setOpen(true);
            }}
          >
            Change your password
          </button>
        </div>
      </Section>
      {open && (
        <Dialog title="Change password" onClose={close}>
          <form className="form" onSubmit={save}>
            <ErrorNote error={error} />
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
              hint="At least 8 characters."
            />
            <PasswordField
              label="Confirm new password"
              creating
              value={again}
              onChange={setAgain}
            />
            <div className="savebar">
              <span className="done-note" role="status">
                {saved && (
                  <>
                    <Tick /> Password changed.
                  </>
                )}
              </span>
              <button className="btn primary" disabled={busy}>
                {busy ? "Saving…" : "Change password"}
              </button>
            </div>
          </form>
        </Dialog>
      )}
    </>
  );
}
