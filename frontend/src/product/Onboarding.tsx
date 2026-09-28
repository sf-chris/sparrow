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
        "Add both API keys to enable automatic discovery and downloads, or choose Watch my existing collection.",
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
    <Page
      kicker="Opening night"
      title="Let’s get the house ready."
      description="Connect the services and storage you want to use. Progress saves as you go, so you can stop and come back."
    >
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
              <p className="sp-hint">
                Automatic fetching needs title information, a reasoning service, storage and a download
                app. You can also start with media you already own and add the rest later.
              </p>
              <div className="sp-choices">
                <button
                  className="sp-choice"
                  aria-label="Find and download for me"
                  aria-describedby="choice-autopilot"
                  disabled={busy}
                  onClick={() => void update({ mode: "autopilot", step: "providers", deferred: false })}
                >
                  <span className="sp-label">Full service</span>
                  <span className="sp-choice-title">Find and download for me</span>
                  <span className="sp-choice-body" id="choice-autopilot">
                    Connect title search, the reasoning service and a download app. Sparrow does the
                    fetching.
                  </span>
                </button>
                <button
                  className="sp-choice"
                  aria-label="Watch my existing collection"
                  aria-describedby="choice-library"
                  disabled={busy}
                  onClick={() => void update({ mode: "library", step: "storage", deferred: false })}
                >
                  <span className="sp-label">Just the projector</span>
                  <span className="sp-choice-title">Watch my existing collection</span>
                  <span className="sp-choice-body" id="choice-library">
                    No API keys or download app needed. Point Sparrow at your media and press play.
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
                  Choose folders on this server, or pair another machine.
                  Downloads need a separate, writable incoming folder.
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
                  Download settings are available on{" "}
                  {state.download_destinations.join(", ")}.
                </p>
              )}
              <ServerSettings
                key="downloads"
                setupSection="downloads"
                onSaved={() => advance("review")}
              />
              <p className="sp-hint">
                For a paired Windows machine, configure its download app in
                Sparrow Node, then check the setup summary.
              </p>
              <button
                className="sp-btn sp-btn-line"
                disabled={busy}
                onClick={() => void update({ step: "review" })}
              >
                Use my paired node and continue
              </button>
            </>
          )}
          {state.step === "review" && (
            <section className="sp-sheet sp-form">
              <h2>Check your setup</h2>
              <p>
                {state.mode === "library"
                  ? "Start by importing your existing media. Automatic downloads can be set up later."
                  : "These are the settings Sparrow will use for your first request."}
              </p>
              {[
                {
                  label: "Title information",
                  value: state.tmdb_configured
                    ? "Key saved"
                    : state.mode === "library"
                      ? "Optional for importing existing media"
                      : "Add a TMDB API key",
                  step: "providers" as Step,
                },
                {
                  label: "Discovery and management agents",
                  value: state.reasoning_configured
                    ? "Key saved"
                    : state.mode === "library"
                      ? "Optional for watching existing media"
                      : "Add an Anthropic API key",
                  step: "providers" as Step,
                },
                {
                  label: "Library and media inspection",
                  value: state.libraries.length
                    ? `Available on ${state.libraries.join(", ")}`
                    : "Connect an available library with media tools",
                  step: "storage" as Step,
                },
                {
                  label: "Downloads",
                  value: state.download_destinations.length
                    ? `Configured on ${state.download_destinations.join(", ")}`
                    : state.mode === "library"
                      ? "Set up later"
                      : "Connect a download app and writable library/incoming folders on the same machine",
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
                Live provider access is checked when you make a request. No paid
                model calls or downloads run during setup.
              </p>
              <Link to="/settings/defaults">
                Review household defaults and spending limits
              </Link>
              {!state.can_finish && (
                <p role="status">
                  Some steps still need attention. Update them above, or finish
                  later and resume from Settings.
                </p>
              )}
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
                    ? "Save setup and import media"
                    : "Save setup and find a title"}
                </button>
              </div>
            </section>
          )}
          <div className="sp-savebar">
            <p className="sp-hint">
              You can return here from Settings → Server setup.
            </p>
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
