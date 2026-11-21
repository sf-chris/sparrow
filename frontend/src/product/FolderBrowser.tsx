import { useEffect, useRef, useState, type KeyboardEvent } from "react";
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
export type Slot = {
  key: string;
  label: string;
  value: string;
  /** Incoming folders must be writable; libraries should be. */
  write?: boolean;
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

function parentOf(path: string) {
  const { sep } = parts(path);
  const cut = path.replace(/[\\/]+$/, "").lastIndexOf(sep);
  return cut > 0 ? path.slice(0, cut) : "";
}

function state(row: FolderRow) {
  if (!row.readable) return "Can’t open";
  if (!row.writable) return "Read only";
  return row.free_bytes !== undefined ? `${bytes(row.free_bytes)} free` : "";
}

/** Browse one computer's folders and choose a folder for each slot. */
export function FolderPicker({
  nodeId,
  slots: initial,
  onChange,
}: {
  nodeId: string;
  slots: Slot[];
  onChange: (slots: Slot[]) => void;
}) {
  const [slots, setSlots] = useState(initial);
  const [active, setActive] = useState(
    Math.max(
      0,
      initial.findIndex((s) => !s.value),
    ),
  );
  const [listing, setListing] = useState<Listing | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [naming, setNaming] = useState(false);
  const [name, setName] = useState("");
  const [creating, setCreating] = useState(false);
  // Docker shows one place (/media): open it, and don't offer a way above it.
  const [single, setSingle] = useState(false);
  const list = useRef<HTMLUListElement>(null);
  const request = useRef(0);

  async function open(path: string, focus = false) {
    const current = ++request.current;
    setLoading(true);
    setError("");
    try {
      const next = await api<Listing>(
        `/admin/storage/browse?node_id=${encodeURIComponent(nodeId)}&path=${encodeURIComponent(path)}`,
      );
      if (current !== request.current) return;
      // One place only (Docker's /media): start inside it.
      if (!path && next.folders.length === 1) {
        setSingle(true);
        return open(next.folders[0].path, focus);
      }
      setListing(next);
      setNaming(false);
      if (focus)
        requestAnimationFrame(() =>
          list.current?.querySelector<HTMLElement>("button")?.focus(),
        );
    } catch (e) {
      if (current !== request.current) return;
      const message = (e as Error).message;
      // A folder that's gone: show its parent. One outside every place: the places.
      if (path && /isn’t in a place/.test(message)) return open("", focus);
      if (path && /isn’t there/.test(message)) return open(parentOf(path), focus);
      setError(message);
    } finally {
      if (current === request.current) setLoading(false);
    }
  }

  useEffect(() => {
    const start = initial[active]?.value || initial.find((s) => s.value)?.value;
    void (async () => {
      if (start) {
        // Know whether there's more than one place before opening a folder in it.
        try {
          const places = await api<Listing>(
            `/admin/storage/browse?node_id=${encodeURIComponent(nodeId)}&path=`,
          );
          setSingle(places.folders.length === 1);
        } catch {
          /* The folder itself will say what's wrong. */
        }
      }
      await open(start ? parentOf(start) : "");
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodeId]);

  function choose() {
    if (!listing?.path) return;
    const next = slots.map((s, n) =>
      n === active ? { ...s, value: listing.path } : s,
    );
    setSlots(next);
    onChange(next);
    const empty = next.findIndex((s, n) => n !== active && !s.value);
    if (empty >= 0) {
      setActive(empty);
      // The next folder usually sits beside this one.
      if (listing.parent) void open(listing.parent);
    }
  }

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
      await open(made.path, true);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setCreating(false);
    }
  }

  function keys(event: KeyboardEvent<HTMLUListElement>) {
    const rows = Array.from(
      list.current?.querySelectorAll<HTMLElement>("button") || [],
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
    else if (
      (event.key === "ArrowLeft" || event.key === "Backspace") &&
      listing?.parent !== null &&
      !(single && !listing?.parent)
    ) {
      event.preventDefault();
      void open(listing?.parent || "", true);
    }
  }

  const slot = slots[active];
  const blocked = !listing?.path
    ? "Open a folder first."
    : listing.readable === false
      ? "Sparrow can’t open this folder."
      : slot?.write && listing.writable === false
        ? "Sparrow can’t save here."
        : "";
  const typed = nameProblem(name.trim());
  return (
    <div className="picker">
      <ul className="folder-rows picker-slots" aria-label="Chosen folders">
        {slots.map((s, n) => (
          <li key={s.key} className={n === active ? "active" : ""}>
            <button
              className="picker-slot"
              aria-pressed={n === active}
              onClick={() => {
                setActive(n);
                if (s.value) void open(parentOf(s.value));
              }}
            >
              <span className="folder-label">{s.label}</span>
              <span className={`path ${s.value ? "" : "suggested"}`}>
                {s.value || "Not chosen"}
              </span>
              <span className="leader" aria-hidden="true" />
              <span className="folder-state">
                {n === active ? "Choosing" : s.value ? <Tick /> : ""}
              </span>
            </button>
          </li>
        ))}
      </ul>
      <nav className="crumbs" aria-label="Folder">
        {!single && (
          <button
            className="link"
            disabled={!listing?.path}
            onClick={() => void open("")}
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
              <button className="link" onClick={() => void open(crumb.path)}>
                {crumb.name}
              </button>
            )}
          </span>
        ))}
      </nav>
      {error && (
        <p className="picker-error problem-text" role="alert">
          {error}
        </p>
      )}
      <ul
        className="picker-list"
        ref={list}
        aria-label={listing?.path ? "Folders inside" : "Places"}
        aria-busy={loading}
        onKeyDown={keys}
      >
        {listing?.folders.map((row) => (
          <li key={row.path}>
            <button
              disabled={!row.readable}
              onClick={() => void open(row.path, true)}
            >
              {listing.path ? (
                <Folder size={18} strokeWidth={2} aria-hidden="true" />
              ) : (
                <HardDrive size={18} strokeWidth={2} aria-hidden="true" />
              )}
              <span className="picker-name">{row.name}</span>
              {state(row) && (
                <>
                  <span className="leader" aria-hidden="true" />
                  <span className="folder-state quiet num">{state(row)}</span>
                </>
              )}
            </button>
          </li>
        ))}
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
            aria-invalid={!!typed}
            aria-describedby={typed ? "new-folder-problem" : undefined}
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
            disabled={creating || !name.trim() || !!typed}
          >
            {creating ? "Creating…" : "Create"}
          </button>
          <button
            type="button"
            className="btn quiet"
            onClick={() => setNaming(false)}
          >
            Cancel
          </button>
          {typed && (
            <p id="new-folder-problem" className="problem-text">
              {typed}
            </p>
          )}
        </form>
      ) : (
        <div className="picker-actions">
          <button
            className="btn"
            disabled={!!blocked || loading}
            title={blocked || undefined}
            onClick={choose}
          >
            Use this folder for {slot?.label}
          </button>
          <button
            className="btn quiet"
            disabled={!listing?.path || listing.writable === false || loading}
            onClick={() => {
              setName("");
              setNaming(true);
            }}
          >
            <FolderPlus size={17} strokeWidth={2.25} />
            New folder
          </button>
          {listing?.path && listing.free_bytes !== undefined && (
            <span className="meta num picker-free">
              {bytes(listing.free_bytes)} free here
            </span>
          )}
        </div>
      )}
    </div>
  );
}

