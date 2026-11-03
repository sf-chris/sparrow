import { api, type Effective } from "./api";
import { bytes, useResource } from "./ui";

export type Disk = {
  value: string;
  kind: "disk";
  name: string;
  online: boolean;
  free_bytes?: number;
  total_bytes?: number;
  download: boolean;
};
export type Computer = {
  value: string;
  kind: "computer";
  name: string;
  online: boolean;
  disks: Disk[];
};
export type Pool = {
  value: string;
  kind: "pool";
  name: string;
  rule: Rule;
  members: string[];
};
export type Rule = "most_free" | "fill" | "round_robin";
export type Destinations = {
  rule: Rule;
  computers: Computer[];
  pools: Pool[];
};

export const rules: [Rule, string][] = [
  ["most_free", "Most free space"],
  ["fill", "Fill in order"],
  ["round_robin", "Take turns"],
];

export function useDestinations() {
  return useResource(() => api<Destinations>("/storage/destinations"));
}

/** Every place a person can choose, by what it is. */
export function places(d?: Destinations | null) {
  const disks = d?.computers.flatMap((c) => c.disks) || [];
  return { disks, count: disks.length };
}

export function placeName(d: Destinations | null | undefined, value: string) {
  if (!value || value === "auto") return "Automatic";
  if (value === "ask") return "Ask each time";
  const pool = d?.pools.find((p) => p.value === value);
  if (pool) return pool.name;
  const computer = d?.computers.find((c) => c.value === value);
  if (computer) return computer.name;
  return (
    d?.computers.flatMap((c) => c.disks).find((x) => x.value === value)
      ?.name || "Removed storage"
  );
}

/** Options for a storage select: Automatic, pools, computers and their disks. */
export function StorageOptions({
  destinations,
  ask = false,
  automatic = true,
}: {
  destinations?: Destinations | null;
  ask?: boolean;
  automatic?: boolean;
}) {
  const offline = (online: boolean) => (online ? "" : " · offline");
  return (
    <>
      {automatic && <option value="auto">Automatic</option>}
      {ask && <option value="ask">Ask each time</option>}
      {!!destinations?.pools.length && (
        <optgroup label="Pools">
          {destinations.pools.map((p) => (
            <option key={p.value} value={p.value}>
              {p.name}
            </option>
          ))}
        </optgroup>
      )}
      <optgroup label="Computers">
        {destinations?.computers.map((c) =>
          c.disks.length > 1 ? (
            [
              <option key={c.value} value={c.value}>
                {c.name}
                {offline(c.online)}
              </option>,
              ...c.disks.map((disk) => (
                <option key={disk.value} value={disk.value}>
                  {" "}
                  {disk.name.split(" · ").pop()}
                  {disk.online && disk.free_bytes !== undefined
                    ? ` · ${bytes(disk.free_bytes)} free`
                    : offline(disk.online)}
                </option>
              )),
            ]
          ) : (
            <option key={c.value} value={c.value}>
              {c.name}
              {c.online && c.disks[0]?.free_bytes !== undefined
                ? ` · ${bytes(c.disks[0].free_bytes)} free`
                : offline(c.online)}
            </option>
          ),
        )}
      </optgroup>
    </>
  );
}

/** A person's usual storage, for the first option of a request's select. */
export function usualPlace(
  d: Destinations | null | undefined,
  prefs: Effective | undefined,
) {
  const value = prefs?.values.storage || "auto";
  return { value, name: placeName(d, value) };
}
