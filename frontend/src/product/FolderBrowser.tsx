import { useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { ChevronRight, Folder, FolderPlus, HardDrive } from "lucide-react";
import { api, post } from "./api";
import { Tick } from "./Brand";
import { Dialog, bytes } from "./ui";

export type FolderRow = {
  name: string;
  path: string;
  readable: boolean;
  writable: boolean;
  free_bytes?: number;
};
type Listing = {
  path: string;
  parent: string | null;
  crumbs: { name: string; path: string }[];
  folders: FolderRow[];
  more: number;
  readable?: boolean;
  writable?: boolean;
  free_bytes?: number;
};

const forbidden = /[\\/:*?"<>|\u0000-\u001f]/;
const reserved = /^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\.|$)/i;

/** The server's rules for a new folder's name, checked as it's typed. */
export function nameProblem(name: string) {
  if (!name) return "";
  if (name.length > 100) return "Use a shorter name.";
  if (forbidden.test(name))
    return 'Folder names can’t contain / \\ : * ? " < > |';
  if (name.startsWith(".")) return "Folder names can’t start with a dot.";
  if (/[ .]$/.test(name)) return "Folder names can’t end with a space or a dot.";
  if (reserved.test(name)) return `${name} is reserved on Windows.`;
  return "";
}

function parts(path: string) {
  const windows = /^[a-z]:\\/i.test(path) || path.includes("\\");
  const clean = path.replace(/[\\/]+$/, "") || path;
  return {
    windows,
    key: windows ? clean.toLowerCase().replace(/\//g, "\\") : clean,
    sep: windows ? "\\" : "/",
  };
}

/** Why these folders can't work together, in the server's words. */
export function overlap(slots: { label: string; value: string }[]) {
  const chosen = slots.filter((s) => s.value);
  for (let i = 0; i < chosen.length; i++) {
    for (const second of chosen.slice(i + 1)) {
      const first = chosen[i];
      const a = parts(first.value),
        b = parts(second.value);
      if (a.key === b.key)
        return `${first.label} and ${second.label} can’t be the same folder.`;
      const inside = (x: typeof a, y: typeof a) => x.key.startsWith(y.key + y.sep);
      const [inner, outer] = inside(b, a)
        ? [second.label, first.label]
        : inside(a, b)
          ? [first.label, second.label]
          : ["", ""];
      if (!inner) continue;
      if (inner === "Incoming")
        return `Incoming can’t be inside ${outer}: downloads in progress would look like part of your collection.`;
      if (outer === "Incoming")
        return `${inner} can’t be inside Incoming: finished titles would be treated as downloads.`;
      return `${inner} can’t be inside ${outer}.`;
    }
  }
  return "";
}

export function parentOf(path: string) {
  const { sep } = parts(path);
  const cut = path.replace(/[\\/]+$/, "").lastIndexOf(sep);
  return cut > 0 ? path.slice(0, cut) : "";
}

/** A folder's own name: "Films" for /mnt/media/Films. */
export function folderName(path: string) {
  const clean = path.replace(/[\\/]+$/, "");
  return clean.slice(clean.lastIndexOf(parts(clean).sep) + 1) || path;
}

const same = (a: string, b: string) => !!a && !!b && parts(a).key === parts(b).key;

function state(row: FolderRow, current: string) {
  if (same(row.path, current)) return "In use";
  if (!row.readable) return "Can’t open";
  if (!row.writable) return "Read only";
  return row.free_bytes !== undefined ? `${bytes(row.free_bytes)} free` : "";
}

/** Something to settle before a folder is used, such as what happens to the
 *  titles already kept. The first choice is the usual one. */
export type Question = {
  text: string;
  choices: { value: string; label: string; hint: string; button: string }[];
};

/** Choose one folder on one computer. Clicking a folder selects it and opening
 *  it looks inside, so every click shows where it went. The one button names
 *  what will happen to the selected folder, or the open one when none is. */
export function FolderChooser({
  nodeId,
  title,
  intro,
  current = "",
  start = "",
  verb,
  write = false,
  problem,
  question,
  onChoose,
  onClose,
}: {
  nodeId: string;
  title: string;
  intro?: ReactNode;
  /** The folder in use now: it opens beside its neighbours, selected. */
  current?: string;
  /** Where to begin when nothing is in use yet. */
  start?: string;
  /** The button, for a folder's name: “Keep titles in Films”. */
  verb: (name: string) => string;
  /** Downloads need a folder Sparrow can save into. */
  write?: boolean;
  /** Why a folder can't be used, given the computer's other folders. */
  problem?: (path: string) => string;
  question?: (path: string) => Question | null;
  onChoose: (path: string, answer?: string) => Promise<void>;
  onClose: () => void;
}) {
  const [listing, setListing] = useState<Listing | null>(null);
  const [selected, setSelected] = useState<FolderRow | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [naming, setNaming] = useState(false);
  const [name, setName] = useState("");
  const [creating, setCreating] = useState(false);
  const [busy, setBusy] = useState(false);
  const [asking, setAsking] = useState<(Question & { path: string }) | null>(null);
  const [answer, setAnswer] = useState("");
  // Docker shows one place (/media): open it, and don't offer a way above it.
  const [single, setSingle] = useState(false);
  const list = useRef<HTMLUListElement>(null);
  const request = useRef(0);

  async function open(
    path: string,
    { select = "", focus = false, retry = true }: { select?: string; focus?: boolean; retry?: boolean } = {},
  ): Promise<void> {
    const current = ++request.current;
    setLoading(true);
    setError("");
    try {
      const next = await api<Listing>(
        `/admin/storage/browse?node_id=${encodeURIComponent(nodeId)}&path=${encodeURIComponent(path)}`,
      );
      if (current !== request.current) return;
      // One place only (Docker's /media): start inside it.
      if (!path && next.folders.length === 1 && !select) {
        setSingle(true);
        return open(next.folders[0].path, { focus });
      }
      const found = select ? next.folders.find((f) => same(f.path, select)) : undefined;
      if (select && !found && retry) return open(select, { focus, retry: false });
      setListing(next);
      setSelected(found || null);
      setNaming(false);
      requestAnimationFrame(() => {
        const rows = list.current;
        const row = rows?.querySelector<HTMLElement>(".selected .picker-row");
        row?.scrollIntoView({ block: "nearest" });
        if (focus) (row || rows?.querySelector<HTMLElement>(".picker-row"))?.focus();
      });
    } catch (e) {
      if (current !== request.current) return;
      const message = (e as Error).message;
      // A folder that's gone: show its parent. One outside every place: the places.
      if (path && /isn’t in a place/.test(message)) return open("", { focus });
      if (path && /isn’t there/.test(message))
        return open(parentOf(path), { select: retry ? select : "", focus, retry: false });
      setError(message);
    } finally {
      if (current === request.current) setLoading(false);
    }
  }

  useEffect(() => {
    const begin = current || start;
    void (async () => {
      if (begin) {
        // Know whether there's more than one place before opening a folder in it.
        try {
          const places = await api<Listing>(
            `/admin/storage/browse?node_id=${encodeURIComponent(nodeId)}&path=`,
          );
          setSingle(places.folders.length === 1);
        } catch {
          /* The folder itself will say what's wrong. */
        }
        await open(parentOf(begin), { select: begin });
      } else await open("");
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodeId]);

  async function create() {
    const clean = name.trim();
    const problem = nameProblem(clean) || (!clean ? "Name the folder." : "");
    if (problem || !listing?.path) {
      setError(problem);
      return;
    }
    setCreating(true);
    setError("");
    try {
      const made = await post<{ path: string }>("/admin/storage/browse/folder", {
        node_id: nodeId,
        path: listing.path,
        name: clean,
      });
      setName("");
      // The new folder, selected where it was made: one press uses it.
      await open(listing.path, { select: made.path, focus: true });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setCreating(false);
    }
  }

  function keys(event: KeyboardEvent<HTMLUListElement>) {
    const rows = Array.from(
      list.current?.querySelectorAll<HTMLElement>(".picker-row") || [],
    );
    const at = rows.indexOf(document.activeElement as HTMLElement);
    const go = (n: number) => {
      event.preventDefault();
      rows[Math.max(0, Math.min(rows.length - 1, n))]?.focus();
    };
    if (event.key === "ArrowDown") go(at + 1);
    else if (event.key === "ArrowUp") go(at - 1);
    else if (event.key === "Home") go(0);
    else if (event.key === "End") go(rows.length - 1);
    else if (event.key === "ArrowRight" && at >= 0 && listing?.folders[at]?.readable) {
      event.preventDefault();
      void open(listing.folders[at].path, { focus: true });
    } else if (
      (event.key === "ArrowLeft" || event.key === "Backspace") &&
      listing?.parent !== null &&
      !(single && !listing?.parent)
    ) {
      event.preventDefault();
      const here = listing?.path || "";
      void open(listing?.parent || "", { select: here, focus: true });
    }
  }

  // The selected folder, else the open one.
  const here = listing?.path
    ? {
        name: listing.crumbs[listing.crumbs.length - 1]?.name || folderName(listing.path),
        path: listing.path,
        readable: listing.readable !== false,
        writable: listing.writable !== false,
      }
    : null;
  const target = selected || here;
  const unchanged = !!target && same(target.path, current);
  const issue = !target
    ? ""
    : !target.readable
      ? "Sparrow can’t open this folder."
      : write && !target.writable
        ? "Sparrow can’t save here."
        : unchanged
          ? ""
          : problem?.(target.path) || "";
  const chosen = asking?.choices.find((c) => c.value === answer) || asking?.choices[0];

  async function use() {
    if (!target) return;
    if (!asking) {
      const ask = question?.(target.path);
      if (ask) {
        setAsking({ ...ask, path: target.path });
        setAnswer(ask.choices[0].value);
        return;
      }
    }
    setBusy(true);
    setError("");
    try {
      await onChoose(asking?.path || target.path, asking ? chosen?.value : undefined);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog
      title={title}
      onClose={onClose}
      footer={
        <>
          {(issue || error) && (
            <p className="sheet-problem problem-text" role="alert">
              {issue || error}
            </p>
          )}
          {asking && (
            <button className="btn quiet" disabled={busy} onClick={() => setAsking(null)}>
              Back
            </button>
          )}
          <button
            className="btn primary"
            disabled={!target || busy || loading || (!asking && (unchanged || !!issue))}
            onClick={() => void use()}
          >
            {busy
              ? "Saving…"
              : asking
                ? chosen?.button
                : target
                  ? verb(target.name)
                  : "Choose a folder"}
          </button>
        </>
      }
    >
      {asking ? (
        <div className="picker">
          <p>{asking.text}</p>
          <fieldset className="job-choices">
            <legend className="sr-only">What happens to them</legend>
            {asking.choices.map((choice) => (
              <label className="check job-choice" key={choice.value}>
                <input
                  type="radio"
                  name="folder-question"
                  checked={chosen?.value === choice.value}
                  onChange={() => setAnswer(choice.value)}
                  aria-describedby={`choice-${choice.value}`}
                />
                <span>
                  <strong>{choice.label}</strong>
                  <span className="muted" id={`choice-${choice.value}`}>
                    {choice.hint}
                  </span>
                </span>
              </label>
            ))}
          </fieldset>
        </div>
      ) : (
        <div className="picker">
          {intro && <p className="muted picker-intro">{intro}</p>}
          <nav className="crumbs" aria-label="Folder">
            {!single && (
              <button
                className="link"
                disabled={!listing?.path}
                onClick={() => void open("", { select: listing?.crumbs[0]?.path })}
              >
                Places
              </button>
            )}
            {listing?.crumbs.map((crumb, n) => (
              <span key={crumb.path}>
                {(!single || n > 0) && (
                  <ChevronRight size={14} strokeWidth={2.5} aria-hidden="true" />
                )}
                {n === listing.crumbs.length - 1 ? (
                  <span aria-current="location">{crumb.name}</span>
                ) : (
                  <button
                    className="link"
                    onClick={() =>
                      void open(crumb.path, { select: listing.crumbs[n + 1]?.path })
                    }
                  >
                    {crumb.name}
                  </button>
                )}
              </span>
            ))}
          </nav>
          <ul
            className="picker-list"
            ref={list}
            aria-label={listing?.path ? `Folders in ${here?.name}` : "Places"}
            aria-busy={loading}
            onKeyDown={keys}
          >
            {listing?.folders.map((row) => {
              const on = !!selected && same(selected.path, row.path);
              return (
                <li key={row.path} className={on ? "selected" : ""}>
                  <button
                    className="picker-row"
                    aria-pressed={on}
                    disabled={!row.readable}
                    onClick={() => setSelected(on ? null : row)}
                    onDoubleClick={() => void open(row.path, { focus: true })}
                  >
                    {listing.path ? (
                      <Folder size={18} strokeWidth={2} aria-hidden="true" />
                    ) : (
                      <HardDrive size={18} strokeWidth={2} aria-hidden="true" />
                    )}
                    <span className="picker-name">{row.name}</span>
                    {state(row, current) && (
                      <>
                        <span className="leader" aria-hidden="true" />
                        <span className="folder-state quiet num">{state(row, current)}</span>
                      </>
                    )}
                    {on && <Tick />}
                  </button>
                  <button
                    className="picker-open"
                    disabled={!row.readable}
                    aria-label={`Open ${row.name}`}
                    title={`Open ${row.name}`}
                    onClick={() => void open(row.path, { focus: true })}
                  >
                    <ChevronRight size={18} strokeWidth={2.5} aria-hidden="true" />
                  </button>
                </li>
              );
            })}
            {listing && !listing.folders.length && (
              <li className="picker-empty muted">
                {listing.path ? "No folders inside." : "No places to choose from."}
              </li>
            )}
            {!!listing?.more && (
              <li className="picker-empty muted">
                {listing.more} more not shown. Make a folder, or open one above.
              </li>
            )}
          </ul>
          {naming ? (
            <form
              className="picker-new"
              onSubmit={(e) => {
                e.preventDefault();
                void create();
              }}
            >
              <label className="sr-only" htmlFor="new-folder">
                New folder name
              </label>
              <input
                id="new-folder"
                autoFocus
                value={name}
                maxLength={100}
                placeholder="New folder"
                aria-invalid={!!nameProblem(name.trim())}
                aria-describedby={nameProblem(name.trim()) ? "new-folder-problem" : undefined}
                onChange={(e) => setName(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Escape") {
                    e.stopPropagation();
                    e.preventDefault();
                    setNaming(false);
                  }
                }}
              />
              <button
                className="btn"
                disabled={creating || !name.trim() || !!nameProblem(name.trim())}
              >
                {creating ? "Creating…" : "Create"}
              </button>
              <button type="button" className="btn quiet" onClick={() => setNaming(false)}>
                Cancel
              </button>
              {nameProblem(name.trim()) && (
                <p id="new-folder-problem" className="problem-text">
                  {nameProblem(name.trim())}
                </p>
              )}
            </form>
          ) : (
            <div className="picker-actions">
              <button
                className="btn quiet"
                disabled={!listing?.path || listing.writable === false || loading}
                onClick={() => {
                  setName("");
                  setNaming(true);
                }}
              >
                <FolderPlus size={17} strokeWidth={2.25} />
                {here ? `New folder in ${here.name}` : "New folder"}
              </button>
              {target && (target as FolderRow).free_bytes !== undefined ? (
                <span className="meta num picker-free">
                  {bytes((target as FolderRow).free_bytes!)} free
                </span>
              ) : (
                listing?.path &&
                listing.free_bytes !== undefined && (
                  <span className="meta num picker-free">{bytes(listing.free_bytes)} free</span>
                )
              )}
            </div>
          )}
        </div>
      )}
    </Dialog>
  );
}
