import { Link } from "react-router-dom";
import { ArrowRight } from "lucide-react";
import { Mark } from "./Brand";

// A shelf filling itself: abstract slots, no real or invented titles.
const shelf = [210, 28, -1, 150, 330, -1, 60, 250, 0, 190, -2, 290];

export default function Landing({ needsSetup }: { needsSetup: boolean }) {
  const entry = needsSetup ? "/setup" : "/login";
  return (
    <main className="sp-front">
      <header className="sp-front-bar">
        <Link className="sp-wordmark" to="/" aria-label="Sparrow home">
          <Mark />
          <span>sparrow</span>
        </Link>
        <Link className="sp-button secondary" to={entry}>
          {needsSetup ? "Set up Sparrow" : "Sign in"}
        </Link>
      </header>
      <section className="sp-front-hero">
        <div className="sp-front-copy">
          <h1>
            Ask for a film. <span>Sparrow does the rest.</span>
          </h1>
          <p>
            It finds what you asked for, checks it’s the real thing, files it in
            your library and keeps your shows up to date. You just press play.
          </p>
          <Link className="sp-button primary sp-button-large" to={entry}>
            {needsSetup ? "Set up your server" : "Open Sparrow"}
            <ArrowRight size={18} />
          </Link>
        </div>
        <div className="sp-shelf" aria-hidden="true">
          {shelf.map((hue, index) => (
            <span
              key={index}
              className={hue === -1 ? "arriving" : hue === -2 ? "empty" : ""}
              style={
                {
                  "--hue": hue,
                  "--delay": `${index * 90}ms`,
                } as React.CSSProperties
              }
            />
          ))}
        </div>
      </section>
      <ol className="sp-front-steps">
        <li>
          <strong>Ask.</strong> By name, or by mood — “something we can finish
          tonight”.
        </li>
        <li>
          <strong>Sparrow fetches.</strong> Every file is checked against the
          real runtime, never trusted by its name.
        </li>
        <li>
          <strong>Watch.</strong> On any screen in the house, with your own
          progress and preferences.
        </li>
      </ol>
    </main>
  );
}
