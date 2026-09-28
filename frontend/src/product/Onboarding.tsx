import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, patch, post } from "./api";
import { ErrorNote, Loading, Page, useResource } from "./ui";
import { Tick } from "./Brand";
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
        "Add both API keys to find and download automatically, or choose Watch my existing collection.",
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
  const checks = state
    ? [
        {
          label: "Title information",
          ok: state.tmdb_configured,
          value: state.tmdb_configured
            ? "Key saved"
            : state.mode === "library"
              ? "Optional for your own files"
              : "Add a TMDB key",
          step: "providers" as Step,
        },
        {
          label: "Agents",
          ok: state.reasoning_configured,
          value: state.reasoning_configured
            ? "Key saved"
            : state.mode === "library"
              ? "Optional for watching"
              : "Add an Anthropic key",
          step: "providers" as Step,
        },
        {
          label: "Library",
          ok: state.libraries.length > 0,
          value: state.libraries.length
            ? `Ready on ${state.libraries.join(", ")}`
            : "Connect a library folder with media tools",
          step: "storage" as Step,
        },
        {
          label: "Downloads",
          ok: state.download_destinations.length > 0,
          value: state.download_destinations.length
            ? `Set up on ${state.download_destinations.join(", ")}`
            : state.mode === "library"
              ? "Later"
              : "Connect a download app beside writable library and incoming folders",
          step: "downloads" as Step,
        },
      ]
    : [];
  return (
    <Page
      className="setup-page"
      title="Setup"
      lede="Connect what you want to use. Progress saves as you go."
    >
      <ErrorNote error={error || resource.error} retry={resource.refresh} />
      {!state ? (
        <Loading label="Loading your setup" />
      ) : (
        <>
          <nav className="steps" aria-label="Setup steps">
            <ol>
              {steps.map(({ id, label }, index) => (
                <li key={id}>
                  <button
                    className="step"
                    aria-current={state.step === id ? "step" : undefined}
                    disabled={busy}
                    onClick={() => void update({ step: id })}
                  >
                    <span className="num step-no">{index + 1}</span>{" "}
                    <span>{label}</span>
                  </button>
                </li>
              ))}
            </ol>
          </nav>
          {state.step === "start" && (
            <section className="setup-start" aria-labelledby="start">
              <h2 id="start" className="setup-heading">
                How do you want to start?
              </h2>
              <div className="choices">
                <button
                  className="choice"
                  disabled={busy}
                  aria-describedby="choice-autopilot"
                  onClick={() =>
                    void update({
                      mode: "autopilot",
                      step: "providers",
                      deferred: false,
                    })
                  }
                >
                  <strong>Find and download for me</strong>
                  <span id="choice-autopilot" aria-hidden="true">
                    Needs title information, an Anthropic key, storage and a
                    download app.
                  </span>
                </button>
                <button
                  className="choice"
                  disabled={busy}
                  aria-describedby="choice-library"
                  onClick={() =>
                    void update({
                      mode: "library",
                      step: "storage",
                      deferred: false,
                    })
                  }
                >
                  <strong>Watch my existing collection</strong>
                  <span id="choice-library" aria-hidden="true">
                    No keys or download app. Add downloads later.
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
              <div className="savebar">
                <p className="muted">
                  Downloads need a separate, writable incoming folder.
                </p>
                <button
                  className="btn primary"
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
                <p className="done-note" role="status">
                  <Tick /> Downloads set up on{" "}
                  {state.download_destinations.join(", ")}.
                </p>
              )}
              <ServerSettings
                key="downloads"
                setupSection="downloads"
                onSaved={() => advance("review")}
              />
              <div className="row">
                <p className="muted">
                  On a paired Windows machine, set up its download app in
                  Sparrow Node instead.
                </p>
                <button
                  className="btn"
                  disabled={busy}
                  onClick={() => void update({ step: "review" })}
                >
                  Use my paired machine
                </button>
              </div>
            </>
          )}
          {state.step === "review" && (
            <section aria-labelledby="review">
              <h2 id="review" className="setup-heading">
                Check your setup
              </h2>
              <ul className="checks">
                {checks.map((check) => (
                  <li className="check-row" key={check.label}>
                    <span className="check-mark" aria-hidden="true">
                      {check.ok ? <Tick /> : <span className="check-open" />}
                    </span>
                    <div>
                      <h3>{check.label}</h3>
                      <p>{check.value}</p>
                    </div>
                    {(state.mode !== "library" || check.step === "storage") && (
                      <button
                        className="btn quiet"
                        disabled={busy}
                        onClick={() => void update({ step: check.step })}
                      >
                        Edit<span className="sr-only"> {check.label}</span>
                      </button>
                    )}
                  </li>
                ))}
              </ul>
              <p className="muted">
                Keys are checked on your first request. Nothing is downloaded or
                billed during setup.{" "}
                <Link to="/settings/defaults">
                  Household defaults and spending limits
                </Link>
              </p>
              {!state.can_finish && (
                <p role="status" className="muted">
                  Some steps still need attention. Fix them above, or finish
                  later from Settings.
                </p>
              )}
              <div className="savebar">
                <button
                  className="btn"
                  onClick={resource.refresh}
                  disabled={busy || resource.loading}
                >
                  Check again
                </button>
                <button
                  className="btn primary"
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
          <div className="setup-later">
            <button
              className="btn quiet"
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
