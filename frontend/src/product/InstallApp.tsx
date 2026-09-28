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
    <Section title="On your phone">
      <div className="row">
        <p className="muted">
          {installed
            ? "Sparrow is installed on this device."
            : !isSecureContext
              ? "Installing to a home screen needs a secure (HTTPS) address. Sparrow works in this browser either way."
              : prompt
                ? "Keep Sparrow one tap away."
                : "Use your browser’s Install or Add to Home Screen option. On iPhone, it’s in Safari’s Share menu."}
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
            Install Sparrow
          </button>
        )}
      </div>
    </Section>
  );
}
