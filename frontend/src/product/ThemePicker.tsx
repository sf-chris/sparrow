import { useState } from "react";
import { Play } from "lucide-react";
import { api, type User } from "./api";
import { Tick } from "./Brand";
import { ErrorNote, Section } from "./ui";
import { applyTheme, themes } from "./theme";

export default function ThemePicker({
  user,
  onChanged,
}: {
  user: User;
  onChanged: () => void;
}) {
  const [chosen, setChosen] = useState(user.theme || "");
  const [error, setError] = useState("");
  async function choose(id: string) {
    const previous = chosen;
    setChosen(id);
    setError("");
    applyTheme(id);
    try {
      await api("/appearance", {
        method: "PUT",
        body: JSON.stringify({ theme: id }),
      });
      onChanged();
    } catch (e) {
      setChosen(previous);
      applyTheme(previous);
      setError((e as Error).message);
    }
  }
  return (
    <Section title="Theme">
      <ErrorNote error={error} />
      <div className="themes" role="radiogroup" aria-label="Theme">
        {themes.map((theme) => (
          <label className="theme-card" key={theme.id || "guide"}>
            <input
              type="radio"
              name="theme"
              value={theme.id}
              checked={chosen === theme.id}
              onChange={() => void choose(theme.id)}
            />
            <span className="theme-frame">
              <span
                className="theme-sample"
                data-theme-preview={theme.id || "guide"}
                aria-hidden="true"
              >
                <span className="theme-sample-band">Tonight</span>
                <span className="theme-sample-row">
                  <span className="theme-sample-dot">
                    <Play size={13} fill="currentColor" strokeWidth={0} />
                  </span>
                  Episode 3<small>24 min left</small>
                </span>
              </span>
            </span>
            <span className="theme-name">
              {theme.name}
              {chosen === theme.id && <Tick />}
            </span>
            <span className="theme-note">{theme.note}</span>
          </label>
        ))}
      </div>
    </Section>
  );
}
