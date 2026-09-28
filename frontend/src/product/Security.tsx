import { useState } from "react";
import { Monitor, Smartphone } from "lucide-react";
import { api } from "./api";
import PasswordSettings from "./PasswordSettings";
import { Empty, ErrorNote, Loading, Page, Section, useResource } from "./ui";

type Session = { id: string; label: string; created: number; current: boolean };

export default function Security() {
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
  const mobile = (label: string) => /Mobile|Android|iPhone|iPad/i.test(label);
  return (
    <Page title="Account & security">
      <PasswordSettings onChanged={resource.refresh} />
      <Section title="Signed-in browsers">
        <ErrorNote error={error || resource.error} retry={resource.refresh} />
        {!resource.data && resource.loading ? (
          <Loading label="Loading browsers…" />
        ) : (
          resource.data &&
          (resource.data.length ? (
            <ul className="sp-rows sp-sessions">
              {resource.data.map((session) => (
                <li className="sp-row" key={session.id}>
                  <div className="sp-session">
                    <span className="sp-session-icon" aria-hidden="true">
                      {mobile(session.label) ? <Smartphone size={18} /> : <Monitor size={18} />}
                    </span>
                    <div>
                      <h3>
                        {session.current
                          ? "This browser"
                          : mobile(session.label)
                            ? "Mobile browser"
                            : "Browser"}
                      </h3>
                      <p className="sp-label">
                        Signed in {new Date(session.created * 1000).toLocaleString()}
                      </p>
                      <details className="sp-disclosure sp-session-detail">
                        <summary>Browser details</summary>
                        <p className="sp-hint">{session.label || "No browser information available."}</p>
                      </details>
                    </div>
                  </div>
                  {!session.current && (
                    <button
                      className="sp-btn sp-btn-line sp-btn-small"
                      disabled={!!busy}
                      onClick={() => revoke(session.id)}
                    >
                      {busy === session.id ? "Ending…" : "End session"}
                    </button>
                  )}
                </li>
              ))}
            </ul>
          ) : (
            <Empty title="No active browsers.">Sign in again to manage this account.</Empty>
          ))
        )}
      </Section>
    </Page>
  );
}
