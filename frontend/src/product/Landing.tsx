import { Link } from "react-router-dom";
import { ArrowRight, ArrowDown } from "lucide-react";
import { Projection, Wordmark } from "./Brand";

const marquee = [
  "Films",
  "Series",
  "That one with the lighthouse",
  "Subtitles that line up",
  "Pick up where you left off",
  "A seat for everyone",
];

const programme = [
  {
    act: "I",
    title: "Ask for anything.",
    body: "Type a title, or describe a feeling — “a clever mystery we can finish tonight”. Sparrow finds the match and shows you exactly what you’re getting before anything starts.",
  },
  {
    act: "II",
    title: "It does the legwork.",
    body: "Sparrow searches, fetches and inspects every file, lines up subtitles and names everything properly. It keeps a plain-English journal, so you can always see what it did and why.",
  },
  {
    act: "III",
    title: "Everyone gets a seat.",
    body: "Invite your household. Each person keeps their own watch history and preferences, and picks up exactly where they left off.",
  },
];

export default function Landing({ needsSetup }: { needsSetup: boolean }) {
  const entry = needsSetup ? "/setup" : "/login";
  return (
    <div className="sp-front">
      <header className="sp-front-bar">
        <Link to="/" aria-label="Sparrow home" className="sp-bar-brand">
          <Wordmark />
        </Link>
        <nav aria-label="Welcome navigation" className="sp-actions">
          <a className="sp-btn sp-btn-ghost sp-front-about" href="#programme">
            How it works
          </a>
          <Link className="sp-btn sp-btn-solid" to={entry}>
            {needsSetup ? "Set up Sparrow" : "Sign in"}
          </Link>
        </nav>
      </header>
      <main id="main-content">
        <section className="sp-front-hero">
          <div className="sp-front-copy">
            <p className="sp-label">A private picture house · on your own server</p>
            <h1>
              Say the word.{" "}
              <span>Dim the lights.</span>
            </h1>
            <p className="sp-front-lede">
              Sparrow is a picture house for your household. Ask for a film or a whole
              series and it finds a good copy, brings it home, checks it’s the real
              thing and files it away — then keeps your collection in shape while you
              get on with the evening.
            </p>
            <div className="sp-actions">
              <Link className="sp-btn sp-btn-play" to={entry}>
                {needsSetup ? "Open the house" : "Take your seat"}
                <ArrowRight size={18} aria-hidden="true" />
              </Link>
              <a className="sp-btn sp-btn-ghost" href="#programme">
                <ArrowDown size={16} aria-hidden="true" />
                See the programme
              </a>
            </div>
          </div>
          <Projection className="sp-front-art" />
        </section>
        <div className="sp-marquee" aria-hidden="true">
          <div>
            {[0, 1].map((copy) => (
              <span key={copy}>
                {marquee.map((line) => (
                  <span key={line}>
                    {line}
                    <i>✶</i>
                  </span>
                ))}
              </span>
            ))}
          </div>
        </div>
        <section className="sp-programme" id="programme" aria-labelledby="programme-title">
          <div className="sp-programme-head">
            <p className="sp-label">Tonight’s programme</p>
            <h2 id="programme-title">A little bird takes care of the rest.</h2>
          </div>
          <ol>
            {programme.map(({ act, title, body }) => (
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
        <section className="sp-front-close">
          <h2>The house is open.</h2>
          <Link className="sp-btn sp-btn-solid" to={entry}>
            {needsSetup ? "Set up your server" : "Sign in to Sparrow"}
            <ArrowRight size={16} aria-hidden="true" />
          </Link>
        </section>
      </main>
      <footer className="sp-foot">
        <div>
          <span className="sp-foot-brand">Sparrow</span>
          <span className="sp-label">Runs on your server · your collection stays yours</span>
        </div>
        <span className="sp-label">No subscription · no adverts</span>
      </footer>
    </div>
  );
}
