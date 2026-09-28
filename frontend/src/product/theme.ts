/** Interface themes. The empty id is Sparrow's official theme, the Guide. */
export const themes = [
  { id: "", name: "Guide", note: "Sparrow’s own TV guide.", colour: "#156068" },
  {
    id: "cinema",
    name: "Cinema",
    note: "Dark, for film nights and the TV.",
    colour: "#101116",
  },
  {
    id: "clear",
    name: "Clear",
    note: "Large type and strong contrast.",
    colour: "#000000",
  },
  {
    id: "saturday",
    name: "Saturday",
    note: "Bright and chunky, for kids.",
    colour: "#2f5fe0",
  },
  {
    id: "repertory",
    name: "Repertory",
    note: "A film programme, for film lovers.",
    colour: "#f7f6f2",
  },
  {
    id: "console",
    name: "Console",
    note: "Dense monospace, for whoever runs the server.",
    colour: "#0b0e0b",
  },
] as const;

const KEY = "sparrow-theme";

/** Applies a theme now and remembers it on this device for the next visit. */
export function applyTheme(id: string) {
  const theme = themes.find((t) => t.id === id) || themes[0];
  const root = document.documentElement;
  if (theme.id) root.dataset.theme = theme.id;
  else delete root.dataset.theme;
  document
    .querySelector('meta[name="theme-color"]')
    ?.setAttribute("content", theme.colour);
  try {
    if (theme.id) localStorage.setItem(KEY, theme.id);
    else localStorage.removeItem(KEY);
  } catch {
    // Private windows can refuse storage; the account still keeps the choice.
  }
}
