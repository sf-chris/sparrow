import { useState, useEffect, useCallback } from 'react'
import {
  ArrowRight, CheckCircle2, Clapperboard, Clock, Download, Film, FolderInput, Loader2,
  BarChart3, HardDrive, PackageCheck, RefreshCw, RotateCw, Search, ShieldAlert, Sparkles, Trash2, Tv, Wand2, XCircle,
} from 'lucide-react'
import {
  createSelectedRequest, deleteDownload, deleteRequest, getDownloads, getRequests,
  getHealth, organizeDownload, previewRequest, retryRequest, runHealthAction, searchSuggest,
} from '../api/client'
import type { PreviewCandidate, RequestPreview, SuggestResponse } from '../api/client'
import type { Download as DL, MediaRequest, RequestStatus, SearchResult } from '../types'
import { useWebSocket } from '../hooks/useWebSocket'
import { Badge, Button, Card, Progress, RelativeTime, SectionHeader } from '../components/ui'
import clsx from 'clsx'

function formatSize(bytes: number): string {
  if (!bytes) return 'Unknown size'
  const gb = bytes / 1024 ** 3
  if (gb >= 1) return `${gb.toFixed(1)} GB`
  return `${(bytes / 1024 ** 2).toFixed(0)} MB`
}

function formatSpeed(bytesPerSec: number): string {
  if (!bytesPerSec) return ''
  const mb = bytesPerSec / (1024 * 1024)
  if (mb >= 1) return `${mb.toFixed(1)} MB/s`
  return `${(bytesPerSec / 1024).toFixed(0)} KB/s`
}

function statusBadgeTone(status: RequestStatus | DL['status']): 'success' | 'danger' | 'warning' | 'info' {
  if (['complete', 'organized', 'completed', 'seeding'].includes(status)) return 'success'
  if (['failed', 'error'].includes(status)) return 'danger'
  if (['partial', 'paused', 'blocked'].includes(status)) return 'warning'
  return 'info'
}

function statusIcon(status: RequestStatus | DL['status']) {
  if (['complete', 'organized', 'completed', 'seeding'].includes(status)) return <CheckCircle2 size={14} />
  if (['failed', 'error'].includes(status)) return <XCircle size={14} />
  if (status === 'blocked') return <ShieldAlert size={14} />
  if (['pending', 'queued'].includes(status)) return <Clock size={14} />
  return <Loader2 size={14} className="animate-spin" />
}

