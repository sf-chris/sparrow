import { useEffect, useState } from "react";

export function Mark({ className = "" }: { className?: string }) {
  return (
    <svg
      className={className}
      width="32"
      height="32"
      viewBox="0 0 40 40"
      fill="none"
      aria-hidden="true"
    >
      <path
        d="M7 18c0-7 5-12 12-12s12 5 12 12v4c0 8-7 13-15 11L5 30l5-5a14 14 0 0 1-3-7Z"
        fill="currentColor"
      />
      <path d="M12 20c1 7 9 9 14 3-5 1-7-2-7-6" fill="#f8f7fc" />
      <circle cx="25" cy="14" r="1.6" fill="#f8f7fc" />
      <path d="m31 16 6 3-6 3" fill="currentColor" />
    </svg>
  );
}

/** Original, scalable illustration: the little things around a good watch. */
export function PlayroomArt({ className = "" }: { className?: string }) {
  return (
    <svg
      className={`sp-playroom-art ${className}`}
      viewBox="0 0 520 410"
      fill="none"
      aria-hidden="true"
    >
      <ellipse cx="276" cy="350" rx="192" ry="20" fill="#e9e6ed" />
      <path
        d="M51 117c-21-2-27-19-15-28 7-5 16-2 20 4 1-21 29-25 38-10 18-7 35 9 26 23-8 12-44 14-69 11Z"
        fill="white"
        stroke="#302d3c"
        strokeWidth="2"
      />
      <path
        d="m441 77 5 13 15 2-11 10 3 15-13-8-13 7 3-14-10-11 15-1 6-13Z"
        fill="#d7efad"
        stroke="#302d3c"
        strokeWidth="2"
        strokeLinejoin="round"
      />
      <g transform="rotate(12 388 230)">
        <rect
          x="319"
          y="132"
          width="133"
          height="203"
          rx="12"
          fill="#f3cbdb"
          stroke="#302d3c"
          strokeWidth="2"
        />
        <circle cx="386" cy="212" r="45" fill="#e186aa" />
        <path
          d="M346 216c22-47 56 52 80-6M347 234c22-47 56 52 80-6M350 199c22-47 56 52 76-6"
          stroke="#fff5fa"
          strokeWidth="3"
        />
        <path
          d="M340 283h59m-59 10h85m-85 10h37"
          stroke="#302d3c"
          strokeWidth="3"
          strokeLinecap="round"
        />
      </g>
      <g transform="rotate(-5 234 328)">
        <rect
          x="106"
          y="313"
          width="263"
          height="31"
          rx="6"
          fill="#d8edc5"
          stroke="#302d3c"
          strokeWidth="2"
        />
        <path d="M123 322h226m-226 8h226" stroke="#9abe8b" />
        <rect
          x="113"
          y="297"
          width="251"
          height="23"
          rx="5"
          fill="#fffef9"
          stroke="#302d3c"
          strokeWidth="2"
        />
        <path
          d="M130 306h80m12 0h12m12 0h96"
          stroke="#302d3c"
          strokeWidth="2"
          strokeLinecap="round"
        />
      </g>
      <g transform="rotate(-5 231 223)">
        <rect
          x="92"
          y="138"
          width="274"
          height="165"
          rx="23"
          fill="#bdb0e9"
          stroke="#302d3c"
          strokeWidth="2.4"
        />
        <rect
          x="107"
          y="152"
          width="214"
          height="134"
          rx="16"
          fill="#f8f5ff"
          stroke="#302d3c"
          strokeWidth="2"
        />
        <path
          d="M119 263c36-19 78-8 99-14s54-23 91-13"
          stroke="#e3daf6"
          strokeWidth="2"
        />
        <circle cx="214" cy="218" r="31" fill="#7153ba" />
        <path
          d="m208 205 19 13-19 13v-26Z"
          fill="white"
          stroke="white"
          strokeLinejoin="round"
        />
        <circle
          cx="343"
          cy="180"
          r="7"
          fill="#fffdf7"
          stroke="#302d3c"
          strokeWidth="2"
        />
        <path
          d="M337 204h12m-12 8h12m-12 8h12m-12 8h12"
          stroke="#302d3c"
          strokeWidth="2"
          strokeLinecap="round"
        />
        <path
          d="m185 136-19-28m25 27 15-24"
          stroke="#302d3c"
          strokeWidth="2.4"
          strokeLinecap="round"
        />
      </g>
      <g transform="rotate(-8 135 105)">
        <path
          d="M105 74c-3-25 34-39 49-16 7 10 6 21 4 29l-5 36c-18 13-47 0-48-18V74Z"
          fill="#fffdf7"
          stroke="#302d3c"
          strokeWidth="2"
        />
        <path
          d="M105 95c-15 10-22 7-31 3 2 20 21 25 34 11"
          fill="#bdb0e9"
          stroke="#302d3c"
          strokeWidth="2"
          strokeLinejoin="round"
        />
        <path
          d="M122 82c-1 18 12 25 23 13"
          stroke="#302d3c"
          strokeWidth="2"
          strokeLinecap="round"
        />
        <circle cx="144" cy="65" r="2.5" fill="#302d3c" />
        <path
          d="m159 67 13 6-13 5"
          fill="#d7efad"
          stroke="#302d3c"
          strokeWidth="2"
          strokeLinejoin="round"
        />
        <path
          d="m124 128-2 11m20-11 1 11m-26 1h9m12 0h9"
          stroke="#302d3c"
          strokeWidth="2"
          strokeLinecap="round"
        />
      </g>
      <g transform="rotate(9 428 322)">
        <path
          d="M398 305h52l-6 40c-10 7-26 7-39 0l-7-40Z"
          fill="#fffdf7"
          stroke="#302d3c"
          strokeWidth="2"
        />
        <path d="M451 311c25-5 22 26-4 20" stroke="#302d3c" strokeWidth="2" />
        <path
          d="M410 315c4 5 9 5 13 0m8 0c3 5 8 5 12 0"
          stroke="#bdb0e9"
          strokeWidth="3"
        />
        <path
          d="M416 294c-9-11 9-15 1-25m15 24c-8-11 8-14 1-23"
          stroke="#302d3c"
          strokeWidth="1.6"
          strokeLinecap="round"
        />
      </g>
      <path
        d="m50 245 5 9 10 3-9 5-3 10-5-9-10-3 9-5 3-10Zm332-201 3 8 8 3-8 3-3 8-3-8-8-3 8-3 3-8Z"
        fill="#bdb0e9"
      />
      <path
        d="M68 288c-21 13-18 32 2 33m-8-6 9 6-8 6M292 77c15-8 29-6 38 8m-8-3 10 5 2-11"
        stroke="#302d3c"
        strokeWidth="1.7"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export function Doodle({
  kind = "spark",
  className = "",
}: {
  kind?: "spark" | "heart" | "orbit";
  className?: string;
}) {
  return (
    <svg
      className={`sp-doodle ${className}`}
      width="64"
      height="64"
      viewBox="0 0 64 64"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {kind === "heart" ? (
        <>
          <path d="M32 49 13 31C-3 13 22 3 32 20c10-17 35-7 19 11L32 49Z" />
          <path d="m9 47-4 4m46-40 4-4M27 56l1 4" />
        </>
      ) : kind === "orbit" ? (
        <>
          <circle cx="32" cy="32" r="18" />
          <ellipse
            cx="32"
            cy="32"
            rx="30"
            ry="9"
            transform="rotate(-32 32 32)"
          />
          <path d="m22 21 4-2m16 26-4 2M51 10v6m-3-3h6" />
        </>
      ) : (
        <>
          <path d="m33 5 4 20 21 7-21 6-5 21-6-21-20-6 20-7 7-20Z" />
          <path d="m50 6 1 9 9 2-9 2-2 8-1-8-8-2 8-2 2-9Z" />
        </>
      )}
    </svg>
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
    <div className={`${className} sp-backdrop-fallback`} aria-hidden="true">
      <Doodle kind="orbit" />
    </div>
  );
}
