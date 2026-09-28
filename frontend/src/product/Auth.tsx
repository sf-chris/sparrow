import { useEffect, useState } from "react";
import { ArrowLeft, Scissors } from "lucide-react";
import { Logotype } from "./Brand";
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
  const heading = needsSetup
    ? "Set up Sparrow"
    : joining
      ? "Join Sparrow"
      : "Sign in";
  return (
    <main className="entry-page" id="main-content">
      <header className="entry-top">
        <Link className="brand" to="/" aria-label="Sparrow home">
          <Logotype />
        </Link>
        <Link className="back" to="/">
          <ArrowLeft size={18} strokeWidth={2.5} /> Back
        </Link>
      </header>
      <section className="coupon" aria-label={heading}>
        <Scissors
          className="coupon-cut"
          size={20}
          strokeWidth={2}
          aria-hidden="true"
        />
        <h1 className="display">{heading}</h1>
        {registering && (
          <p className="lede">
            {needsSetup
              ? "Create the owner account for this server."
              : "Your own account, progress and preferences."}
          </p>
        )}
        <form onSubmit={submit} className="form">
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
          <button className="btn primary" disabled={busy}>
            {busy
              ? "One moment…"
              : needsSetup
                ? "Create owner account"
                : joining
                  ? "Create account"
                  : "Sign in"}
          </button>
        </form>
      </section>
      <p className="entry-foot">An account on this server only.</p>
    </main>
  );
}
