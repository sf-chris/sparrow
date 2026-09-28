import { useEffect, useState } from "react";
import { ArrowLeft } from "lucide-react";
import { Mark } from "./Brand";
import { Link } from "react-router-dom";
import PasswordField from "./PasswordField";
import { post, type Auth as AuthState } from "./api";
import { ErrorNote, Field } from "./ui";

export default function Auth({
  needsSetup,
  onSuccess,
}: {
  needsSetup: boolean;
  onSuccess: (auth: AuthState) => void;
}) {
  const joining = location.pathname === "/join";
  const invitation = joining ? location.hash.slice(1) : "";
  const [name, setName] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState(() =>
    needsSetup
      ? new URLSearchParams(location.hash.slice(1)).get("setup_code") || ""
      : "",
  );
  useEffect(() => {
    function readSetupLink() {
      if (joining) return;
      const linkedCode = new URLSearchParams(location.hash.slice(1)).get(
        "setup_code",
      );
      if (linkedCode === null) return;
      if (needsSetup) setCode(linkedCode);
      history.replaceState(
        history.state,
        "",
        location.pathname + location.search,
      );
    }
    readSetupLink();
    window.addEventListener("hashchange", readSetupLink);
    return () => window.removeEventListener("hashchange", readSetupLink);
  }, [joining, needsSetup]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const registering = needsSetup || joining;
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const path = needsSetup ? "bootstrap" : joining ? "join" : "login";
      const auth = await post<AuthState>(
        `/auth/${path}`,
        registering
          ? { name, username, password, setup_code: code, invitation }
          : { username, password },
      );
      onSuccess(auth);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <main className="sp-entry">
      <header className="sp-front-bar">
        <Link className="sp-wordmark" to="/" aria-label="Sparrow home">
          <Mark />
          <span>sparrow</span>
        </Link>
        <Link className="sp-back" to="/">
          <ArrowLeft size={16} /> Back
        </Link>
      </header>
      <section
        className="sp-entry-card"
        aria-label={registering ? "Create your account" : "Sign in"}
      >
        <Mark />
        <h1>
          {needsSetup
            ? "Set up your Sparrow server"
            : joining
              ? "You’re invited."
              : "Welcome back."}
        </h1>
        <p className="sp-lede">
          {needsSetup
            ? "Create the administrator account. Next, connect your storage and invite your household."
            : joining
              ? "Create your account. Your progress and preferences stay yours."
              : "Sign in to your library."}
        </p>
        <form onSubmit={submit} className="sp-form">
          <ErrorNote error={error} />
          {needsSetup && (
            <Field
              label="Setup code"
              hint="Your installation agent or the server’s startup log has this one-time code."
            >
              <input
                required
                value={code}
                onChange={(e) => setCode(e.target.value)}
                autoComplete="off"
                autoCapitalize="none"
                spellCheck={false}
              />
            </Field>
          )}
          {registering && (
            <Field label="Your name">
              <input
                required
                maxLength={100}
                value={name}
                onChange={(e) => setName(e.target.value)}
                autoComplete="name"
              />
            </Field>
          )}
          <Field label="Username">
            <input
              required
              minLength={3}
              maxLength={64}
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
              autoCapitalize="none"
              spellCheck={false}
            />
          </Field>
          <PasswordField
            value={password}
            onChange={setPassword}
            creating={registering}
            hint={registering ? "8 characters or more." : undefined}
          />
          <button className="sp-button primary sp-button-block" disabled={busy}>
            {busy
              ? "One moment…"
              : needsSetup
                ? "Create administrator account"
                : joining
                  ? "Create account"
                  : "Sign in"}
          </button>
        </form>
        <p className="sp-quiet sp-entry-note">
          An account on this server only. No Sparrow subscription.
        </p>
      </section>
    </main>
  );
}
