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
  return (
    <Page
      title="Account & security"
      description="Manage your password and the browsers signed in to your account."
    >
      <PasswordSettings onChanged={resource.refresh} />
      <Section
        title="Signed-in browsers"
        description="End a session to remove its access. Use Sign out in the settings navigation to leave this browser."
      >
        <ErrorNote error={error || resource.error} retry={resource.refresh} />
        {!resource.data && resource.loading ? (
          <Loading label="Loading browsers…" />
        ) : (
          resource.data &&
          (resource.data.length ? (
            <div className="sp-panel">
              {resource.data.map((session) => (
                <div className="sp-row" key={session.id}>
                  <div>
                    <h3 className="sp-actions">
                      {/Mobile|Android|iPhone|iPad/i.test(session.label) ? (
                        <Smartphone size={17} />
                      ) : (
                        <Monitor size={17} />
                      )}
                      {session.current
                        ? "This browser"
                        : /Mobile|Android|iPhone|iPad/i.test(session.label)
                          ? "Mobile browser"
                          : "Browser"}
                    </h3>
                    <p>
                      Signed in{" "}
                      {new Date(session.created * 1000).toLocaleString()}
                    </p>
                    <details className="sp-session-detail">
                      <summary>Browser details</summary>
                      <p>
                        {session.label || "No browser information available."}
                      </p>
                    </details>
                  </div>
                  {!session.current && (
                    <button
                      className="sp-button secondary"
                      disabled={!!busy}
                      onClick={() => revoke(session.id)}
                    >
                      {busy === session.id ? "Ending…" : "End session"}
                    </button>
                  )}
                </div>
              ))}
            </div>
          ) : (
            <Empty title="No active browsers.">
              Sign in again to manage this account.
            </Empty>
          ))
        )}
      </Section>
    </Page>
  );
}
