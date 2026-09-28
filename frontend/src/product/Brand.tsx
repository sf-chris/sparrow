import { useEffect, useState } from "react";

const BIRD =
  "M25.6 9.6 30.5 11.4 25.8 12.9C25.2 20.6 19 27.2 9.5 28.4 11.8 26.5 13.3 24.3 13.9 21.6 9.8 22.6 6 22 2.5 20.2 7.6 18.6 11 15.2 13.2 10.2 14.5 6.9 17.2 5 20.4 5 23.1 5 25 6.9 25.6 9.6Z";

/** The sparrow: one silhouette, one eye. Colour comes from `currentColor`. */
export function Mark({ className = "" }: { className?: string }) {
  return (
    <svg
      className={`sp-mark ${className}`}
      width="24"
      height="24"
      viewBox="0 0 32 32"
      aria-hidden="true"
    >
      <path d={BIRD} fill="currentColor" />
      <circle cx="21.3" cy="9.4" r="1.25" className="sp-mark-eye" />
    </svg>
  );
}

/** Deterministic hue for artwork that has not arrived yet. */
export function hueOf(text: string) {
  let hash = 0;
  for (const character of text)
    hash = (hash * 31 + character.charCodeAt(0)) | 0;
  return Math.abs(hash) % 360;
}

export function Backdrop({
  src,
  title = "",
  className = "",
  lazy = false,
}: {
  src?: string;
  title?: string;
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
    <div
      className={`${className} sp-art-fallback`}
      style={{ "--hue": hueOf(title) } as React.CSSProperties}
      aria-hidden="true"
    />
  );
}
