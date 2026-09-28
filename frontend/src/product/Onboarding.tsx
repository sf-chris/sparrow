import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, patch, post } from "./api";
import { ErrorNote, Loading, Page, useResource } from "./ui";
import { ServerSettings } from "./Administration";
import Storage from "./Storage";

type Step = "start" | "providers" | "storage" | "downloads" | "review";
type Setup = {
  complete: boolean;
  deferred: boolean;
  mode: "autopilot" | "library";
  step: Step;
  tmdb_configured: boolean;
  reasoning_configured: boolean;
  libraries: string[];
  download_destinations: string[];
  can_finish: boolean;
};

export default function Onboarding({
  onChanged,
}: {
  onChanged: () => Promise<void>;
}) {
  const resource = useResource(() => api<Setup>("/admin/onboarding"));
  const navigate = useNavigate();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const state = resource.data;
  const steps: { id: Step; label: string }[] = [
    { id: "start", label: "How to start" },
    ...(state?.mode === "library"
      ? []
      : [{ id: "providers" as Step, label: "Connections" }]),
    { id: "storage", label: "Storage" },
    ...(state?.mode === "library"
      ? []
      : [{ id: "downloads" as Step, label: "Downloads" }]),
    { id: "review", label: "Review" },
  ];
  async function update(
    values: Partial<Pick<Setup, "mode" | "step" | "deferred">>,
  ) {
    setBusy(true);
    setError("");
    try {
      resource.setData(await patch<Setup>("/admin/onboarding", values));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function advance(step: Step) {
    const current = await api<Setup>("/admin/onboarding");
    resource.setData(current);
    if (
      current.step === "providers" &&
      (!current.tmdb_configured || !current.reasoning_configured)
    )
      throw new Error(
        "Add both API keys, or go back and choose Watch my existing collection.",
      );
    await update({ step });
  }
  async function leave(finish: boolean) {
    setBusy(true);
    setError("");
    try {
      if (finish) await post("/admin/onboarding/finish");
      else await patch("/admin/onboarding", { deferred: true });
      await onChanged();
      navigate(
        finish
          ? state?.mode === "library"
            ? "/settings/storage"
            : "/discover"
          : "/",
        { replace: true },
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Page title="Server setup" description="Progress is saved as you go.">
      <ErrorNote error={error || resource.error} retry={resource.refresh} />
      {!state ? (
        <Loading label="Loading your setup…" />
      ) : (
        <>
          <nav className="sp-steps" aria-label="Server setup steps">
            <ol>
              {steps.map(({ id, label }, index) => {
                const position = steps.findIndex((step) => step.id === state.step);
                return (
                  <li key={id} className={index < position ? "is-done" : index === position ? "is-current" : ""}>
                    <button
                      aria-current={state.step === id ? "step" : undefined}
                      disabled={busy}
                      onClick={() => void update({ step: id })}
                    >
                      <span className="sp-steps-number" aria-hidden="true">
                        {String(index + 1).padStart(2, "0")}
                      </span>
                      <span>
                        <span className="sp-sr">{index + 1}. </span>
                        {label}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ol>
          </nav>
          {state.step === "start" && (
            <section className="sp-setup-start" aria-labelledby="setup-start">
              <h2 id="setup-start">How would you like to start?</h2>
              <div className="sp-choices">
                <button
                  className="sp-choice"
                  aria-label="Find and download for me"
                  aria-describedby="choice-autopilot"
                  disabled={busy}
                  onClick={() => void update({ mode: "autopilot", step: "providers", deferred: false })}
                >
                  <span className="sp-choice-title">Find and download for me</span>
                  <span className="sp-choice-body" id="choice-autopilot">
                    Needs two API keys and a download app.
                  </span>
                </button>
                <button
                  className="sp-choice"
                  aria-label="Watch my existing collection"
                  aria-describedby="choice-library"
                  disabled={busy}
                  onClick={() => void update({ mode: "library", step: "storage", deferred: false })}
                >
                  <span className="sp-choice-title">Watch my existing collection</span>
                  <span className="sp-choice-body" id="choice-library">
                    Point Sparrow at your media. You can add downloads later.
                  </span>
                </button>
              </div>
            </section>
          )}
          {state.step === "providers" && (
            <ServerSettings
              key="providers"
              setupSection="providers"
              onSaved={() => advance("storage")}
            />
          )}
          {state.step === "storage" && (
            <>
              <Storage onboarding onChanged={resource.refresh} />
              <div className="sp-savebar">
                <p className="sp-hint">
                  Downloads need a separate, writable staging folder.
                </p>
                <button
                  className="sp-btn sp-btn-solid"
                  disabled={busy}
                  onClick={() =>
                    void update({
                      step: state.mode === "library" ? "review" : "downloads",
                    })
                  }
                >
                  Continue
                </button>
              </div>
            </>
          )}
          {state.step === "downloads" && (
            <>
              {state.download_destinations.length > 0 && (
                <p className="sp-success" role="status">
                  Downloads are set up on {state.download_destinations.join(", ")}.
                </p>
              )}
              <ServerSettings
                key="downloads"
                setupSection="downloads"
                onSaved={() => advance("review")}
              />
              <p className="sp-hint">
                For a paired Windows machine, set up its download app in
                Sparrow Node instead.
              </p>
              <button
                className="sp-btn sp-btn-line"
                disabled={busy}
                onClick={() => void update({ step: "review" })}
              >
                Continue with my paired machine
              </button>
            </>
          )}
          {state.step === "review" && (
            <section className="sp-sheet sp-form">
              <h2>Check your setup</h2>
              {[
                {
                  label: "TMDB key",
                  value: state.tmdb_configured
                    ? "Saved"
                    : state.mode === "library"
                      ? "Optional"
                      : "Missing",
                  step: "providers" as Step,
                },
                {
                  label: "Anthropic key",
                  value: state.reasoning_configured
                    ? "Saved"
                    : state.mode === "library"
                      ? "Optional"
                      : "Missing",
                  step: "providers" as Step,
                },
                {
                  label: "Library",
                  value: state.libraries.length
                    ? state.libraries.join(", ")
                    : "No library with working media tools yet",
                  step: "storage" as Step,
                },
                {
                  label: "Downloads",
                  value: state.download_destinations.length
                    ? state.download_destinations.join(", ")
                    : state.mode === "library"
                      ? "Set up later"
                      : "Needs a download app and writable folders on the same machine",
                  step: "downloads" as Step,
                },
              ].map((check) => (
                <div className="sp-row" key={check.label}>
                  <div>
                    <h3>{check.label}</h3>
                    <p>{check.value}</p>
                  </div>
                  {(state.mode !== "library" || check.step === "storage") && (
                    <button
                      className="sp-btn sp-btn-ghost"
                      disabled={busy}
                      onClick={() => void update({ step: check.step })}
                    >
                      Edit<span className="sp-sr"> {check.label}</span>
                    </button>
                  )}
                </div>
              ))}
              <p className="sp-hint">
                Keys are tested on your first request. Setup makes no paid
                calls.
              </p>
              <Link to="/settings/defaults">Household defaults and spending limits</Link>
              {!state.can_finish && <p role="status">Some steps still need attention.</p>}
              <div className="sp-actions">
                <button
                  className="sp-btn sp-btn-line"
                  onClick={resource.refresh}
                  disabled={busy || resource.loading}
                >
                  Check again
                </button>
                <button
                  className="sp-btn sp-btn-solid"
                  disabled={busy || !state.can_finish}
                  onClick={() => void leave(true)}
                >
                  {state.mode === "library"
                    ? "Finish and import media"
                    : "Finish and find a title"}
                </button>
              </div>
            </section>
          )}
          <div className="sp-savebar">
            <button
              className="sp-btn sp-btn-ghost"
              disabled={busy}
              onClick={() => void leave(false)}
            >
              Finish later
            </button>
          </div>
        </>
      )}
    </Page>
  );
}
