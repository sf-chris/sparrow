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
import { Scissors, X } from "lucide-react";
import { Bird } from "./Brand";
import type { Asset, Item } from "./api";

export function Page({
  title,
  lede,
  action,
  className = "",
  embedded = false,
  children,
}: {
  title: ReactNode;
  lede?: ReactNode;
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
      className={`${embedded ? "embedded" : "page"} ${className}`}
    >
      {title && (
        <header className="page-head">
          <div>
            <Heading className="display">{title}</Heading>
            {lede && <p className="lede">{lede}</p>}
          </div>
          {action && <div className="page-action">{action}</div>}
        </header>
      )}
      {children}
    </Container>
  );
}

/** A reversed listings bar: the guide's channel heading. */
export function Bar({
  title,
  children,
  id,
}: {
  title: ReactNode;
  children?: ReactNode;
  id?: string;
}) {
  return (
    <div className="bar">
      <h2 id={id}>{title}</h2>
      {children && <div className="bar-end">{children}</div>}
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
  description?: ReactNode;
  children: ReactNode;
  action?: ReactNode;
}) {
  return (
    <section className="section">
      <div className="section-head">
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

export function ErrorNote({
  error,
  retry,
}: {
  error: string;
  retry?: () => void;
}) {
  if (!error) return null;
  return (
    <div className="error-note" role="alert">
      <p>{error}</p>
      {retry && (
        <button className="btn quiet" onClick={retry}>
          Try again
        </button>
      )}
    </div>
  );
}

export function Loading({ label = "Loading" }: { label?: string }) {
  return (
    <div className="loading" role="status">
      <Bird />
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
    <div className="empty">
      <Bird />
      <h2>{title}</h2>
      {children && <p>{children}</p>}
      {action && <div className="actions">{action}</div>}
    </div>
  );
}

export function Field({
  label,
  hint,
  children,
  wide = false,
}: {
  label: string;
  hint?: ReactNode;
  children: ReactNode;
  wide?: boolean;
}) {
  const id = useId(),
    descriptionId = id + "-hint";
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
    <div className={`field ${wide ? "wide" : ""}`}>
      {direct ? (
        <>
          <label htmlFor={id}>{label}</label>
          {control}
        </>
      ) : (
        <>
          <span className="field-label">{label}</span>
          <label>{control}</label>
        </>
      )}
      {hint && <small id={descriptionId}>{hint}</small>}
    </div>
  );
}

/** Exceptions only: the normal, playable state carries no label. */
const flags: Record<string, [string, string]> = {
  subtitles_pending: ["Needs subtitles", ""],
  unavailable: ["Offline", "problem"],
  verifying: ["Checking", ""],
  paused: ["Paused", ""],
  complete: ["Arrived", "done"],
  abandoned: ["Stopped", "problem"],
  failed: ["Failed", "problem"],
  attention: ["Stuck", "problem"],
};
export function Flag({ value }: { value: string }) {
  if (!flags[value]) return null;
  const [label, tone] = flags[value];
  return <span className={`flag ${tone}`}>{label}</span>;
}
export const duration = (seconds: number) =>
  seconds < 60
    ? "Under 1 min"
    : seconds >= 3600
      ? `${Math.floor(seconds / 3600)} hr ${Math.floor((seconds % 3600) / 60)} min`
      : `${Math.floor(seconds / 60)} min`;
export const bytes = (value: number) =>
  value >= 1e9
    ? `${(value / 1e9).toFixed(1)} GB`
    : `${Math.round(value / 1e6)} MB`;
export const itemLink = (item: Item) =>
  item.tmdb_id
    ? `/title/${item.media_type}/${item.tmdb_id}`
    : `/items/${item.id}`;
export const progress = (asset: Asset) =>
  Math.round(
    Math.min(
      100,
      Math.max(
        0,
        (100 * asset.watch!.position) / Math.max(1, asset.watch!.duration),
      ),
    ),
  );
export const inProgress = (asset: Asset) =>
  !!asset.watch && !asset.watch.watched && asset.watch.position > 5;
export const episodeCode = (asset: Pick<Asset, "season" | "episode">) =>
  asset.episode ? `S${asset.season ?? 1} E${asset.episode}` : "";

export function Meter({ value, label }: { value: number; label: string }) {
  return (
    <span
      className="meter"
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={value}
    >
      <span style={{ width: `${value}%` }} />
    </span>
  );
}

export function Cover({
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
    <span className={`cover ${className}`}>
      {src && !failed ? (
        <img src={src} alt="" loading="lazy" onError={() => setFailed(true)} />
      ) : (
        <span className="cover-empty" aria-hidden="true">
          <span>{title}</span>
        </span>
      )}
    </span>
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
        setError(e instanceof Error ? e.message : "Couldn’t load this.");
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

export function Dialog({
  title,
  children,
  footer,
  onClose,
  coupon = false,
}: {
  title: string;
  children: ReactNode;
  footer?: ReactNode;
  onClose: () => void;
  coupon?: boolean;
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
      className={`sheet ${coupon ? "coupon-sheet" : ""}`}
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
      <div className="sheet-head">
        {coupon && (
          <Scissors
            className="sheet-cut"
            size={18}
            strokeWidth={2}
            aria-hidden="true"
          />
        )}
        <h2>{title}</h2>
        <button className="icon-btn" aria-label="Close" onClick={onClose}>
          <X size={20} strokeWidth={2.25} />
        </button>
      </div>
      <div className="sheet-body">{children}</div>
      {footer && <div className="sheet-foot">{footer}</div>}
    </dialog>
  );
}

/** Arrow-key movement between listed things, for keyboards and TV remotes. */
export function useSpatialNavigation() {
  useEffect(() => {
    const keys: Record<string, [number, number]> = {
      ArrowUp: [0, -1],
      ArrowDown: [0, 1],
      ArrowLeft: [-1, 0],
      ArrowRight: [1, 0],
    };
    function move(event: KeyboardEvent) {
      const direction = keys[event.key];
      const current = document.activeElement as HTMLElement | null;
      if (
        !direction ||
        !current?.matches("[data-nav]") ||
        event.altKey ||
        event.metaKey ||
        event.ctrlKey
      )
        return;
      const origin = current.getBoundingClientRect();
      const ox = origin.left + origin.width / 2,
        oy = origin.top + origin.height / 2;
      const scope = current.closest("dialog[open]") || document;
      let best: HTMLElement | null = null,
        bestScore = Infinity;
      scope.querySelectorAll<HTMLElement>("[data-nav]").forEach((candidate) => {
        if (candidate === current || !candidate.getClientRects().length) return;
        const box = candidate.getBoundingClientRect();
        const dx = box.left + box.width / 2 - ox,
          dy = box.top + box.height / 2 - oy;
        const along = dx * direction[0] + dy * direction[1];
        if (along <= 4) return;
        const across = Math.abs(dx * direction[1] + dy * direction[0]);
        const score = along + across * 2.5;
        if (score < bestScore) {
          bestScore = score;
          best = candidate;
        }
      });
      if (best) {
        event.preventDefault();
        (best as HTMLElement).focus();
        (best as HTMLElement).scrollIntoView({
          block: "nearest",
          inline: "nearest",
        });
      }
    }
    window.addEventListener("keydown", move);
    return () => window.removeEventListener("keydown", move);
  }, []);
}
