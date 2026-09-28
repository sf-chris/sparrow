import { useEffect, useState, type ReactNode } from "react";

/** The margin doodle: a sparrow drawn in one or two biro strokes. */
export function Bird({ className = "" }: { className?: string }) {
  return (
    <svg
      className={`bird ${className}`}
      viewBox="0 0 48 40"
      fill="none"
      aria-hidden="true"
    >
      <path
        className="bird-line"
        pathLength={1}
        d="M38.4 11.3c-.7-3.4-3.6-5.1-6.9-4.7-2.9.4-4.8 2.6-5.7 5.3-2 3.2-7.6 4.3-12.9 5.6L2.6 16l2.3 3.1-1.3 1.6 7.4 1.5c3 5.5 11.1 8.3 17.8 5.3 5.9-2.7 10-7.6 9.6-13.9ZM38.4 11.3l5.1 1.8-4.9 1.8M15.6 19.8c4.9-1.9 10.4-1.2 13.6 2.1M22.2 29.6l-1.1 5.9m0 0-2.6.7m2.6-.7 2.1 1M26.9 28.5l.6 6.8m0 0-2.2 1.1m2.2-1.1 2.6.3"
      />
      <circle className="bird-eye" cx="33.4" cy="10.4" r="1.3" />
    </svg>
  );
}

export function Logotype({ className = "" }: { className?: string }) {
  return (
    <span className={`logotype ${className}`}>
      <span>sparrow</span>
      <Bird />
    </span>
  );
}

/** A household's biro circle around something they want. */
export function Circled({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <span className={`circled ${className}`}>
      {children}
      <svg viewBox="0 0 200 60" preserveAspectRatio="none" aria-hidden="true">
        <path
          pathLength={1}
          d="M24 13C66 2 152 1 185 15c19 8 11 32-24 39-49 9-121 7-147-6C-4 39 4 19 42 9"
        />
      </svg>
    </span>
  );
}

export function Tick({ className = "" }: { className?: string }) {
  return (
    <svg
      className={`tick ${className}`}
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden="true"
    >
      <path
        pathLength={1}
        d="M3.5 13.2c2.3 1.4 4 3.3 5.4 5.8C11.8 11.6 15.9 6.4 21 3.6"
      />
    </svg>
  );
}

/** A landscape picture from the catalogue, or a quiet printed placeholder. */
export function Still({
  src,
  className = "",
  lazy = false,
}: {
  src?: string;
  className?: string;
  lazy?: boolean;
}) {
  const [failed, setFailed] = useState(false);
  useEffect(() => setFailed(false), [src]);
  return (
    <span className={`still ${className}`}>
      {src && !failed ? (
        <img
          src={src}
          alt=""
          loading={lazy ? "lazy" : "eager"}
          onError={() => setFailed(true)}
        />
      ) : (
        <span className="still-empty" aria-hidden="true">
          <Bird />
        </span>
      )}
    </span>
  );
}
