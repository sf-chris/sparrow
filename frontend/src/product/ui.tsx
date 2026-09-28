import {
  useCallback,
  useEffect,
  useRef,
  useState,
  useId,
  cloneElement,
  isValidElement,
  type CSSProperties,
  type ReactElement,
  type ReactNode,
} from "react";
import { Link, useLocation } from "react-router-dom";
import { AlertTriangle, ArrowRight, Play, RotateCcw, X } from "lucide-react";
import { Mark } from "./Brand";
import type { Item } from "./api";

export function Page({
  title,
  kicker,
  description,
  action,
  className = "",
  embedded = false,
  children,
}: {
  title: string;
  kicker?: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
  className?: string;
  embedded?: boolean;
  children: ReactNode;
}) {
  const Container = embedded ? "section" : "main";
  const Heading = embedded ? "h2" : "h1";
  return (
    <Container
      id={embedded ? undefined : "main-content"}
      className={`${embedded ? "sp-embedded" : "sp-page"} ${className}`}
    >
      {title && (
        <header className="sp-masthead">
          <div>
            {kicker && <span className="sp-label">{kicker}</span>}
            <Heading>{title}</Heading>
            {description && <p className="sp-lede">{description}</p>}
          </div>
          {action && <div className="sp-actions">{action}</div>}
        </header>
      )}
      {children}
    </Container>
  );
}

export function Section({
  title,
  kicker,
  description,
  action,
  className = "",
  children,
}: {
  title: string;
  kicker?: string;
  description?: ReactNode;
  action?: ReactNode;
  className?: string;
  children: ReactNode;
}) {
  const id = useId();
  return (
    <section className={`sp-section ${className}`} aria-labelledby={id}>
      <div className="sp-section-head">
        <div>
          {kicker && <span className="sp-label">{kicker}</span>}
          <h2 id={id}>{title}</h2>
          {description && <p>{description}</p>}
        </div>
        {action}
      </div>
      {children}
    </section>
  );
}

export function ErrorNote({
  error,
  retry,
}: {
  error: string;
  retry?: () => void;
}) {
  if (!error) return null;
  return (
    <div className="sp-error" role="alert">
      <AlertTriangle size={20} aria-hidden="true" />
      <p>{error}</p>
      {retry && (
        <button className="sp-btn sp-btn-line sp-btn-small" onClick={retry}>
          <RotateCcw size={14} aria-hidden="true" />
          Try again
        </button>
      )}
    </div>
  );
}

export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="sp-loading" role="status">
      <span>{label}</span>
    </div>
  );
}

export function Empty({
  title,
  children,
  action,
}: {
  title: string;
  children?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="sp-empty">
      <Mark />
      <h2>{title}</h2>
      {children && <p>{children}</p>}
      {action && <div className="sp-actions">{action}</div>}
    </div>
  );
}

export function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: ReactNode;
  children: ReactNode;
}) {
  const id = useId(),
    hintId = id + "-hint";
  const direct =
    isValidElement(children) &&
    typeof children.type === "string" &&
    ["input", "select", "textarea"].includes(children.type);
  if (direct)
    return (
      <div className="sp-field">
        <label htmlFor={id}>{label}</label>
        {cloneElement(
          children as ReactElement<{ id: string; "aria-describedby"?: string }>,
          { id, "aria-describedby": hint ? hintId : undefined },
        )}
        {hint && <small id={hintId}>{hint}</small>}
      </div>
    );
  return (
    <div className="sp-field" role="group" aria-labelledby={id}>
      <span className="sp-field-label" id={id}>
        {label}
      </span>
      {children}
      {hint && <small>{hint}</small>}
    </div>
  );
}

const statuses: Record<string, [string, string]> = {
  ready: ["Ready", "is-ok"],
  subtitles_pending: ["No subtitles yet", "is-warn"],
  unavailable: ["Storage offline", "is-bad"],
  verifying: ["Checking", "is-warn"],
  active: ["In progress", "is-live"],
  paused: ["Paused", "is-quiet"],
  complete: ["Done", "is-ok"],
  abandoned: ["Needs attention", "is-warn"],
  pending: ["Preparing", "is-quiet"],
  failed: ["Needs attention", "is-warn"],
};
export function Status({ value }: { value: string }) {
  const [label, tone] = statuses[value] || [value, "is-quiet"];
  return <span className={`sp-status ${tone}`}>{label}</span>;
}

export const duration = (seconds: number) =>
  seconds < 60
    ? "Under a minute"
    : seconds >= 3600
      ? `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`
      : `${Math.floor(seconds / 60)} min`;
export const bytes = (value: number) =>
  value >= 1e9 ? `${(value / 1e9).toFixed(1)} GB` : `${Math.round(value / 1e6)} MB`;
export const itemLink = (item: Item) =>
  item.tmdb_id ? `/title/${item.media_type}/${item.tmdb_id}` : `/items/${item.id}`;
export const kind = (mediaType: "movie" | "tv") => (mediaType === "tv" ? "Series" : "Film");
export const percent = (position: number, total: number) =>
  Math.round(Math.min(100, Math.max(0, (position / Math.max(1, total)) * 100)));

