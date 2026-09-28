import { useEffect, useState } from "react";

/** The sparrow: a half-disc body, a round head, a tail and a beak. */
export function Mark({ className = "" }: { className?: string }) {
  return (
    <svg
      className={`sp-mark ${className}`}
      viewBox="0 0 32 32"
      fill="currentColor"
      aria-hidden="true"
    >
      <path d="M3 15h24a12 12 0 0 1-24 0Z" />
      <circle cx="20" cy="9.6" r="5.6" />
      <path d="M3 15-.5 8 10 15Z" />
      <path d="m25 8 5.5 1.8-5.5 1.8Z" />
      <circle cx="21.3" cy="8.7" r="1.25" fill="var(--mark-eye, var(--paper))" />
    </svg>
  );
}

export function Wordmark() {
  return (
    <span className="sp-wordmark">
      <Mark />
      <span>Sparrow</span>
    </span>
  );
}

export function Backdrop({
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
  return src && !failed ? (
    <img
      className={className}
      src={src}
      alt=""
      loading={lazy ? "lazy" : "eager"}
      onError={() => setFailed(true)}
    />
  ) : (
    <div className={`${className} sp-backdrop-fallback`} aria-hidden="true" />
  );
}

/** A sparrow projecting onto a screen. Pure geometry that follows the theme tokens. */
export function Projection({ className = "" }: { className?: string }) {
  return (
    <svg
      className={`sp-projection ${className}`}
      viewBox="0 0 560 460"
      fill="none"
      aria-hidden="true"
    >
      <path d="M72 382 166 34h360L118 392Z" fill="var(--signal)" opacity=".16" />
      <path d="M72 382 166 34M72 382 526 264" stroke="var(--signal)" strokeWidth="1.5" strokeDasharray="3 6" />
      <rect x="150" y="18" width="392" height="262" fill="var(--sheet)" stroke="var(--ink)" strokeWidth="2" />
      <rect x="166" y="34" width="360" height="230" fill="var(--inverse)" />
      <circle cx="430" cy="300" r="118" fill="var(--signal)" clipPath="url(#sp-screen)" />
      <defs>
        <clipPath id="sp-screen">
          <rect x="166" y="34" width="360" height="230" />
        </clipPath>
      </defs>
      <g fill="var(--inverse-ink)" fontFamily="Big Shoulders Display, sans-serif" fontWeight="900">
        <text x="190" y="104" fontSize="54" letterSpacing="1">NOW</text>
        <text x="190" y="156" fontSize="54" letterSpacing="1">SHOWING</text>
      </g>
      <g transform="translate(20 356) scale(3.3)" fill="var(--ink)">
        <path d="M3 15h24a12 12 0 0 1-24 0Z" />
        <circle cx="20" cy="9.6" r="5.6" />
        <path d="M3 15-.5 8 10 15Z" />
        <path d="m25 8 5.5 1.8-5.5 1.8Z" />
        <circle cx="21.3" cy="8.7" r="1.25" fill="var(--paper)" />
      </g>
      <path d="M0 457h560" stroke="var(--ink)" strokeWidth="2" />
      <g fill="var(--ink)">
        {Array.from({ length: 9 }, (_, i) => (
          <rect key={i} x={188 + i * 38} y="312" width="26" height="14" rx="1" opacity={i % 3 === 1 ? 1 : 0.2} />
        ))}
        {Array.from({ length: 8 }, (_, i) => (
          <rect key={i} x={207 + i * 38} y="342" width="26" height="14" rx="1" opacity={i === 4 ? 1 : 0.2} />
        ))}
      </g>
    </svg>
  );
}
