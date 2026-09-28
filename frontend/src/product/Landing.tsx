import { Link } from "react-router-dom";
import { Projection, Wordmark } from "./Brand";

const steps = [
  { act: "I", title: "Ask", body: "Type a title, or describe what you’re in the mood for." },
  {
    act: "II",
    title: "Sparrow fetches it",
    body: "It finds a good copy, checks the file, adds subtitles and puts it in your library.",
  },
  {
    act: "III",
    title: "Watch",
    body: "Everyone in the household gets their own account, progress and preferences.",
  },
];

export default function Landing({ needsSetup }: { needsSetup: boolean }) {
  return (
    <div className="sp-front">
      <header className="sp-front-bar">
        <Link to="/" aria-label="Sparrow home" className="sp-bar-brand">
          <Wordmark />
        </Link>
        <Link className="sp-btn sp-btn-solid" to={needsSetup ? "/setup" : "/login"}>
          {needsSetup ? "Set up Sparrow" : "Sign in"}
        </Link>
      </header>
      <main id="main-content">
        <section className="sp-front-hero">
          <div className="sp-front-copy">
            <h1>
              Films and series <span>on request.</span>
            </h1>
            <p className="sp-front-lede">
              Ask for something to watch, and Sparrow finds a good copy, checks it and adds it to
              your library. It runs on your own server.
            </p>
          </div>
          <Projection className="sp-front-art" />
        </section>
        <section className="sp-programme" aria-labelledby="programme-title">
          <h2 className="sp-label" id="programme-title">
            How it works
          </h2>
          <ol>
            {steps.map(({ act, title, body }) => (
              <li key={act}>
                <span className="sp-programme-act" aria-hidden="true">
                  {act}
                </span>
                <h3>{title}</h3>
                <p>{body}</p>
              </li>
            ))}
          </ol>
        </section>
      </main>
    </div>
  );
}
