import { Link } from "react-router-dom";
import { ArrowRight, Play, Library, Wand2, Users } from "lucide-react";
import { Mark, PlayroomArt, Doodle } from "./Brand";

export default function Landing({ needsSetup }: { needsSetup: boolean }) {
  const entry = needsSetup ? "/setup" : "/login";
  return (
    <main className="sp-auth sp-landing">
      <header className="sp-landing-nav">
        <Link className="sp-brand" to="/" aria-label="Sparrow home">
          <Mark />
          <span>sparrow</span>
        </Link>
        <nav aria-label="Welcome navigation">
          <a className="sp-landing-about" href="#little-things">
            Meet Sparrow
          </a>
          <Link className="sp-button secondary" to={entry}>
            {needsSetup ? "Set up Sparrow" : "Sign in"}
            <ArrowRight size={15} />
          </Link>
        </nav>
      </header>
      <section className="sp-landing-hero">
        <div className="sp-landing-copy">
          <p className="sp-landing-tag">
            <span /> Your own little watch-world
          </p>
          <h1>
            Less scrolling.
            <br />
            More{" "}
            <span>
              good stuff.
              <svg viewBox="0 0 300 16" fill="none" aria-hidden="true">
                <path
                  d="M3 10c87-9 190-9 293-5M11 15c96-8 190-9 268-6"
                  stroke="currentColor"
                  strokeWidth="2.5"
                  strokeLinecap="round"
                />
              </svg>
            </span>
          </h1>
          <p>
            Your favourite films. That show you keep meaning to watch. A happy
            little home for all of it.
          </p>
          <Link className="sp-button primary" to={entry}>
            <Play size={14} fill="currentColor" />
            {needsSetup ? "Make yourself at home" : "Open your Sparrow"}
            <ArrowRight size={16} />
          </Link>
          <span className="sp-landing-small">
            Your collection. Your people. Your pace.
          </span>
        </div>
        <div className="sp-landing-illustration">
          <PlayroomArt />
          <span className="sp-hand-note">
            good company, great stories
            <Doodle kind="spark" />
          </span>
        </div>
      </section>
      <section
        className="sp-landing-features"
        id="little-things"
        aria-labelledby="little-things-title"
      >
        <div className="sp-landing-section-heading">
          <h2 id="little-things-title">
            A little bird takes care of the little things.
          </h2>
          <span>You get the good part.</span>
        </div>
        <div className="sp-landing-feature-grid">
          <article>
            <Library size={21} />
            <span className="sp-feature-number">01</span>
            <h3>All together now.</h3>
            <p>
              Bring your films and shows into one lovely, organised collection.
              Pick up right where you left off.
            </p>
          </article>
          <article>
            <Wand2 size={21} />
            <span className="sp-feature-number">02</span>
            <h3>A title. A mood. A maybe.</h3>
            <p>
              Find the one you had in mind, or let a little curiosity lead you
              to your next favourite.
            </p>
          </article>
          <article>
            <Users size={21} />
            <span className="sp-feature-number">03</span>
            <h3>Room for your people.</h3>
            <p>
              A shared collection, with watch history and preferences that are
              just yours. Everybody gets comfy.
            </p>
          </article>
        </div>
      </section>
      <footer className="sp-landing-footer">
        <span>
          <Mark /> A little less managing. A lot more watching.
        </span>
        <span>Make a night of it.</span>
      </footer>
    </main>
  );
}