function RequestCard({
  request,
  downloads,
  onRetry,
  onDelete,
}: {
  request: MediaRequest
  downloads: DL[]
  onRetry: (id: string) => void
  onDelete: (id: string) => void
}) {
  const linked = downloads.filter(d => request.download_ids.includes(d.id))
  const total = request.progress_total || linked.length || 1
  const found = Math.max(request.progress_found || 0, linked.filter(d => ['seeding', 'completed', 'organized'].includes(d.status)).length)
  const pct = Math.max(6, Math.min(100, Math.round((found / total) * 100)))
  const heading = request.title || request.query
  const seasonLabel = request.season ? `Season ${request.season}` : request.media_type === 'movie' ? 'Movie' : 'Request'
  const strategy = request.status === 'blocked'
    ? 'Waiting for health'
    : request.strategy === 'season_pack'
    ? 'Season pack'
    : request.strategy === 'episodes'
      ? 'Episode fallback'
      : request.strategy === 'movie'
        ? 'Best release'
        : 'Resolving'

  return (
    <Card className="p-4">
      <div className="flex items-start gap-4">
        <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg bg-primary/15 text-primary-light">
          {request.media_type === 'tv' ? <Tv size={20} /> : <Film size={20} />}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="truncate text-base font-semibold text-text">{heading}</h3>
            <Badge tone={statusBadgeTone(request.status)}>
              {statusIcon(request.status)}
              {request.status.replace('_', ' ')}
            </Badge>
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted">
            <span>{seasonLabel}</span>
            <span>·</span>
            <span>{request.quality}</span>
            <span>·</span>
            <span>{strategy}</span>
          </div>
          <Progress value={pct} className="mt-3" />
          <p className="mt-2 text-xs text-muted">
            {request.error_message || request.decision_summary || `Tracking ${found}/${total} items`}
          </p>
          {request.missing_episodes.length > 0 && (
            <p className="mt-1 text-xs text-amber-300">
              Missing episodes: {request.missing_episodes.join(', ')}
            </p>
          )}
          {linked.length > 0 && (
            <div className="mt-3 space-y-1.5">
              {linked.slice(0, 3).map(dl => (
                <div key={dl.id} className="flex items-center gap-2 rounded-md bg-bg/70 px-2.5 py-1.5 text-xs text-muted">
                  <Download size={12} />
                  <span className="min-w-0 flex-1 truncate">{dl.name}</span>
                  <span>{Math.round(dl.progress * 100)}%</span>
                </div>
              ))}
            </div>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-1">
          {['failed', 'partial', 'blocked'].includes(request.status) && (
            <Button variant="ghost" size="icon" onClick={() => onRetry(request.id)} title="Retry request">
              <RotateCw size={14} />
            </Button>
          )}
          <Button variant="ghost" size="icon" onClick={() => onDelete(request.id)} title="Remove request">
            <Trash2 size={14} />
          </Button>
        </div>
      </div>
    </Card>
  )
}

function DownloadRow({
  dl,
  onOrganize,
  onDelete,
}: {
  dl: DL
  onOrganize: (d: DL) => void
  onDelete: (d: DL) => void
}) {
  return (
    <div className="grid grid-cols-[1fr_auto] gap-3 border-b border-white/[0.08] px-4 py-3 last:border-b-0">
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <Badge tone={statusBadgeTone(dl.status)}>
            {statusIcon(dl.status)}
            {dl.status}
          </Badge>
          <p className="truncate text-sm font-medium text-text">{dl.name}</p>
        </div>
        <div className="mt-2 flex items-center gap-3 text-xs text-muted">
          <span>{formatSize(dl.size_bytes)}</span>
          {dl.download_speed > 0 && <span>{formatSpeed(dl.download_speed)}</span>}
          <span>{Math.round(dl.progress * 100)}%</span>
          {dl.stats_updated_at > 0 && (
            Date.now() / 1000 - dl.stats_updated_at > 120 &&
            ['queued', 'downloading', 'seeding'].includes(dl.status) ? (
              <span className="text-amber-300">
                stale — last update <RelativeTime ts={dl.stats_updated_at} />
              </span>
            ) : (
              <span>updated <RelativeTime ts={dl.stats_updated_at} /></span>
            )
          )}
          {!dl.stats_updated_at && <span>added <RelativeTime ts={dl.added_at} /></span>}
        </div>
        <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-white/[0.08]">
          <div className="h-full rounded-full bg-sky-300" style={{ width: `${Math.max(4, Math.round(dl.progress * 100))}%` }} />
        </div>
      </div>
      <div className="flex items-center gap-1">
        {['completed', 'seeding', 'error'].includes(dl.status) && (
          <Button variant="ghost" size="icon" onClick={() => onOrganize(dl)} title="Organize">
            <FolderInput size={14} />
          </Button>
        )}
        <Button variant="ghost" size="icon" onClick={() => onDelete(dl)} title="Remove">
          <Trash2 size={14} />
        </Button>
      </div>
    </div>
  )
}

function HealthPanel({
  health,
  onAction,
  running,
}: {
  health: Awaited<ReturnType<typeof getHealth>>
  onAction: (issueId: string) => void
  running: string | null
}) {
  if (health.issues.length === 0) return null
  return (
    <Card className="border-amber-300/20 bg-amber-300/[0.08] p-4">
      <div className="flex items-start gap-3">
        <XCircle size={18} className="mt-0.5 text-amber-200" />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-sm font-semibold text-amber-100">Sparrow needs attention</h2>
            {health.issues.length > 1 && <Badge tone="warning">{health.issues.length} issues</Badge>}
          </div>
          <div className="mt-3 space-y-3">
            {health.issues.map(issue => (
              <div key={issue.id} className="rounded-md border border-amber-200/15 bg-bg/35 p-3">
                <div className="flex flex-col items-start justify-between gap-3 sm:flex-row sm:items-start">
                  <div className="min-w-0">
                    <p className="text-sm font-medium text-amber-50">{issue.title}</p>
                    <p className="mt-1 text-sm text-amber-100/70">{issue.detail}</p>
                  </div>
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={() => onAction(issue.id)}
                    disabled={running === issue.id}
                    className="w-full shrink-0 border-amber-200/20 bg-bg/50 text-amber-100 hover:bg-bg/80 sm:w-auto"
                  >
                    {running === issue.id ? <Loader2 size={13} className="animate-spin" /> : <Wand2 size={13} />}
                    Try fix
                  </Button>
                </div>
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {issue.actions.map(action => (
                    <Badge key={action} tone="warning">{action}</Badge>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </Card>
  )
}

function StatCard({
  label,
  value,
  detail,
  tone = 'neutral',
}: {
  label: string
  value: string
  detail: string
  tone?: 'neutral' | 'success' | 'warning'
}) {
  return (
    <Card className="min-w-0 bg-panel/75 p-4">
      <p className="text-[11px] font-semibold uppercase tracking-wider text-muted">{label}</p>
      <div className="mt-2 flex items-end justify-between gap-3">
        <span className="text-2xl font-semibold tracking-tight text-text">{value}</span>
        <span className={clsx(
          'hidden max-w-[9rem] truncate rounded-md px-2 py-1 text-[11px] font-medium sm:inline-block',
          tone === 'success' && 'bg-emerald-400/10 text-emerald-300',
          tone === 'warning' && 'bg-amber-300/[0.08] text-amber-200',
          tone === 'neutral' && 'bg-white/5 text-muted',
        )}>{detail}</span>
      </div>
    </Card>
  )
}

function ReleaseCandidate({
  item,
  selected,
  onSelect,
}: {
  item: PreviewCandidate
  selected?: boolean
  onSelect?: () => void
}) {
  const facts = (item.score.facts || {}) as Record<string, unknown>
  const components = (item.score.components || {}) as Record<string, unknown>
  const quality = facts.quality ? String(facts.quality) : String(item.score.quality || '')
  const codec = facts.codec ? String(facts.codec) : String(item.score.codec || '')
  const source = facts.source ? String(facts.source) : String(item.score.source || '')
  const confidence = item.score.confidence ? String(item.score.confidence) : ''
  const size = facts.size_gb ? `${facts.size_gb} GB` : item.result.size_human
  const label = String(facts.label || 'Release option')
  const delivery = String(facts.delivery || facts.scope || '')
  const releaseGroup = String(facts.release_group || item.result.uploader || '')
  const coverage = Number(facts.coverage || 0)
  const episodeCount = Number(facts.episode_count || 0)
  const score = Number(item.score.score || 0)
  const strength = Number(facts.seeders || item.result.seeders || 0) >= 80 ? 'Excellent'
    : Number(facts.seeders || item.result.seeders || 0) >= 30 ? 'Strong'
      : Number(facts.seeders || item.result.seeders || 0) > 0 ? 'Available'
        : 'Unknown'
  const matchLabel = score >= 90 ? 'Best match' : score >= 75 ? 'Good match' : confidence === 'review' ? 'Check first' : 'Candidate'
  const detailTitle = `Score ${score || 'n/a'} combines title match, quality, size, source, availability, and risk checks.`
  return (
    <button
      type="button"
      onClick={onSelect}
      className={clsx(
        'w-full rounded-md border bg-bg/45 p-3 text-left transition-colors',
        selected ? 'border-primary/70 ring-1 ring-primary/35' : 'border-white/10 hover:border-primary/35',
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-semibold text-text">{label}</p>
          <p className="mt-0.5 text-[11px] text-muted">
            {[coverage && episodeCount ? `${coverage}/${episodeCount} episodes` : '', quality, codec, source].filter(Boolean).join(' · ')}
          </p>
        </div>
        <span title={detailTitle}>
          <Badge tone={confidence === 'auto' ? 'success' : confidence === 'reject' ? 'danger' : 'info'}>{matchLabel}</Badge>
        </span>
      </div>
      <div className="mt-1 flex flex-wrap gap-2 text-[11px] text-muted">
        {delivery && <span className="text-primary-light">{delivery.replace('_', ' ')}</span>}
        <span title={`${facts.seeders || item.result.seeders || 'Unknown'} seeders`}>{strength} availability</span>
        <span>{size}</span>
      </div>
      <details className="mt-2 text-[10px] text-muted" onClick={event => event.stopPropagation()}>
        <summary className="cursor-pointer select-none text-muted hover:text-text">Torrent details</summary>
        <div className="mt-2 space-y-1 rounded bg-white/[0.04] p-2">
          <p className="break-all">{item.result.name}</p>
          <p>Release group: {releaseGroup || 'unknown'}</p>
          <p>Score: {String(item.score.score ?? '?')} · Seeds: {String(components.seed_health ?? '-')} · Size: {String(components.size_sanity ?? '-')} · Match: {String(components.title_match ?? '-')}</p>
        </div>
      </details>
    </button>
  )
}

function PreviewPanel({
  preview,
  selectedCandidateIds,
  onSelectCandidate,
  onConfirm,
  confirming,
}: {
  preview: RequestPreview
  selectedCandidateIds: string[]
  onSelectCandidate: (item: PreviewCandidate) => void
  onConfirm: () => void
  confirming: boolean
}) {
  const candidateList = preview.series_candidates?.length
    ? preview.series_candidates
    : preview.pack_candidates.length
      ? preview.pack_candidates
      : preview.movie_candidates.length
        ? preview.movie_candidates
        : preview.episode_candidates
  const visibleCandidateCount = preview.strategy === 'series' ? 12 : 4
  const resolvedBits = [
    preview.resolved.media_type,
    preview.resolved.season ? `season ${preview.resolved.season}` : '',
    preview.resolved.episode_count ? `${preview.resolved.episode_count} episodes` : '',
    preview.resolved.quality,
  ].filter(Boolean)

  return (
    <Card className="mt-4 max-w-5xl border-primary/20 bg-primary/[0.05] p-4">
      <div className="flex items-start gap-3">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-primary/15 text-primary-light">
          <BarChart3 size={17} />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-sm font-semibold text-text">{preview.resolved.title}</h2>
            <Badge tone={preview.strategy === 'season_pack' ? 'success' : 'info'}>{preview.strategy.replace('_', ' ')}</Badge>
          </div>
          <p className="mt-1 text-xs text-muted">{resolvedBits.join(' · ')}</p>
          <p className="mt-3 text-sm text-text/85">{preview.summary || 'Sparrow compared candidate releases.'}</p>
          {candidateList.length > 0 && (
            <div className="mt-3 grid gap-2 lg:grid-cols-2">
              {candidateList.slice(0, visibleCandidateCount).map((item, idx) => (
                <ReleaseCandidate
                  key={`${item.result.info_hash || item.result.name}-${idx}`}
                  item={item}
                  selected={selectedCandidateIds.includes(item.result.info_hash || item.result.name)}
                  onSelect={() => onSelectCandidate(item)}
                />
              ))}
            </div>
          )}
          <div className="mt-4 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
            <p className="text-xs text-muted">
              Pick the release you want. Sparrow will only start downloading after you confirm.
            </p>
            <Button
              type="button"
              variant="primary"
              size="sm"
              onClick={onConfirm}
              disabled={confirming || selectedCandidateIds.length === 0}
              className="w-full sm:w-auto"
            >
              {confirming ? <Loader2 size={13} className="animate-spin" /> : <Download size={13} />}
              Start download{selectedCandidateIds.length > 1 ? ` (${selectedCandidateIds.length})` : ''}
            </Button>
          </div>
        </div>
      </div>
    </Card>
  )
}

function SuggestionRail({
  suggestions,
  loading,
  query,
  onPick,
}: {
  suggestions: SuggestResponse | null
  loading: boolean
  query: string
  onPick: (title: string) => void
}) {
  if (query.trim().length < 2) return null
  const items = suggestions?.tmdb ?? []
  const primary = items[0]
  const secondary = items.slice(1, 5).filter(item => item.poster_url)
  return (
    <div className="mt-5 max-w-5xl">
      <div className="mb-2 flex items-center justify-between">
        <p className="text-xs font-semibold uppercase tracking-wider text-muted">Suggestions</p>
        {loading && <span className="flex items-center gap-1.5 text-xs text-muted"><Loader2 size={12} className="animate-spin" /> Searching</span>}
      </div>
      <div className="grid gap-3 lg:grid-cols-[minmax(0,1.35fr)_minmax(18rem,1fr)]">
        {loading && items.length === 0 && (
          <>
            <div className="grid grid-cols-[5.5rem_1fr] gap-4 rounded-xl border border-white/10 bg-white/[0.055] p-3">
              <div className="aspect-[2/3] animate-pulse rounded-lg bg-white/[0.07]" />
              <div className="space-y-3 self-center">
                <div className="h-4 w-32 animate-pulse rounded bg-white/[0.07]" />
                <div className="h-6 w-56 animate-pulse rounded bg-white/[0.07]" />
                <div className="h-14 w-full animate-pulse rounded bg-white/[0.07]" />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-2">
              {Array.from({ length: 4 }).map((_, idx) => (
                <div key={idx} className="space-y-2">
                  <div className="aspect-[2/3] animate-pulse rounded-lg bg-white/[0.07]" />
                  <div className="h-3 w-24 animate-pulse rounded bg-white/[0.07]" />
                </div>
              ))}
            </div>
          </>
        )}
        {!loading && primary && (
          <button
            type="button"
            onClick={() => onPick(primary.title)}
            className="group grid min-w-0 grid-cols-[5.5rem_1fr] gap-4 rounded-xl border border-white/10 bg-white/[0.055] p-3 text-left shadow-xl shadow-black/20 transition-colors hover:border-primary/40"
          >
            <div className="aspect-[2/3] overflow-hidden rounded-lg bg-white/[0.06]">
              {primary.poster_url ? (
                <img src={primary.poster_url} alt="" className="h-full w-full object-cover" />
              ) : (
                <div className="flex h-full w-full items-center justify-center text-muted">
                  {primary.media_type === 'tv' ? <Tv size={22} /> : <Film size={22} />}
                </div>
              )}
            </div>
            <div className="min-w-0 self-center">
              <div className="mb-2 flex flex-wrap items-center gap-2">
                <Badge tone="info">{primary.media_type === 'tv' ? 'Series' : 'Movie'}</Badge>
                {primary.year && <span className="text-xs text-muted">{primary.year}</span>}
                {primary.rating > 0 && <span className="text-xs text-muted">{primary.rating}/10</span>}
              </div>
              <p className="truncate text-lg font-semibold text-text">{primary.title}</p>
              <p className="mt-2 line-clamp-3 text-sm leading-5 text-muted">{primary.overview}</p>
            </div>
          </button>
        )}
        {!loading && secondary.length > 0 && (
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-2">
            {secondary.map(item => (
              <button
                key={`${item.media_type}-${item.tmdb_id}`}
                type="button"
                onClick={() => onPick(item.title)}
                className="group min-w-0 text-left"
              >
                <div className="aspect-[2/3] overflow-hidden rounded-lg border border-white/10 bg-white/[0.06] shadow-lg shadow-black/25 transition-transform group-hover:-translate-y-0.5 group-hover:border-primary/40">
                  <img src={item.poster_url || ''} alt="" className="h-full w-full object-cover" />
                </div>
                <p className="mt-2 truncate text-sm font-semibold text-text">{item.title}</p>
                <p className="truncate text-xs text-muted">
                  {[item.media_type === 'tv' ? 'Series' : 'Movie', item.year].filter(Boolean).join(' · ')}
                </p>
              </button>
            ))}
          </div>
        )}
      </div>
      {!loading && items.length === 0 && (
        <div className="rounded-lg border border-white/10 bg-white/[0.04] px-3 py-3 text-sm text-muted">
          No title match yet. You can still review torrent options for “{query.trim()}”.
        </div>
      )}
    </div>
  )
}

export default function Downloads() {
  const [requests, setRequests] = useState<MediaRequest[]>([])
  const [downloads, setDownloads] = useState<DL[]>([])
  const [query, setQuery] = useState('')
  const [quality, setQuality] = useState('1080p')
  const [submitting, setSubmitting] = useState(false)
  const [previewing, setPreviewing] = useState(false)
  const [preview, setPreview] = useState<RequestPreview | null>(null)
  const [selectedCandidates, setSelectedCandidates] = useState<PreviewCandidate[]>([])
  const [suggestions, setSuggestions] = useState<SuggestResponse | null>(null)
  const [suggesting, setSuggesting] = useState(false)
  const [suggestOpen, setSuggestOpen] = useState(false)
  const [actionError, setActionError] = useState('')
  const [loading, setLoading] = useState(true)
  const [health, setHealth] = useState<Awaited<ReturnType<typeof getHealth>> | null>(null)
  const [healthAction, setHealthAction] = useState<string | null>(null)
  const [healthMessage, setHealthMessage] = useState('')

  const load = useCallback(async () => {
    const [reqs, dls, healthData] = await Promise.all([getRequests(), getDownloads(), getHealth()])
    setRequests(reqs)
    setDownloads(dls)
    setHealth(healthData)
    setLoading(false)
  }, [])

  useEffect(() => { load().catch(() => setLoading(false)) }, [load])

  useEffect(() => {
    const q = query.trim()
    setPreview(null)
    setSelectedCandidates([])
    if (q.length < 2) {
      setSuggestions(null)
      setSuggestOpen(false)
      return
    }
    let cancelled = false
    setSuggesting(true)
    const timer = window.setTimeout(() => {
      searchSuggest(q)
        .then(data => {
          if (!cancelled) {
            setSuggestions(data)
            setSuggestOpen(true)
          }
        })
        .catch(() => {
          if (!cancelled) setSuggestions(null)
        })
        .finally(() => {
          if (!cancelled) setSuggesting(false)
        })
    }, 220)
    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [query])

  useWebSocket((event) => {
    if (event.type === 'request_added') {
      const added = event.data as MediaRequest
      setRequests(prev => [added, ...prev.filter(r => r.id !== added.id)])
    } else if (event.type === 'request_update') {
      const updated = event.data as MediaRequest
      setRequests(prev => prev.map(r => r.id === updated.id ? updated : r))
    } else if (event.type === 'request_removed') {
      const { id } = event.data as { id: string }
      setRequests(prev => prev.filter(r => r.id !== id))
    } else if (event.type === 'download_update') {
      const updated = event.data as DL
      setDownloads(prev => prev.map(d => d.id === updated.id ? { ...d, ...updated } : d))
    } else if (event.type === 'download_added') {
      const added = event.data as DL
      setDownloads(prev => [added, ...prev.filter(d => d.id !== added.id)])
    } else if (event.type === 'download_removed') {
      const { id } = event.data as { id: string }
      setDownloads(prev => prev.filter(d => d.id !== id))
    }
  })

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!query.trim()) return
    await handlePreview()
  }

  const removeRequest = async (id: string) => {
    await deleteRequest(id)
    setRequests(prev => prev.filter(r => r.id !== id))
  }

  const removeDownload = async (dl: DL) => {
    if (!confirm(`Remove "${dl.name}" from tracking? Files are not deleted.`)) return
    await deleteDownload(dl.id)
    setDownloads(prev => prev.filter(d => d.id !== dl.id))
  }

  const handleHealthAction = async (issueId: string) => {
    setHealthAction(issueId)
    setHealthMessage('')
    try {
      const result = await runHealthAction(issueId)
      setHealth(result.health)
      setHealthMessage(result.message)
    } finally {
      setHealthAction(null)
    }
  }

  const handlePreview = async () => {
    if (!query.trim()) return
    setPreviewing(true)
    setPreview(null)
    setSelectedCandidates([])
    setSuggestOpen(false)
    try {
      const nextPreview = await previewRequest({ query: query.trim(), quality })
      setPreview(nextPreview)
      const preferred = nextPreview.series_candidates?.[0] || nextPreview.pack_candidates[0] || nextPreview.movie_candidates[0] || nextPreview.episode_candidates[0]
      if (preferred) setSelectedCandidates([preferred])
    } finally {
      setPreviewing(false)
    }
  }

  const handleSuggestionPick = async (title: string) => {
    setQuery(title)
    setSuggestOpen(false)
    setPreviewing(true)
    setPreview(null)
    setSelectedCandidates([])
    try {
      const nextPreview = await previewRequest({ query: title, quality })
      setPreview(nextPreview)
      const preferred = nextPreview.series_candidates?.[0] || nextPreview.pack_candidates[0] || nextPreview.movie_candidates[0] || nextPreview.episode_candidates[0]
      if (preferred) setSelectedCandidates([preferred])
    } finally {
      setPreviewing(false)
    }
  }

  const toggleCandidate = (item: PreviewCandidate) => {
    const id = item.result.info_hash || item.result.name
    const facts = (item.score.facts || {}) as Record<string, unknown>
    const isCompleteSeries = facts.scope === 'complete_series'
    setSelectedCandidates(prev => {
      const exists = prev.some(candidate => (candidate.result.info_hash || candidate.result.name) === id)
      if (exists) return prev.filter(candidate => (candidate.result.info_hash || candidate.result.name) !== id)
      if (preview?.strategy !== 'series' || isCompleteSeries) return [item]
      return [
        ...prev.filter(candidate => ((candidate.score.facts || {}) as Record<string, unknown>).scope !== 'complete_series'),
        item,
      ]
    })
  }

  const confirmSelected = async () => {
    if (!preview || selectedCandidates.length === 0) return
    setSubmitting(true)
    setActionError('')
    try {
      const selectedItems = selectedCandidates.flatMap(candidate => candidate.results?.length ? candidate.results : [candidate.result])
      const created = await createSelectedRequest({
        query: preview.query,
        quality,
        resolved: preview.resolved,
        selected: selectedItems[0],
        selected_items: selectedItems,
        strategy: preview.strategy,
        score: selectedCandidates[0].score,
        summary: preview.summary,
      })
      setRequests(prev => [created, ...prev.filter(r => r.id !== created.id)])
      setPreview(null)
      setSelectedCandidates([])
      setQuery('')
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Could not start download')
    } finally {
      setSubmitting(false)
    }
  }

  const planningRequests = requests.filter(r => !['complete', 'downloading', 'organizing'].includes(r.status))
  const completeRequests = requests.filter(r => r.status === 'complete')
  const activeDownloads = downloads.filter(dl => ['queued', 'downloading', 'organizing'].includes(dl.status))
  const healthIssueCount = health?.issues.length ?? 0
  const examples = ['The Last of Us season 2', 'Interstellar 4K', 'Breaking Bad']

  return (
    <div className="cinema-page">
      <section className="cinema-hero min-h-[560px] border-b border-white/[0.08]">
        <div className="absolute inset-0 bg-[radial-gradient(circle_at_18%_12%,rgba(244,193,93,0.24),transparent_28rem),radial-gradient(circle_at_78%_18%,rgba(194,90,46,0.20),transparent_26rem),linear-gradient(115deg,rgba(5,5,7,0.98)_8%,rgba(12,10,12,0.76)_46%,rgba(5,5,7,0.96)_100%)]" />
        <div className="absolute bottom-[-18%] right-[-8%] hidden h-[32rem] w-[32rem] rounded-full border border-primary/10 bg-primary/[0.035] blur-3xl lg:block" />
        <div className="cinema-shell">
          <div className="mb-8 flex flex-col items-start justify-between gap-3 sm:flex-row sm:items-center">
            <div>
              <div className="cinema-kicker">
                <Sparkles size={13} />
                Now acquiring
              </div>
              <h1 className="cinema-title">What should Sparrow find?</h1>
              <p className="cinema-copy">
                Search like a streaming app. Sparrow compares the best releases, prefers full seasons, and quietly turns downloads into a clean library.
              </p>
            </div>
            <Button variant="secondary" size="sm" onClick={load}>
              <RefreshCw size={14} />
              Refresh
            </Button>
          </div>

          <form onSubmit={submit} className="glass-panel flex w-full max-w-full flex-col gap-2 p-2.5 sm:max-w-4xl sm:flex-row sm:p-2">
            <div className="relative min-w-0 flex-1">
              <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
              <input
                value={query}
                onChange={e => setQuery(e.target.value)}
                onFocus={() => query.trim().length >= 2 && setSuggestOpen(true)}
                className="h-14 w-full rounded-full bg-transparent pl-10 pr-3 text-base text-text outline-none placeholder:text-muted"
                placeholder="Search a film, show, or season"
              />
            </div>
            <select
              value={quality}
              onChange={e => setQuality(e.target.value)}
              className="h-14 w-full rounded-full border border-white/10 bg-black/50 px-4 text-sm text-text outline-none focus:ring-1 focus:ring-primary sm:w-auto"
              aria-label="Quality"
            >
              <option value="1080p">1080p</option>
              <option value="2160p">4K</option>
              <option value="720p">720p</option>
              <option value="any">Any</option>
            </select>
            <Button variant="primary" className="h-14 w-full shrink-0 px-6 sm:w-auto" disabled={previewing || !query.trim()}>
              {previewing ? <Loader2 size={15} className="animate-spin" /> : <ArrowRight size={15} />}
              Review options
            </Button>
          </form>

          {!preview && (
            <SuggestionRail
              suggestions={suggestions}
              loading={suggesting}
              query={query}
              onPick={handleSuggestionPick}
            />
          )}

          <div className="mt-3 flex max-w-3xl flex-wrap gap-2">
            {examples.map(example => (
              <button
                key={example}
                type="button"
              onClick={() => {
                setQuery(example)
                setSuggestOpen(false)
              }}
                className="rounded-full border border-white/10 bg-white/[0.06] px-3 py-1.5 text-xs font-medium text-muted backdrop-blur-xl transition-colors hover:border-primary/30 hover:text-text"
              >
                {example}
              </button>
            ))}
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={handlePreview}
              disabled={previewing || !query.trim()}
              className="border border-primary/20 text-primary-light"
            >
              {previewing ? <Loader2 size={13} className="animate-spin" /> : <BarChart3 size={13} />}
              Compare releases
            </Button>
          </div>

          {preview && (
            <PreviewPanel
              preview={preview}
              selectedCandidateIds={selectedCandidates.map(candidate => candidate.result.info_hash || candidate.result.name)}
              onSelectCandidate={toggleCandidate}
              onConfirm={confirmSelected}
              confirming={submitting}
            />
          )}
          {actionError && (
            <div className="mt-3 max-w-5xl rounded-lg border border-red-300/20 bg-red-400/[0.08] px-3 py-2 text-sm text-red-100">
              {actionError}
            </div>
          )}

          <div className="mt-8 grid max-w-5xl gap-3 lg:grid-cols-3">
            <Card className="p-4">
              <p className="text-sm font-semibold text-text">Resolve the title</p>
              <p className="mt-1 text-xs text-muted">Canonical movie, show, season, episode count, and artwork metadata.</p>
            </Card>
            <Card className="p-4">
              <p className="text-sm font-semibold text-text">Pick the best release</p>
              <p className="mt-1 text-xs text-muted">Season pack first, scored by size, seeders, codec, and source.</p>
            </Card>
            <Card className="p-4">
              <p className="text-sm font-semibold text-text">Finish the shelf</p>
              <p className="mt-1 text-xs text-muted">Organize silently, then verify the result after the move.</p>
            </Card>
          </div>

          <div className="mt-3 grid max-w-5xl gap-3 sm:grid-cols-3">
            <StatCard
              label="Requests"
              value={`${requests.length}`}
              detail={`${planningRequests.length} planning`}
            />
            <StatCard
              label="Library"
              value={`${health?.summary.library ?? 0}`}
              detail="items ready"
              tone="success"
            />
            <StatCard
              label="Health"
              value={healthIssueCount === 0 ? 'Ready' : `${healthIssueCount}`}
              detail={healthIssueCount === 0 ? 'all clear' : 'needs attention'}
              tone={healthIssueCount === 0 ? 'success' : 'warning'}
            />
          </div>
        </div>
      </section>

      <div className="w-full max-w-7xl space-y-8 overflow-hidden p-4 sm:p-8">
        {loading ? (
          <div className="flex items-center gap-2 text-sm text-muted">
            <Loader2 size={14} className="animate-spin" /> Loading requests...
          </div>
        ) : (
          <>
            {health && !health.healthy && (
              <HealthPanel health={health} onAction={handleHealthAction} running={healthAction} />
            )}
            {healthMessage && (
              <p className="rounded-md border border-white/10 bg-panel px-3 py-2 text-sm text-muted">{healthMessage}</p>
            )}

            <section>
              <SectionHeader title="Planning" meta={`${planningRequests.length} needs attention`} />
              {planningRequests.length === 0 ? (
                <div className="glass-panel overflow-hidden p-0">
                  <div className="relative p-8 text-center sm:p-12">
                    <div className="absolute inset-0 bg-[radial-gradient(circle_at_center,rgba(244,193,93,0.10),transparent_24rem)]" />
                    <div className="relative">
                      <Clapperboard size={40} className="mx-auto text-primary-light/60" />
                      <p className="mt-4 text-base font-semibold text-text">Nothing waiting for a decision</p>
                      <p className="mx-auto mt-2 max-w-md text-sm text-muted">
                        Search above, inspect the release options, then start the download you want.
                      </p>
                    </div>
                  </div>
                </div>
              ) : (
                <div className="grid gap-3 lg:grid-cols-2">
                  {planningRequests.map(request => (
                    <RequestCard
                      key={request.id}
                      request={request}
                      downloads={downloads}
                      onRetry={id => retryRequest(id).then(updated => setRequests(prev => prev.map(r => r.id === id ? updated : r)))}
                      onDelete={removeRequest}
                    />
                  ))}
                </div>
              )}
            </section>

            {completeRequests.length > 0 && (
              <section>
                <SectionHeader title="Ready to watch" icon={<PackageCheck size={15} className="text-emerald-300" />} />
                <div className="grid gap-3 lg:grid-cols-2">
                  {completeRequests.slice(0, 6).map(request => (
                    <RequestCard
                      key={request.id}
                      request={request}
                      downloads={downloads}
                      onRetry={id => retryRequest(id).then(updated => setRequests(prev => prev.map(r => r.id === id ? updated : r)))}
                      onDelete={removeRequest}
                    />
                  ))}
                </div>
              </section>
            )}

            <section>
              <SectionHeader title="Now downloading" meta={`${activeDownloads.length} active`} />
              <div className="glass-panel overflow-hidden">
                {activeDownloads.length === 0 ? (
                  <div className="p-8 text-center text-sm text-muted sm:p-10">
                    <HardDrive size={34} className="mx-auto mb-3 text-muted/35" />
                    Confirm a release and the live transfer appears here.
                  </div>
                ) : (
                  activeDownloads.map(dl => (
                    <DownloadRow
                      key={dl.id}
                      dl={dl}
                      onOrganize={d => organizeDownload(d.id)}
                      onDelete={removeDownload}
                    />
                  ))
                )}
              </div>
            </section>
          </>
        )}
      </div>
    </div>
  )
}
