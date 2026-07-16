export type MediaType = 'movie' | 'tv' | 'unknown'
export type Quality = '2160p' | '1080p' | '720p' | '480p' | 'any'
export type DownloadStatus =
  | 'queued'
  | 'downloading'
  | 'seeding'
  | 'completed'
  | 'organizing'
  | 'organized'
  | 'error'
  | 'paused'
export type TorrentClientType = 'qbittorrent' | 'transmission' | 'none'

export interface TorrentClientConfig {
  type: TorrentClientType
  host: string
  port: number
  username: string
  password: string
  password_configured?: boolean
  url?: string
  url_configured?: boolean
}

export interface Config {
  staging_dir: string
  library_dir: string
  torrent_client: TorrentClientConfig
  quality_preference: Quality
  tmdb_api_key: string
  anthropic_api_key: string
  tmdb_api_key_configured?: boolean
  anthropic_api_key_configured?: boolean
  onboarding_complete: boolean
  auto_organize: boolean
  preferred_search_engines: string[]
  seeding_ratio_limit: number
  seeding_time_hours: number
  prefer_smaller_files: boolean
  prefer_season_packs: boolean
  season_pack_size_limit_gb: number
  smart_model: string
  cheap_model: string
}

export interface SearchResult {
  id: string
  name: string
  info_hash: string
  seeders: number
  leechers: number
  size_bytes: number
  size_human: string
  added_ts: number
  category: string
  source: string
  imdb_id: string | null
  uploader: string
  magnet_url: string
}

export interface Download {
  id: string
  name: string
  magnet_url: string
  media_type: MediaType
  status: DownloadStatus
  progress: number
  size_bytes: number
  downloaded_bytes: number
  download_speed: number
  eta_seconds: number
  torrent_hash: string
  staging_path: string
  library_path: string
  tmdb_id: number | null
  metadata: Record<string, unknown>
  added_at: number
  completed_at: number | null
  error_message: string
  quality: string
}

export type RequestStatus =
  | 'pending'
  | 'resolving'
  | 'evaluating'
  | 'blocked'
  | 'downloading'
  | 'organizing'
  | 'complete'
  | 'partial'
  | 'failed'
  | 'searching'
  | 'waiting'

export type RequestStrategy = 'unknown' | 'movie' | 'season_pack' | 'episodes' | 'series'

export interface MediaRequest {
  id: string
  query: string
  status: RequestStatus
  strategy: RequestStrategy
  title: string
  media_type: MediaType
  season: number | null
  episode_count: number | null
  quality: string
  tmdb_id: number | null
  progress_found: number
  progress_total: number
  decision_summary: string
  error_message: string
  download_ids: string[]
  missing_episodes: number[]
  evaluation: Record<string, unknown>
  created_at: number
  updated_at: number
  wanted_episodes: Record<string, number[]>
  min_quality: string
  upgrade: boolean
  attempts: Array<{ ts: number; info_hash: string; name: string; outcome: string; note: string }>
  next_check_at: number
  check_interval: number
  paused: boolean
}

export interface LibraryItem {
  id: string
  title: string
  media_type: MediaType
  path: string
  year: number | null
  tmdb_id: number | null
  imdb_id: string | null
  overview: string
  poster_path: string
  backdrop_path: string
  genres: string[]
  rating: number | null
  seasons: number | null
  episode_count: number | null
  size_bytes: number
  added_at: number
  metadata: Record<string, unknown>
  episodes: Record<string, Record<string, EpisodeFile>>
}

export interface EpisodeFile {
  quality: string
  path: string
  size_bytes?: number
  added_at?: number
  verified?: boolean
  group?: string
}

export interface ShowEpisode {
  episode: number
  have: boolean
  quality: string
  in_flight: boolean
}

export interface ShowSeason {
  season_number: number
  name: string
  episode_count: number
  have_count: number
  in_flight_count: number
  episodes: ShowEpisode[]
}

export interface ShowDetail {
  tmdb_id: number
  media_type: 'tv' | 'movie'
  title: string
  year: number | null
  overview: string
  rating: number | null
  genres: string[]
  poster_url: string | null
  backdrop_url: string | null
  status: string
  in_library: boolean
  library_item_id: string | null
  seasons: ShowSeason[]
  goals: MediaRequest[]
}

