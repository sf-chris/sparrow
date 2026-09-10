import {
  useCallback,
  useEffect,
  useRef,
  useState,
  useId,
  cloneElement,
  isValidElement,
  type ReactElement,
  type ReactNode,
} from "react";
import { Link, useLocation } from "react-router-dom";
import {
  AlertCircle,
  ArrowRight,
  Loader2,
  RefreshCw,
  Play,
  ArrowUpRight,
  X,
} from "lucide-react";
import { Mark } from "./Brand";
import type { Item } from "./api";

export function Page({
  title,
  eyebrow,
  description,
  action,
  className = "",
  children,
}: {
  title: string;
  eyebrow?: string;
  description?: string;
  action?: ReactNode;
  className?: string;
  children: ReactNode;
}) {
  return (
    <main id="main-content" className={`sp-page ${className}`}>
      {title && (
        <header className="sp-page-heading">
          <div>
            {eyebrow && <p className="sp-eyebrow">{eyebrow}</p>}
            <h1>{title}</h1>
            {description && <p className="sp-description">{description}</p>}
          </div>
          {action}
        </header>
      )}
      {children}
    </main>
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
      <AlertCircle size={20} aria-hidden="true" />
      <p>{error}</p>
      {retry && (
        <button className="sp-button secondary" onClick={retry}>
          <RefreshCw size={16} />
          Try again
        </button>
      )}
    </div>
  );
}
export function Loading({
  label = "Loading your collection…",
}: {
  label?: string;
}) {
  return (
    <div className="sp-loading" role="status">
      <Loader2 className="animate-spin" size={22} />
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
  children: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="sp-empty">
      <span className="sp-empty-icon">
        <Mark />
      </span>
      <h2>{title}</h2>
      <p>{children}</p>
      {action}
    </div>
  );
}
export function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
}) {
  const id = useId(),
    descriptionId = id + "-description";
  const direct =
    isValidElement(children) &&
    typeof children.type === "string" &&
    ["input", "select", "textarea"].includes(children.type);
  const control = direct
    ? cloneElement(
        children as ReactElement<{ id: string; "aria-describedby"?: string }>,
        { id, "aria-describedby": hint ? descriptionId : undefined },
      )
    : children;
  return (
    <div className="sp-field">
      {direct ? (
        <>
          <label htmlFor={id}>{label}</label>
          {control}
        </>
      ) : (
        <>
          <span>{label}</span>
          <label>{control}</label>
        </>
      )}
      {hint && <small id={descriptionId}>{hint}</small>}
    </div>
  );
}
export function Section({
  title,
  description,
  children,
  action,
}: {
  title: string;
  description?: string;
  children: ReactNode;
  action?: ReactNode;
}) {
  return (
    <section className="sp-section">
      <div className="sp-section-heading">
        <div>
          <h2>{title}</h2>
          {description && <p>{description}</p>}
        </div>
        {action}
      </div>
      {children}
    </section>
  );
}
export function Status({ value }: { value: string }) {
  const labels: Record<string, string> = {
    ready: "Ready to watch",
    subtitles_pending: "Playable · subtitles needed",
    unavailable: "Storage unavailable",
    verifying: "Needs verification",
    active: "In progress",
    paused: "Paused",
    complete: "Complete",
    abandoned: "Needs attention",
    pending: "Preparing",
    failed: "Needs attention",
  };
  return <span className={`sp-status ${value}`}>{labels[value] || value}</span>;
}
export const duration = (seconds: number) =>
  seconds < 60
    ? "Less than a minute"
    : seconds >= 3600
      ? `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`
      : `${Math.floor(seconds / 60)} min`;
export const bytes = (value: number) =>
  value >= 1e9
    ? `${(value / 1e9).toFixed(1)} GB`
    : `${Math.round(value / 1e6)} MB`;
export const itemLink = (item: Item) =>
  item.tmdb_id
    ? `/title/${item.media_type}/${item.tmdb_id}`
    : `/items/${item.id}`;
export function Poster({
  title,
  src,
  className = "",
}: {
  title: string;
  src?: string;
  className?: string;
}) {
  const [failed, setFailed] = useState(false);
  useEffect(() => setFailed(false), [src]);
  return (
    <div className={`sp-poster ${className}`}>
      {src && !failed ? (
        <img src={src} alt="" loading="lazy" onError={() => setFailed(true)} />
      ) : (
        <div className="sp-poster-fallback">
          <span className="sp-poster-kicker">SPARROW / COLLECTION</span>
          <Mark />
          <span>{title}</span>
        </div>
      )}
    </div>
  );
}
export function MediaCard({ item }: { item: Item }) {
  const location = useLocation();
  const watching = item.assets.find(
    (a) => a.watch && !a.watch.watched && a.watch.position > 5,
  );
  return (
    <article className="sp-media-card">
      <Link
        to={itemLink(item)}
        state={{ from: location.pathname + location.search }}
        aria-label={`Open ${item.title}`}
      >
        <div className="sp-card-art">
          <Poster title={item.title} src={item.poster_url} />
          <span className="sp-card-type">
            {item.media_type === "tv" ? "Series" : "Film"}
          </span>
          <span className="sp-card-hover" aria-hidden="true">
            {item.state === "ready" ? (
              <Play size={20} fill="currentColor" />
            ) : (
              <ArrowUpRight size={20} />
            )}
          </span>
        </div>
        {watching && (
          <div
            className="sp-card-progress"
            role="progressbar"
            aria-label="Watch progress"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={Math.round(
              Math.min(
                100,
                Math.max(
                  0,
                  (watching.watch!.position /
                    Math.max(1, watching.watch!.duration)) *
                    100,
                ),
              ),
            )}
          >
            <span
              style={{
                width: `${Math.min(100, Math.max(0, (100 * watching.watch!.position) / Math.max(1, watching.watch!.duration)))}%`,
              }}
            />
          </div>
        )}
        <h3>{item.title}</h3>
        <p>
          {item.year || (item.media_type === "tv" ? "TV show" : "Movie")}
          {item.year && ` · ${item.media_type === "tv" ? "TV show" : "Movie"}`}
        </p>
        <Status value={item.state} />
      </Link>
    </article>
  );
}
export function useResource<T>(
  load: () => Promise<T>,
  dependencies: unknown[] = [],
) {
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
}: {
  to: string;
  children: ReactNode;
}) {
  return (
    <Link className="sp-button secondary" to={to}>
      {children}
      <ArrowRight size={16} />
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
  function focusable() {
    return Array.from(
      ref.current?.querySelectorAll<HTMLElement>(
        'button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])',
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
      className={`sp-dialog ${footer ? "sp-dialog-with-footer" : ""}`}
      ref={ref}
      aria-label={title}
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
      <div className="sp-dialog-heading">
        <h2>{title}</h2>
        <button
          className="sp-button quiet"
          aria-label="Close dialog"
          onClick={onClose}
        >
          <X size={18} />
        </button>
      </div>
      <div className="sp-dialog-content">{children}</div>
      {footer && <div className="sp-dialog-footer">{footer}</div>}
    </dialog>
  );
}
