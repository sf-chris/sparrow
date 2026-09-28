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
    <Section kicker="Pocket edition" title="Sparrow on your phone">
      <div className="sp-sheet">
        <p className="sp-hint">
          {installed
            ? "Sparrow is installed on this device."
            : !isSecureContext
              ? "Use Sparrow in this browser. Installing it on your home screen requires a trusted HTTPS address."
              : prompt
                ? "Keep your collection a tap away with the Sparrow web app."
                : "Use your browser’s Add to Home Screen or Install option when available. On iPhone, open Safari’s Share menu."}
        </p>
        {prompt && (
          <button
            className="sp-btn sp-btn-line sp-install"
            onClick={async () => {
              await prompt.prompt();
              await prompt.userChoice;
              setPrompt(null);
            }}
          >
            Install Sparrow
          </button>
        )}
      </div>
    </Section>
  );
}
