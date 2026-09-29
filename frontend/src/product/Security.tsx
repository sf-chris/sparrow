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
    <Page title="Account">
      <PasswordSettings onChanged={resource.refresh} />
      <Section title="Devices">
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
                        <p className="num">
                          Signed in{" "}
                          {new Date(session.created * 1000).toLocaleString()}
                        </p>
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
