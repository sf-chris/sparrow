export class RequestError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}

export async function api<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const response = await fetch(`/api/v1${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      "X-Sparrow-Request": "1",
      ...options.headers,
    },
    credentials: "same-origin",
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const detail = Array.isArray(body.detail)
      ? body.detail.map((d: { msg: string }) => d.msg).join(" ")
      : body.detail;
    if (response.status === 401 && !path.startsWith("/auth/"))
      window.dispatchEvent(new Event("sparrow:signed-out"));
    throw new RequestError(
      detail || "That didn’t work. Try again.",
      response.status,
    );
  }
  return response.json();
}
export const post = <T>(path: string, body: unknown = {}) =>
  api<T>(path, { method: "POST", body: JSON.stringify(body) });
export const patch = <T>(path: string, body: unknown) =>
  api<T>(path, { method: "PATCH", body: JSON.stringify(body) });
export type User = {
  id: string;
  name: string;
  username: string;
  role: "admin" | "requester" | "viewer";
  welcomed: boolean;
  disabled: boolean;
  preferences: Partial<Preferences>;
  library_scope: string[] | null;
};
export type Preferences = {
  preferred_quality: string;
  min_quality: string;
  audio_pref: string;
  subtitle_languages: string[];
  subtitle_mode: string;
  subtitle_kind: string;
  require_subtitles: boolean;
  max_file_size_gb: number;
  monitoring: string;
  urgency: string;
  prefer_smaller_files: boolean;
};
export type Effective = {
  principal: string;
  version: string;
  values: Preferences;
  sources: Record<string, string>;
  policy: Policy;
};
export type Policy = {
  max_quality: string;
  max_file_size_gb: number;
  max_agent_calls: number;
  max_agent_dollars: number;
};
export type Auth = {
  needs_setup?: boolean;
  server_setup?: { complete: boolean; deferred: boolean } | null;
  user: User | null;
  preferences: Effective | null;
};
export type Track = {
  index: number;
  codec: string;
  language: string;
  title: string;
  default: boolean;
  forced: boolean;
  hearing_impaired: boolean;
};
export type Asset = {
  id: string;
  item_id: string;
  node_id: string;
  root_id: string;
  path: string;
  season?: number;
  episode?: number;
  state: string;
  facts: {
    duration: number;
    quality: string;
    size_bytes: number;
    audio_tracks: Track[];
    subtitle_tracks: Track[];
    video_codec: string;
    version: { size_bytes: number; mtime_ns: number };
  };
  watch?: {
    position: number;
    duration: number;
    watched: boolean;
    updated: number;
  };
};
export type Item = {
  id: string;
  title: string;
  media_type: "movie" | "tv";
  tmdb_id?: number;
  year?: number;
  overview: string;
  poster_url?: string;
  backdrop_url?: string;
  library_id: string;
  state: string;
  ready_count: number;
  assets: Asset[];
  size_bytes: number;
};
export type Job = {
  id: string;
  tmdb_id: number;
  media_type: "movie" | "tv";
  title: string;
  status: string;
  state_line: string;
  wanted_episodes: Record<string, number[]>;
  user_id: string;
  node_id: string;
  preferred_quality: string;
  audio_pref: string;
  urgency: string;
  updated_at: number;
  preferences: Effective;
  revision: number;
  needs_attention?: boolean;
};
export type Title = {
  tmdb_id: number;
  media_type: "movie" | "tv";
  title: string;
  year: string;
  overview: string;
  poster_url?: string;
  backdrop_url?: string;
  seasons: { season_number: number; episode_count: number; name?: string }[];
  items: Item[];
  jobs: Job[];
  preferences?: Effective;
  runtime?: number;
};
export type NodeInfo = {
  id: string;
  name: string;
  online: boolean;
  disabled: boolean;
  last_seen: number;
  capabilities: {
    platform: string;
    probe: boolean;
    transcode: boolean;
    download: boolean;
    roots: {
      id: string;
      available: boolean;
      free_bytes?: number;
      total_bytes?: number;
      error?: string;
    }[];
  };
};
