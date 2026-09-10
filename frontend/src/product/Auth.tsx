import { useEffect, useState } from "react";
import { ArrowRight, ArrowLeft, Check } from "lucide-react";
import { Mark, PlayroomArt } from "./Brand";
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
    <main className="sp-auth sp-auth-layout">
      <header className="sp-auth-header">
        <Link className="sp-brand" to="/" aria-label="Sparrow home">
          <Mark />
          <span>sparrow</span>
        </Link>
        <Link className="sp-back" to="/">
          <ArrowLeft size={15} /> Back to the good stuff
        </Link>
      </header>
      <section className="sp-auth-story" aria-label="Make yourself at home">
        <PlayroomArt />
        <h2>
          Your own little corner
          <br />
          of the watch-world.
        </h2>
        <p>Good stories. Familiar faces. Something just for you.</p>
      </section>
      <section
        className="sp-auth-entry"
        aria-label={registering ? "Create your account" : "Sign in"}
      >
        <div className="sp-auth-card">
          <div className="sp-auth-step">
            <span>
              {needsSetup
                ? "A FRESH START"
                : joining
                  ? "YOUR INVITATION"
                  : "HELLO AGAIN"}
            </span>
            <span className="sp-small-mark">
              <Mark />
            </span>
          </div>
          <h1>
            {needsSetup
              ? "Set up your Sparrow server"
              : joining
                ? "Welcome to the collection."
                : "Come on in."}
          </h1>
          <p className="sp-description">
            {needsSetup
              ? "A few details, then it’s yours. Next, connect your collection and invite your people."
              : joining
                ? "Make yourself at home. Your watch history and preferences will be just for you."
                : "Your collection is right where you left it."}
          </p>
          <form onSubmit={submit} className="sp-form">
            <ErrorNote error={error} />
            {needsSetup && (
              <Field
                label="Setup code"
                hint="Your installation agent or server startup logs have this one-time code."
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
              hint={
                registering
                  ? "8 characters or more. Keep it simple."
                  : undefined
              }
            />
            <button className="sp-button primary" disabled={busy}>
              {busy
                ? "One moment…"
                : needsSetup
                  ? "Create administrator account"
                  : joining
                    ? "Create account"
                    : "Sign in"}
              <ArrowRight size={17} />
            </button>
          </form>
          <p className="sp-auth-note">
            <Check size={14} /> An account on this server. No Sparrow
            subscription.
          </p>
        </div>
        <p className="sp-auth-footer">
          A little less managing. A lot more watching.
        </p>
      </section>
    </main>
  );
}
