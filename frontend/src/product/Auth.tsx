import { useEffect, useState } from "react";
import { ArrowRight, ArrowLeft } from "lucide-react";
import { Link } from "react-router-dom";
import { Mark, Wordmark } from "./Brand";
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
    needsSetup ? new URLSearchParams(location.hash.slice(1)).get("setup_code") || "" : "",
  );
  useEffect(() => {
    function readSetupLink() {
      if (joining) return;
      const linkedCode = new URLSearchParams(location.hash.slice(1)).get("setup_code");
      if (linkedCode === null) return;
      if (needsSetup) setCode(linkedCode);
      history.replaceState(history.state, "", location.pathname + location.search);
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
    <div className="sp-entry">
      <aside className="sp-entry-ticket" aria-hidden="true">
        <Link to="/" tabIndex={-1} className="sp-bar-brand">
          <Wordmark />
        </Link>
        <div className="sp-entry-stub">
          <strong>Admit one</strong>
          <Mark />
        </div>
      </aside>
      <main id="main-content" className="sp-entry-main">
        <div className="sp-entry-top">
          <Link className="sp-entry-brand" to="/" aria-label="Sparrow home">
            <Wordmark />
          </Link>
          <Link className="sp-back" to="/">
            <ArrowLeft size={15} aria-hidden="true" /> Back to Sparrow
          </Link>
        </div>
        <section
          className="sp-entry-form"
          aria-label={registering ? "Create your account" : "Sign in"}
        >
          <h1>{needsSetup ? "Set up Sparrow" : joining ? "Create your account" : "Sign in"}</h1>
          {needsSetup && <p className="sp-lede">Start with the administrator account.</p>}
          <form onSubmit={submit} className="sp-form">
            <ErrorNote error={error} />
            {needsSetup && (
              <Field
                label="Setup code"
                hint="From your installer or the server’s startup log."
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
              hint={registering ? "At least 8 characters." : undefined}
            />
            <button className="sp-btn sp-btn-solid sp-entry-submit" disabled={busy}>
              {busy
                ? registering
                  ? "Creating account…"
                  : "Signing in…"
                : needsSetup
                  ? "Create administrator account"
                  : joining
                    ? "Create account"
                    : "Sign in"}
              <ArrowRight size={17} aria-hidden="true" />
            </button>
          </form>
        </section>
      </main>
    </div>
  );
}
