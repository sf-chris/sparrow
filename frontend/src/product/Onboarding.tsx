import { useState, type FormEvent, type ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, patch, post } from "./api";
import { ErrorNote, Loading, Page, useResource } from "./ui";
import { Tick } from "./Brand";
import { KEYS, KeyField, type KeyName } from "./Keys";
import DownloadApp from "./DownloadApp";
import Storage from "./Storage";

type Step = "start" | "providers" | "storage" | "downloads" | "review";
type Setup = {
  complete: boolean;
  deferred: boolean;
  mode: "autopilot" | "library";
  step: Step;
  tmdb_configured: boolean;
  reasoning_configured: boolean;
  openai_configured: boolean;
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
  const library = state?.mode === "library";
  const steps: { id: Step; label: string }[] = [
    { id: "start", label: "Start" },
    ...(library ? [] : [{ id: "providers" as Step, label: "Keys" }]),
    { id: "storage", label: "Storage" },
    ...(library ? [] : [{ id: "downloads" as Step, label: "Downloads" }]),
    { id: "review", label: "Finish" },
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
  async function leave(finish: boolean) {
    setBusy(true);
    setError("");
    try {
      if (finish) await post("/admin/onboarding/finish");
      else await patch("/admin/onboarding", { deferred: true });
      await onChanged();
      navigate(finish ? (library ? "/settings/storage" : "/discover") : "/", {
        replace: true,
      });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  /** Every step ends the same way: leave for now on the left, go on at the right. */
  const bar = (actions: ReactNode) => (
    <div className="savebar setup-bar">
      <button
        className="btn quiet later"
        type="button"
        disabled={busy}
        onClick={() => void leave(false)}
      >
        Finish later
      </button>
      {actions}
    </div>
  );
  const keyState = (configured: boolean, required: boolean) =>
    configured ? "On server" : required && !library ? "Missing" : "Optional";
  const checks = state
    ? [
        {
          label: "TMDB key",
          ok: state.tmdb_configured,
          value: keyState(state.tmdb_configured, true),
          step: "providers" as Step,
        },
        {
          label: "Anthropic key",
          ok: state.reasoning_configured,
          value: keyState(state.reasoning_configured, true),
          step: "providers" as Step,
        },
        {
          label: "OpenAI key",
          ok: state.openai_configured,
          value: keyState(state.openai_configured, false),
          step: "providers" as Step,
          optional: true,
        },
        {
          label: "Library",
          ok: state.libraries.length > 0,
          value: state.libraries.length
            ? state.libraries.join(", ")
            : "No library folder yet",
          step: "storage" as Step,
        },
        {
          label: "Downloads",
          ok: state.download_destinations.length > 0,
          value: state.download_destinations.length
            ? state.download_destinations.join(", ")
            : library
              ? "Later"
              : "No download app yet",
          step: "downloads" as Step,
        },
      ]
    : [];
  return (
    <Page
      className="setup-page"
      title="Setup"
      action={
        state && (
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
        )
      }
    >
      <ErrorNote error={error || resource.error} retry={resource.refresh} />
      {!state ? (
        <Loading label="Loading setup" />
      ) : (
        <>
          {state.step === "start" && (
            <section className="setup-step" aria-labelledby="start">
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
                    Needs a TMDB key, an Anthropic key, storage and a download
                    app.
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
                    Needs storage only. Add the rest any time.
                  </span>
                </button>
              </div>
              {bar(null)}
            </section>
          )}
          {state.step === "providers" && (
            <KeysStep
              bar={bar}
              onDone={async () => {
                await resource.refresh();
                await update({ step: "storage" });
              }}
            />
          )}
          {state.step === "storage" && (
            <section className="setup-step" aria-labelledby="storage-step">
              <h2 id="storage-step" className="setup-heading">
                Storage
              </h2>
              <p className="setup-lede">
                {library
                  ? "The folder where your films and series are."
                  : "Where films and series go, and a separate folder for downloads in progress."}
              </p>
              <Storage onboarding onChanged={resource.refresh} />
              {bar(
                <button
                  className={`btn ${state.libraries.length ? "primary" : ""}`}
                  disabled={busy}
                  onClick={() =>
                    void update({ step: library ? "review" : "downloads" })
                  }
                >
                  {state.libraries.length ? "Continue" : "Skip for now"}
                </button>,
              )}
            </section>
          )}
          {state.step === "downloads" && (
            <section className="setup-step" aria-labelledby="downloads-step">
              <h2 id="downloads-step" className="setup-heading">
                Download app
              </h2>
              <p className="setup-lede">
                Sparrow hands each download to a torrent app.
              </p>
              <DownloadApp onChanged={resource.refresh} />
              {bar(
                <button
                  className={`btn ${state.download_destinations.length ? "primary" : ""}`}
                  disabled={busy}
                  onClick={() => void update({ step: "review" })}
                >
                  {state.download_destinations.length
                    ? "Continue"
                    : "Skip for now"}
                </button>,
              )}
            </section>
          )}
          {state.step === "review" && (
            <section className="setup-step" aria-labelledby="review">
              <h2 id="review" className="setup-heading">
                Check your setup
              </h2>
              <ul className="checks">
                {checks.map((check) => (
                  <li className="check-row" key={check.label}>
                    <span className="check-mark" aria-hidden="true">
                      {check.ok ? (
                        <Tick />
                      ) : (
                        <span
                          className={`check-open ${check.optional || (library && check.step !== "storage") ? "optional" : ""}`}
                        />
                      )}
                    </span>
                    <div>
                      <h3>{check.label}</h3>
                      <p>{check.value}</p>
                    </div>
                    {(!library || check.step === "storage") && (
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
                Keys are tested on the first request. Nothing is downloaded or
                charged during setup.{" "}
                <Link to="/settings/defaults">Set spending limits</Link>
              </p>
              {!state.can_finish && (
                <p role="status" className="muted">
                  Finish the open steps, or come back later.
                </p>
              )}
              {bar(
                <>
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
                    {library ? "Finish and import" : "Finish and find something"}
                  </button>
                </>,
              )}
            </section>
          )}
        </>
      )}
    </Page>
  );
}

/** Keys never come back to the browser; each field says whether the server has one. */
function KeysStep({
  bar,
  onDone,
}: {
  bar: (actions: ReactNode) => ReactNode;
  onDone: () => Promise<void>;
}) {
  const config = useResource(() => api<Record<string, unknown>>("/admin/config"));
  const [draft, setDraft] = useState<Partial<Record<KeyName, string>>>({});
  const [missing, setMissing] = useState<KeyName[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const typed = Object.fromEntries(
    Object.entries(draft)
      .map(([name, value]) => [name, (value || "").trim()])
      .filter(([, value]) => value),
  );
  const changed = Object.keys(typed).length > 0;
  async function save(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      let saved = config.data!;
      if (changed) {
        saved = await patch<Record<string, unknown>>("/admin/config", typed);
        config.setData(saved);
        setDraft({});
      }
      const absent = KEYS.filter(
        (key) => key.required && !saved[`${key.name}_configured`],
      ).map((key) => key.name);
      setMissing(absent);
      if (absent.length) {
        requestAnimationFrame(() =>
          document
            .querySelector<HTMLInputElement>(".key-field.invalid input")
            ?.focus(),
        );
        return;
      }
      await onDone();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <form className="setup-step" aria-labelledby="keys-step" onSubmit={save}>
      <h2 id="keys-step" className="setup-heading">
        Keys
      </h2>
      <p className="setup-lede">
        Kept on this server and never shown again.
      </p>
      <ErrorNote error={config.error} retry={config.refresh} />
      {!config.data ? (
        config.loading && <Loading label="Loading keys" />
      ) : (
        <div className="form-grid keys-grid">
          {KEYS.map((spec) => (
            <KeyField
              key={spec.name}
              spec={spec}
              saved={!!config.data![`${spec.name}_configured`]}
              value={draft[spec.name] || ""}
              error={
                missing.includes(spec.name) && !draft[spec.name]?.trim()
                  ? "Needed to find and download."
                  : undefined
              }
              onChange={(value) => setDraft({ ...draft, [spec.name]: value })}
            />
          ))}
        </div>
      )}
      {error && (
        <p className="field-error" role="alert">
          {error}
        </p>
      )}
      {bar(
        <button
          className="btn primary"
          type="submit"
          disabled={busy || !config.data}
        >
          {busy ? "Saving…" : changed ? "Save and continue" : "Continue"}
        </button>,
      )}
    </form>
  );
}
