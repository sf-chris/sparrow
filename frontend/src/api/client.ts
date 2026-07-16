import type {
  Config, SearchResult, Download, LibraryItem, MediaRequest,
  TorrentClientInfo, OnboardingStatus, MediaType, Quality,
  ShowDetail, ActivityEvent,
  ResolveCard, Job, JobStatus, JobUrgency, JobDetail, JournalEntry, AgentSession,
  UsageLedger, AgentPromptPreview,
  LibraryViewEntry, Mandate, MonitoringMode,
} from '../types'

const BASE = '/api'

export class ApiError extends Error {
  status: number
  constructor(message: string, status: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

async function req<T>(path: string, opts?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...opts,
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new ApiError(err.detail || res.statusText, res.status)
  }
  return res.json()
}

// Config
export const getConfig = () => req<Config>('/config')
export const updateConfig = (data: Partial<Config>) =>
  req<Config>('/config', { method: 'PATCH', body: JSON.stringify(data) })
export const getOnboardingStatus = () => req<OnboardingStatus>('/config/onboarding-status')

// Torrent clients
export const discoverClients = () => req<TorrentClientInfo[]>('/torrent-clients/discover')
export const testClient = (cfg: Partial<Config['torrent_client']>) =>
  req<TorrentClientInfo>('/torrent-clients/test', { method: 'POST', body: JSON.stringify(cfg) })
export const getClientStatus = () => req<TorrentClientInfo>('/torrent-clients/status')
export const getHealth = () =>
  req<{
    healthy: boolean
    issues: Array<{ id: string; title: string; detail: string; actions: string[] }>
    summary: { requests: number; downloads: number; library: number; torrent_client: unknown }
  }>('/health')
export const runHealthAction = (issueId: string) =>
  req<{
    applied: boolean
    message: string
    health: Awaited<ReturnType<typeof getHealth>>
  }>(`/health/actions/${issueId}`, { method: 'POST' })

// Search
export const searchTorrents = (q: string, type?: MediaType, quality?: Quality, limit = 30) => {
  const params = new URLSearchParams({ q, limit: String(limit) })
  if (type) params.set('type', type)
  if (quality) params.set('quality', quality)
  return req<SearchResult[]>(`/search?${params}`)
}

// Downloads
export const getDownloads = (status?: string) => {
  const params = status ? `?status=${status}` : ''
  return req<Download[]>(`/downloads${params}`)
}
export const addDownload = (data: { magnet_url: string; name: string; media_type: string; tmdb_id?: number }) =>
  req<Download>('/downloads', { method: 'POST', body: JSON.stringify(data) })
export const deleteDownload = (id: string, deleteFiles = false) =>
  req<{ success: boolean }>(`/downloads/${id}?delete_files=${deleteFiles}`, { method: 'DELETE' })
export const organizeDownload = (id: string, data?: { media_type?: string; tmdb_id?: number }) =>
  req<{ success: boolean }>(`/downloads/${id}/organize`, { method: 'POST', body: JSON.stringify({ download_id: id, ...data }) })
export const enrichDownload = (id: string) =>
  req<{ success: boolean; poster: string; title: string }>(`/downloads/${id}/enrich`, { method: 'POST' })

// Requests
export const getRequests = () => req<MediaRequest[]>('/requests')
export const createRequest = (data: { query: string; quality?: string }) =>
  req<MediaRequest>('/requests', { method: 'POST', body: JSON.stringify(data) })
export const createSelectedRequest = (data: {
  query: string
  quality?: string
  resolved: RequestPreview['resolved']
  selected: SearchResult
  selected_items?: SearchResult[]
  strategy: string
  score?: Record<string, unknown>
  summary?: string
}) =>
  req<MediaRequest>('/requests/selected', { method: 'POST', body: JSON.stringify(data) })
export interface RequestPreview {
  query: string
  resolved: {
    title: string
    media_type: string
    season: number | null
    episode_count: number | null
    quality: string
    tmdb_id: number | null
    year?: number | null
  }
  strategy: string
  decision: unknown
  pack_candidates: PreviewCandidate[]
  series_candidates?: PreviewCandidate[]
  episode_candidates: PreviewCandidate[]
  movie_candidates: PreviewCandidate[]
  summary: string
}
export type PreviewCandidate = { result: SearchResult; results?: SearchResult[]; score: Record<string, unknown>; option?: Record<string, unknown> }
export const previewRequest = (data: { query: string; quality?: string }) =>
  req<RequestPreview>('/requests/preview', { method: 'POST', body: JSON.stringify(data) })
export const retryRequest = (id: string) =>
  req<MediaRequest>(`/requests/${id}/retry`, { method: 'POST' })
export const deleteRequest = (id: string) =>
  req<{ success: boolean }>(`/requests/${id}`, { method: 'DELETE' })

// v3: Resolution / Jobs / Journal / Agent sessions
export const TMDB_POSTER_BASE = 'https://image.tmdb.org/t/p/w342'

export const resolveSearch = (q: string) =>
  req<ResolveCard[]>(`/resolve?q=${encodeURIComponent(q)}`)

export interface CreateJobPayload {
  tmdb_id: number
  media_type: 'tv' | 'movie'
  /** Omit for the whole show */
  wanted_episodes?: Record<string, number[]>
  preferred_quality?: string
  min_quality?: string
  audio_pref?: string
  urgency?: JobUrgency
  /** Standing authority granted with this request; omit to leave unchanged */
  monitoring?: MonitoringMode | ''
}
export const createJob = (data: CreateJobPayload) =>
  req<Job>('/jobs', { method: 'POST', body: JSON.stringify(data) })

export const getJobs = (status?: JobStatus) =>
  req<Job[]>(`/jobs${status ? `?status=${status}` : ''}`)
export const getJob = (id: string) => req<JobDetail>(`/jobs/${id}`)
export const nudgeJob = (id: string) =>
  req<{ ok: boolean }>(`/jobs/${id}/nudge`, { method: 'POST' })
export const pauseJob = (id: string) => req<Job>(`/jobs/${id}/pause`, { method: 'POST' })
export const resumeJob = (id: string) => req<Job>(`/jobs/${id}/resume`, { method: 'POST' })
export const cancelJob = (id: string) => req<Job>(`/jobs/${id}`, { method: 'DELETE' })

export const getJournal = (jobId = '', limit = 200) =>
  req<JournalEntry[]>(`/journal?job_id=${encodeURIComponent(jobId)}&limit=${limit}`)

export const getAgentSessions = (openOnly = true) =>
  req<AgentSession[]>(`/agent-sessions?open_only=${openOnly}`)
export const getUsageLedger = () => req<UsageLedger>('/usage')
export const getAgentPrompts = () => req<AgentPromptPreview[]>('/agent-prompts')

// v2: Shows / Goals / Activity
export const getShow = (tmdbId: number, mediaType: 'tv' | 'movie' = 'tv') =>
  req<ShowDetail>(mediaType === 'movie' ? `/movies/${tmdbId}` : `/shows/${tmdbId}`)
export const getMovieStatus = (tmdbId: number) =>
  req<{ tmdb_id: number; in_library: boolean; library_item_id: string | null; goals: MediaRequest[] }>(
    `/movies/${tmdbId}/status`)
export const createGoal = (data: {
  tmdb_id?: number
  media_type: MediaType
  title?: string
  query?: string
  seasons?: number[]
  episodes?: Record<string, number[]>
  quality?: string
  min_quality?: string
}) => req<MediaRequest>('/goals', { method: 'POST', body: JSON.stringify(data) })
export const pauseGoal = (id: string) => req<MediaRequest>(`/goals/${id}/pause`, { method: 'POST' })
export const resumeGoal = (id: string) => req<MediaRequest>(`/goals/${id}/resume`, { method: 'POST' })
export const getActivity = (limit = 100, requestId = '') =>
  req<ActivityEvent[]>(`/activity?limit=${limit}${requestId ? `&request_id=${requestId}` : ''}`)

// Mandates (user authority; the tool layer enforces these against agents)
export const getMandate = (tmdbId: number, mediaType: 'tv' | 'movie' = 'tv') =>
  req<{ tmdb_id: number; media_type: string; mandate: Mandate | null; summary: string }>(
    `/mandates/${tmdbId}?media_type=${mediaType}`)
export const setMonitoring = (tmdbId: number, mode: MonitoringMode, seasons: number[] = [],
                              mediaType: 'tv' | 'movie' = 'tv') =>
  req<{ mandate: Mandate; summary: string }>(`/mandates/${tmdbId}/monitoring`, {
    method: 'PUT',
    body: JSON.stringify({ mode, seasons, media_type: mediaType }),
  })

// Library
export const getLibrary = (type?: MediaType) => {
  const params = type ? `?type=${type}` : ''
  return req<LibraryItem[]>(`/library${params}`)
}
export const getLibraryView = () => req<LibraryViewEntry[]>('/library/view')
export const deleteLibraryItem = (id: string, deleteFiles = false) =>
  req<{ success: boolean }>(`/library/${id}?delete_files=${deleteFiles}`, { method: 'DELETE' })
export const scanLibrary = () => req<{ scanned: number; added: number }>('/library/scan', { method: 'POST' })

// Metadata
export const searchMetadata = (q: string, type: string = 'movie') =>
  req<unknown[]>(`/metadata/search?q=${encodeURIComponent(q)}&type=${type}`)

// CWM
export const getCWMLogs = (limit = 50) => req<unknown[]>(`/cwm/logs?limit=${limit}`)
export const getCWMModel = () => req<{ content: string; path: string }>('/cwm/model')
export const runCWMAnalysis = (prompt: string) =>
  req<{ output: string; model_evolved: boolean; error: string | null }>('/cwm/analyze', {
    method: 'POST', body: JSON.stringify({ prompt })
  })
export const runHealthCheck = () =>
  req<{ output: string; model_evolved: boolean; error: string | null }>('/cwm/health-check', { method: 'POST' })

// Search suggestions
export interface SuggestionItem {
  tmdb_id: number
  title: string
  media_type: 'movie' | 'tv'
  year: string
  poster_url: string | null
  rating: number
  overview: string
}
export interface TVSeason {
  season_number: number
  episode_count: number
  name: string
}
export interface SuggestResponse {
  library: Array<{ id: string; title: string; media_type: string }>
  downloads: Array<{ id: string; name: string; status: string }>
  tmdb: SuggestionItem[]
}
export const searchSuggest = (q: string) =>
  req<SuggestResponse>(`/search/suggest?q=${encodeURIComponent(q)}`)
export const searchSuggestExpand = (q: string) =>
  req<{ suggestions: SuggestionItem[] }>(`/search/suggest/expand?q=${encodeURIComponent(q)}`)
export const searchSuggestTV = (tmdbId: number) =>
  req<{ seasons: TVSeason[] }>(`/search/suggest/tv/${tmdbId}`)

// Logs
export const getLogs = (limit = 200, level = '') =>
  req<unknown[]>(`/logs?limit=${limit}${level ? `&level=${level}` : ''}`)

// CWM versioning
export const getCWMVersion = () =>
  req<{ version: number; updated_at: number | null; strategy_added: string; strategy_count: number; pattern_count: number }>('/cwm/version')
export const getCWMVersions = () =>
  req<Array<{ version: number; filename: string; timestamp: number }>>('/cwm/versions')
export const getCWMVersionContent = (filename: string) =>
  req<{ filename: string; content: string }>(`/cwm/versions/${filename}`)
export const getCWMDiff = (fromFile: string, toFile: string) =>
  req<{ diff: string }>(`/cwm/diff?from=${encodeURIComponent(fromFile)}&to=${encodeURIComponent(toFile)}`)
