import { Link } from "react-router-dom";
import { Play } from "lucide-react";
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
          <span>The household guide to your own films and series</span>
          <span className="num">{today}</span>
        </p>
      </header>
      <section className="cover-body">
        <div className="cover-lines">
          <h1 className="display">Say what you want to watch.</h1>
          <p className="lede">
            Sparrow finds it, checks it and files it on your own server.
            Everyone at home picks up where they left off.
          </p>
          <div className="actions">
            <Link className="btn primary" to={needsSetup ? "/setup" : "/login"}>
              {needsSetup ? "Set up Sparrow" : "Sign in"}
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
                <span className="meta">
                  Sparrow suggests three. You pick one.
                </span>
              </span>
            </li>
            <li className="demo-row">
              <span className="slot-time num">20:42</span>
              <span className="slot-body">
                <strong>Northern Signal</strong>
                <span className="meta">Season 2 · checked and filed</span>
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
            An example evening: a request, a title arriving, and a show to
            resume.
          </figcaption>
        </figure>
      </section>
      <footer className="cover-foot">
        Runs on your own server. No Sparrow account needed.
      </footer>
    </main>
  );
}
