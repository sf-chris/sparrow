import { useState } from "react";
import PasswordField from "./PasswordField";
import { post } from "./api";
import { ErrorNote } from "./ui";
export default function PasswordSettings() {
  const [current, setCurrent] = useState(""),
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
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <details className="mt-4">
      <summary className="sp-button quiet">Change your password</summary>
      <form className="sp-form mt-4" onSubmit={save}>
        <ErrorNote error={error} />
        <p className="sp-muted">
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
          <button className="sp-button secondary" disabled={busy}>
            {busy ? "Saving…" : "Change password"}
          </button>
        </div>
      </form>
    </details>
  );
}