/** A sheet for choosing folders, saved together with one button. */
export default function FolderSheet({
  title,
  nodeId,
  slots,
  onSave,
  onClose,
}: {
  title: string;
  nodeId: string;
  slots: Slot[];
  onSave: (values: Record<string, string>) => Promise<void>;
  onClose: () => void;
}) {
  const [chosen, setChosen] = useState(slots);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const problem = overlap(chosen);
  const ready = chosen.every((s) => s.value) && !problem;
  return (
    <Dialog
      title={title}
      onClose={onClose}
      footer={
        <>
          {(problem || error) && (
            <p className="sheet-problem problem-text" role="alert">
              {problem || error}
            </p>
          )}
          <button
            className="btn primary"
            disabled={!ready || busy}
            onClick={async () => {
              setBusy(true);
              setError("");
              try {
                await onSave(
                  Object.fromEntries(chosen.map((s) => [s.key, s.value])),
                );
              } catch (e) {
                setError((e as Error).message);
              } finally {
                setBusy(false);
              }
            }}
          >
            {busy ? "Saving…" : chosen.length > 1 ? "Save folders" : "Add folder"}
          </button>
        </>
      }
    >
      <FolderPicker nodeId={nodeId} slots={slots} onChange={setChosen} />
    </Dialog>
  );
}