const posterStyles = [
  { bg: "#17140f", ink: "#eee8db", accent: "#cc3a16" },
  { bg: "#cc3a16", ink: "#17140f", accent: "#f7f3ea" },
  { bg: "#e4dccb", ink: "#17140f", accent: "#cc3a16" },
  { bg: "#2b2821", ink: "#eee8db", accent: "#c99a2e" },
];
function hash(text: string) {
  let value = 7;
  for (const char of text) value = (value * 31 + char.charCodeAt(0)) >>> 0;
  return value;
}
/** Catalogue artwork, or a typographic print generated from the title. */
export function Poster({ title, src }: { title: string; src?: string }) {
  const [failed, setFailed] = useState(false);
  useEffect(() => setFailed(false), [src]);
  if (src && !failed)
    return <img src={src} alt="" loading="lazy" onError={() => setFailed(true)} />;
  const seed = hash(title);
  const style = posterStyles[seed % posterStyles.length];
  return (
    <div
      className="sp-poster"
      aria-hidden="true"
      style={
        {
          "--poster-bg": style.bg,
          "--poster-ink": style.ink,
          "--poster-accent": style.accent,
          "--poster-tilt": `${(seed % 15) - 7}deg`,
        } as CSSProperties
      }
    >
      <span className="sp-poster-title">{title}</span>
    </div>
  );
}

export function Progress({
  value,
  label,
  className,
}: {
  value: number;
  label: string;
  className: string;
}) {
  return (
    <div
      className={className}
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={value}
    >
      <span style={{ width: `${value}%` }} />
    </div>
  );
}

/** A poster card for a title in the library. */
export function PrintCard({ item }: { item: Item }) {
  const location = useLocation();
  const watching = item.assets.find(
    (a) => a.watch && !a.watch.watched && a.watch.position > 5,
  );
  return (
    <article className="sp-print">
      <Link
        to={itemLink(item)}
        state={{ from: location.pathname + location.search }}
        aria-label={`Open ${item.title}`}
      >
        <div className="sp-print-art">
          <Poster title={item.title} src={item.poster_url} />
          {item.state === "ready" && (
            <span className="sp-print-play sp-play-disc" aria-hidden="true">
              <Play size={18} fill="currentColor" />
            </span>
          )}
          {watching && (
            <Progress
              className="sp-print-progress"
              label="Watch progress"
              value={percent(watching.watch!.position, watching.watch!.duration)}
            />
          )}
        </div>
        <div className="sp-print-caption">
          <span className="sp-label">
            <span>{kind(item.media_type)}</span>
            <span>{item.year || ""}</span>
          </span>
          <h3>{item.title}</h3>
          {item.state !== "ready" && <Status value={item.state} />}
        </div>
      </Link>
    </article>
  );
}

export function useResource<T>(load: () => Promise<T>, dependencies: unknown[] = []) {
  const loader = useRef(load);
  loader.current = load;
  const generation = useRef(0);
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const refresh = useCallback(async () => {
    const current = ++generation.current;
    setLoading(true);
    try {
      const result = await loader.current();
      if (current === generation.current) {
        setData(result);
        setError("");
      }
    } catch (e) {
      if (current === generation.current)
        setError(e instanceof Error ? e.message : "Could not load this page.");
    } finally {
      if (current === generation.current) setLoading(false);
    }
  }, []);
  useEffect(() => {
    void refresh();
    return () => {
      generation.current++;
    };
  }, [refresh, ...dependencies]);
  return { data, error, loading, refresh, setData };
}

export function ActionLink({
  to,
  children,
  solid = false,
}: {
  to: string;
  children: ReactNode;
  solid?: boolean;
}) {
  return (
    <Link className={`sp-btn ${solid ? "sp-btn-solid" : "sp-btn-line"}`} to={to}>
      {children}
      <ArrowRight size={16} aria-hidden="true" />
    </Link>
  );
}

export function Dialog({
  title,
  children,
  footer,
  onClose,
}: {
  title: string;
  children: ReactNode;
  footer?: ReactNode;
  onClose: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const trigger = useRef<HTMLElement | null>(null);
  const headingId = useId();
  function focusable() {
    return Array.from(
      ref.current?.querySelectorAll<HTMLElement>(
        'button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), summary, [tabindex]:not([tabindex="-1"])',
      ) || [],
    ).filter((element) => element.getClientRects().length > 0);
  }
  useEffect(() => {
    const dialog = ref.current;
    trigger.current ||= document.activeElement as HTMLElement;
    dialog?.showModal();
    function trap(event: KeyboardEvent) {
      if (event.key !== "Tab" || !dialog?.matches(":modal")) return;
      const controls = focusable();
      const first = controls[0],
        last = controls[controls.length - 1];
      if (
        !dialog.contains(document.activeElement) ||
        (event.shiftKey && document.activeElement === first) ||
        (!event.shiftKey && document.activeElement === last)
      ) {
        event.preventDefault();
        (event.shiftKey ? last : first)?.focus();
      }
    }
    document.addEventListener("keydown", trap, true);
    return () => {
      document.removeEventListener("keydown", trap, true);
      dialog?.close();
      if (trigger.current?.isConnected) trigger.current.focus();
    };
  }, []);
  useEffect(() => {
    // Forms can replace their focused submit button with a success result.
    if (ref.current?.open && !ref.current.contains(document.activeElement)) {
      focusable()[0]?.focus();
    }
  });
  return (
    <dialog
      className="sp-dialog"
      ref={ref}
      aria-labelledby={headingId}
      onCancel={onClose}
      onClick={(e) => {
        if (e.target !== ref.current) return;
        const bounds = ref.current.getBoundingClientRect();
        if (
          e.clientX < bounds.left ||
          e.clientX > bounds.right ||
          e.clientY < bounds.top ||
          e.clientY > bounds.bottom
        )
          onClose();
      }}
    >
      <div className="sp-dialog-head">
        <h2 id={headingId}>{title}</h2>
        <button className="sp-dialog-close" aria-label="Close dialog" onClick={onClose}>
          <X size={20} />
        </button>
      </div>
      <div className="sp-dialog-body">{children}</div>
      {footer && <div className="sp-dialog-foot">{footer}</div>}
    </dialog>
  );
}
