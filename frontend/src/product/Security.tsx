import { useState } from "react";
import { Monitor, Smartphone } from "lucide-react";
import { api } from "./api";
import PasswordSettings from "./PasswordSettings";
import AddPhone from "./PhoneSignIn";
import { Empty, ErrorNote, Loading, Page, Section, useResource } from "./ui";

type Session = { id: string; label: string; created: number; current: boolean };

/** "today, 9:41 pm", "3 Nov, 9:41 pm", or with the year when it isn't this one. */
function signedIn(seconds: number) {
  const when = new Date(seconds * 1000);
  const now = new Date();
  const time = when.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  if (when.toDateString() === now.toDateString()) return `today, ${time}`;
  const date = when.toLocaleDateString([], {
    day: "numeric",
    month: "short",
    year: when.getFullYear() === now.getFullYear() ? undefined : "numeric",
  });
  return `${date}, ${time}`;
}

export default function Security({ admin = false }: { admin?: boolean }) {
  const resource = useResource(() => api<Session[]>("/sessions"));
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  async function revoke(id: string) {
    setBusy(id);
    setError("");
    try {
      await api(`/sessions/${id}`, { method: "DELETE" });
      await resource.refresh();
    } catch (error) {
      setError((error as Error).message);
    } finally {
      setBusy("");
    }
  }
  return (
    <Page title="Account">
      <PasswordSettings onChanged={resource.refresh} />
      <Section
        title="Devices"
        description="Where you’re signed in."
        action={
          <AddPhone admin={admin} onSignedIn={() => void resource.refresh()} />
        }
      >
        <ErrorNote error={error || resource.error} retry={resource.refresh} />
        {!resource.data && resource.loading ? (
          <Loading label="Loading devices" />
        ) : (
          resource.data &&
          (resource.data.length ? (
            <ul className="rows">
              {resource.data.map((session) => {
                const phone = /Mobile|Android|iPhone|iPad/i.test(session.label);
                return (
                  <li className="row" key={session.id}>
                    <div className="device">
                      {phone ? (
                        <Smartphone
                          size={22}
                          strokeWidth={2}
                          aria-hidden="true"
                        />
                      ) : (
                        <Monitor size={22} strokeWidth={2} aria-hidden="true" />
                      )}
                      <div>
                        <h3>
                          {session.current
                            ? "This browser"
                            : phone
                              ? "Phone or tablet"
                              : "Browser"}
                        </h3>
                        <p className="num">Signed in {signedIn(session.created)}</p>
                        <details className="disclosure">
                          <summary>Browser details</summary>
                          <p className="muted">
                            {session.label || "No browser information."}
                          </p>
                        </details>
                      </div>
                    </div>
                    {!session.current && (
                      <button
                        className="btn"
                        disabled={!!busy}
                        onClick={() => revoke(session.id)}
                      >
                        {busy === session.id ? "Signing out…" : "Sign out"}
                        <span className="sr-only">
                          {" "}
                          of {phone ? "phone or tablet" : "browser"}
                        </span>
                      </button>
                    )}
                  </li>
                );
              })}
            </ul>
          ) : (
            <Empty title="No devices.">
              Sign in again to manage this account.
            </Empty>
          ))
        )}
      </Section>
    </Page>
  );
}
