import { Link } from "react-router-dom";
import { ArrowRight, Play } from "lucide-react";
import { Circled, Logotype, Tick } from "./Brand";

export default function Landing({ needsSetup }: { needsSetup: boolean }) {
  const today = new Date().toLocaleDateString([], {
    weekday: "long",
    day: "numeric",
    month: "long",
  });
  return (
    <main className="cover-page" id="main-content">
      <header className="cover-top">
        <Logotype />
        <p>
          <span>Home edition</span>
          <span className="num">{today}</span>
        </p>
      </header>
      <section className="cover-body">
        <div className="cover-lines">
          <h1 className="display">Say what you want to watch.</h1>
          <p className="lede">
            Sparrow finds it and adds it to your collection.
          </p>
          <div className="actions">
            <Link className="btn primary" to={needsSetup ? "/setup" : "/login"}>
              {needsSetup ? "Set up Sparrow" : "Sign in"}
              <ArrowRight size={20} strokeWidth={2.5} aria-hidden="true" />
            </Link>
          </div>
        </div>
        <figure className="cover-demo">
          <div className="bar">
            <h2>Tonight</h2>
            <span className="bar-end">Example</span>
          </div>
          <ol>
            <li className="demo-row">
              <span className="slot-time num">20:30</span>
              <span className="slot-body">
                <Circled>
                  <strong>“Something clever we can finish tonight”</strong>
                </Circled>
                <span className="meta">Asked Sparrow · 3 picks</span>
              </span>
            </li>
            <li className="demo-row">
              <span className="slot-time num">20:42</span>
              <span className="slot-body">
                <strong>Northern Signal</strong>
                <span className="meta">Season 2 · new</span>
              </span>
              <Tick />
            </li>
            <li className="demo-row">
              <span className="slot-time num">21:00</span>
              <span className="slot-body">
                <strong>Harbour Lights</strong>
                <span className="meta leader-line">
                  <span>S1 E3</span>
                  <span className="leader" aria-hidden="true" />
                  <span>24 min left</span>
                </span>
                <span className="meter" aria-hidden="true">
                  <span style={{ width: "58%" }} />
                </span>
              </span>
              <span className="play-dot static" aria-hidden="true">
                <Play size={16} fill="currentColor" strokeWidth={0} />
              </span>
            </li>
          </ol>
          <figcaption className="sr-only">
            An example listing: a question for Sparrow, a new season and an
            episode to resume.
          </figcaption>
        </figure>
      </section>
      <footer className="cover-foot">Runs on your own server.</footer>
    </main>
  );
}