export interface ActivityEvent {
  id: string
  timestamp: number
  kind: string
  message: string
  detail: string
  request_id: string
  tmdb_id: number | null
  level: 'info' | 'success' | 'warning' | 'error'
}

// v3: agentic jobs, journal, sessions
export type JobStatus = 'active' | 'complete' | 'abandoned' | 'paused'
export type JobUrgency = 'tonight' | 'soon' | 'whenever'
export type AgentKind = 'fetch' | 'media' | 'librarian'
export type SessionStatus = 'running' | 'hibernating' | 'closed'

export interface ResolveCard {
  tmdb_id: number
  title: string
  media_type: 'tv' | 'movie'
  year: string
  poster_url: string | null
  rating: number
  overview: string
  active_job: { job_id: string; state_line: string } | null
  in_library: boolean
}

export interface Job {
  id: string
  tmdb_id: number
  media_type: 'tv' | 'movie'
  title: string
  year: string
  /** TMDB path like "/abc.jpg" — prefix with https://image.tmdb.org/t/p/w342 */
  poster_path: string
  wanted_episodes: Record<string, number[]>
  preferred_quality: string
  min_quality: string
  audio_pref: string
  urgency: JobUrgency
  status: JobStatus
  origin: string
  /** Plain-language current state */
  state_line: string
  /** Unix seconds; 0 = none scheduled */
  next_wake_at: number
  session_id: string
  created_at: number
  updated_at: number
  closed_at: number
}

export interface JournalEntry {
  id: string
  job_id: string
  session_id: string
  agent: AgentKind
  ts: number
  text: string
}

export interface SessionSpend {
  turns: number
  searches: number
  peeks: number
  input_tokens: number
  output_tokens: number
  cache_creation_input_tokens: number
  cache_read_input_tokens: number
  dollars: number
  entries: SpendEntry[]
  pricing_source: string
}

export interface SpendEntry {
  ts: number
  model: string
  input_tokens: number
  output_tokens: number
  cache_creation_input_tokens: number
  cache_read_input_tokens: number
  rates: {
    input: number
    output: number
    cache_write: number
    cache_read: number
  }
  cost: number
  legacy_aggregate?: boolean
}

export interface JobSessionInfo {
  status: SessionStatus
  wake_at: number
  wake_reason: string
  spend: SessionSpend
}

export interface ExecutionTraceEntry {
  id: string
  tool: string
  message: string
  input: Record<string, unknown>
  result: string
  is_error: boolean
}

export interface JobDetail {
  job: Job
  journal: JournalEntry[]
  session: JobSessionInfo | null
  downloads: Download[]
  activity: ActivityEvent[]
  trace: ExecutionTraceEntry[]
}

export interface AgentSession {
  id: string
  agent: AgentKind
  job_id: string
  job_title: string
  download_id: string
  model: string
  status: SessionStatus
  wake_at: number
  wake_reason: string
  spend: SessionSpend
  created_at: number
  updated_at: number
  closed_at: number | null
}

export interface UsageLedger {
  currency: 'USD'
  pricing_source: string
  totals: {
    cost: number
    input_tokens: number
    output_tokens: number
    cache_creation_input_tokens: number
    cache_read_input_tokens: number
    api_calls: number
    legacy_sessions: number
  }
  models: Array<{
    model: string
    cost: number
    input_tokens: number
    output_tokens: number
    cache_creation_input_tokens: number
    cache_read_input_tokens: number
    api_calls: number
    sessions: number
    legacy_sessions: number
    current_rates: { input: number; output: number; cache_write: number; cache_read: number }
  }>
  sessions: AgentSession[]
}

export interface AgentPromptPreview {
  agent: AgentKind
  label: string
  model: string
  context: string
  prompt: string
  tools: Array<{
    name: string
    description: string
    input_schema: Record<string, unknown>
  }>
}

export interface TorrentClientInfo {
  type: TorrentClientType
  host: string
  port: number
  reachable: boolean
  version: string
  active_downloads: number
}

export interface OnboardingStatus {
  complete: boolean
  has_staging: boolean
  has_library: boolean
  has_torrent_client: boolean
  has_tmdb_key: boolean
  has_anthropic_key: boolean
}
