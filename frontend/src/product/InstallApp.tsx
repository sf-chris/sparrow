import { useEffect, useState } from "react";
import { Section } from "./ui";
type InstallEvent = Event & {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: string }>;
};
export default function InstallApp() {
  const [prompt, setPrompt] = useState<InstallEvent | null>(null),
    [installed, setInstalled] = useState(
      matchMedia("(display-mode: standalone)").matches,
    );
  useEffect(() => {
    const before = (event: Event) => {
      event.preventDefault();
      setPrompt(event as InstallEvent);
    };
    const done = () => {
      setInstalled(true);
      setPrompt(null);
    };
    addEventListener("beforeinstallprompt", before);
    addEventListener("appinstalled", done);
    return () => {
      removeEventListener("beforeinstallprompt", before);
      removeEventListener("appinstalled", done);
    };
  }, []);
  return (
    <Section title="App">
      <div className="row">
        <p className="muted">
          {installed
            ? "Installed on this device."
            : !isSecureContext
              ? "Installing needs an HTTPS address. Sparrow works in the browser either way."
              : prompt
                ? "Add Sparrow to your home screen."
                : "Use your browser’s Install or Add to Home Screen option. On iPhone, it’s under Share in Safari."}
        </p>
        {prompt && (
          <button
            className="btn"
            onClick={async () => {
              await prompt.prompt();
              await prompt.userChoice;
              setPrompt(null);
            }}
          >
            Install
          </button>
        )}
      </div>
    </Section>
  );
}
