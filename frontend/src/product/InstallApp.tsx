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
    <Section title="Install the app">
      <div className="sp-sheet">
        <p className="sp-hint">
          {installed
            ? "Sparrow is installed on this device."
            : !isSecureContext
              ? "Installing needs an HTTPS address."
              : prompt
                ? "Add Sparrow to your home screen."
                : "Use your browser’s Install or Add to Home Screen option. On iPhone, it’s in Safari’s Share menu."}
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
