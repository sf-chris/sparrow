import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import {
  Activity, AlertTriangle, ArrowLeft, Check, ChevronDown, Clock, Download, Film, Loader2,
  Pause, Play, RefreshCw, ShieldCheck, SlidersHorizontal, Sparkles, Star, Trash2, Tv,
} from 'lucide-react'
import clsx from 'clsx'
import {
  ApiError, cancelJob, createJob, getJob, getJobs, getMandate, getShow, nudgeJob, pauseJob,
  resumeJob, setMonitoring,
} from '../api/client'
import type { CreateJobPayload } from '../api/client'
import type {
  ActivityEvent, Download as DownloadItem, ExecutionTraceEntry, Job, JobDetail, JournalEntry,
  Mandate, MonitoringMode, ShowDetail, ShowEpisode, ShowSeason,
} from '../types'
import { useWebSocket } from '../hooks/useWebSocket'
import { Badge, Button, Card, Progress, RelativeTime } from '../components/ui'

const QUALITY_OPTIONS = [
  { value: '', label: 'Best available' },
  { value: '2160p', label: '4K' },
  { value: '1080p', label: 'Full HD' },
  { value: '720p', label: 'HD' },
] as const

/** Transfer stats older than this are presented as stale, not current. */
const STALE_AFTER_SECONDS = 120

type TabId = 'watch' | 'progress' | 'preferences'

function qualityOptionLabel(value: string): string {
  return QUALITY_OPTIONS.find(option => option.value === value)?.label || 'Best available'
}

function qualityLabel(quality: string): string {
  switch (quality) {
    case '2160p': return '4K'
    case '1080p': return 'Full HD'
    case '720p': return 'HD'
    case '480p': return 'SD'
    default: return ''
  }
}

function episodeTitle(episode: ShowEpisode): string {
  if (episode.have) {
    const label = qualityLabel(episode.quality)
    return `Episode ${episode.episode}${label ? ` — ${label}` : ' — in your library'}`
  }
  if (episode.in_flight) return `Episode ${episode.episode} — on the way`
  return `Episode ${episode.episode} — not here yet`
}

function seasonEpisodes(season: ShowSeason): ShowEpisode[] {
  if (season.episodes.length > 0) return season.episodes
  return Array.from({ length: season.episode_count }, (_, i) => ({
    episode: i + 1, have: false, quality: '', in_flight: false,
  }))
}

function SeasonBadge({ season }: { season: ShowSeason }) {
  if (season.episode_count === 0) {
    return <Badge tone="neutral">Not aired yet</Badge>
  }
  if (season.episode_count > 0 && season.have_count >= season.episode_count) {
    return <Badge tone="success"><Check size={11} /> Complete</Badge>
  }
  if (season.in_flight_count > 0) {
    return (
      <Badge tone="info">
        On the way — {season.have_count} of {season.episode_count} here
      </Badge>
    )
  }
  if (season.have_count > 0) {
    return <Badge tone="warning">{season.have_count} of {season.episode_count} episodes</Badge>
  }
  return <Badge tone="neutral">Not in library</Badge>
}

function EpisodePill({ episode }: { episode: ShowEpisode }) {
  return (
    <span
      title={episodeTitle(episode)}
      className={clsx(
        'flex h-8 w-8 items-center justify-center rounded-full border text-[11px] font-semibold',
        episode.have
          ? 'border-emerald-400/30 bg-emerald-400/15 text-emerald-300'
          : episode.in_flight
            ? 'animate-pulse border-amber-300/50 bg-amber-300/[0.06] text-amber-200'
            : 'border-white/10 bg-transparent text-muted/50',
      )}
    >
      {episode.episode}
    </span>
  )
}

function QualityDisclosure({
  quality,
  onChange,
}: {
  quality: string
  onChange: (value: string) => void
}) {
  const [open, setOpen] = useState(false)
  return (
    <div className="min-w-0">
      <button
        type="button"
        className="inline-flex items-center gap-1 text-xs text-muted transition-colors hover:text-text"
        onClick={() => setOpen(prev => !prev)}
      >
        Options · {qualityOptionLabel(quality)}
        <ChevronDown size={12} className={clsx('transition-transform', open && 'rotate-180')} />
      </button>
      {open && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {QUALITY_OPTIONS.map(option => (
            <button
              key={option.value}
              type="button"
              onClick={() => onChange(option.value)}
              className={clsx(
                'rounded-full border px-2.5 py-1 text-[11px] font-semibold transition-colors',
                quality === option.value
                  ? 'border-primary/40 bg-primary/15 text-primary-light'
                  : 'border-white/10 bg-white/5 text-muted hover:text-text',
              )}
            >
              {option.label}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

// --- Time / formatting helpers ---

function formatCountdown(secondsLeft: number): string {
  if (secondsLeft <= 0) return 'any moment now'
  const minutes = Math.max(1, Math.floor(secondsLeft / 60))
  if (minutes < 60) return `${minutes}m`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h ${minutes % 60}m`
  const days = Math.floor(hours / 24)
  return `${days}d ${hours % 24}h`
}

function formatSpeed(bytesPerSecond: number): string {
  if (!bytesPerSecond || bytesPerSecond <= 0) return ''
  const mb = bytesPerSecond / (1024 * 1024)
  if (mb >= 1) return `${mb.toFixed(1)} MB/s`
  return `${Math.max(1, Math.round(bytesPerSecond / 1024))} KB/s`
}

function formatEtaShort(seconds: number): string {
  const minutes = Math.max(1, Math.round(seconds / 60))
  if (minutes < 60) return `about ${minutes} min left`
  return `about ${Math.floor(minutes / 60)}h ${minutes % 60}m left`
}

function formatEtaRemaining(seconds: number): string {
  const minutes = Math.max(1, Math.round(seconds / 60))
  if (minutes < 60) return `about ${minutes} minute${minutes === 1 ? '' : 's'} remaining`
  const hours = Math.floor(minutes / 60)
  const rest = minutes % 60
  return `about ${hours}h${rest > 0 ? ` ${rest}m` : ''} remaining`
}

function isStale(dl: DownloadItem, nowSeconds: number): boolean {
  return dl.stats_updated_at > 0 && nowSeconds - dl.stats_updated_at > STALE_AFTER_SECONDS
}

function CollapsibleText({ text }: { text: string }) {
  const [open, setOpen] = useState(false)
  const long = text.length > 280 || text.includes('\n')
  return (
    <>
      <p className={clsx(
        'whitespace-pre-line break-words text-sm leading-relaxed text-text/90',
        long && !open && 'line-clamp-3',
      )}>
        {text}
      </p>
      {long && (
        <button
          type="button"
          className="mt-1 text-xs font-semibold text-primary-light hover:underline"
          onClick={() => setOpen(prev => !prev)}
        >
          {open ? 'Show less' : 'Read full update'}
        </button>
      )}
    </>
  )
}

const AGENT_DOT: Record<string, string> = {
  fetch: 'bg-sky-400',
  media: 'bg-violet-400',
  librarian: 'bg-emerald-400',
}

/** Turn a release string into something a person would say — never show the raw name outside Advanced. */
function humanizeDownloadName(name: string): string {
  let s = name.replace(/[._]/g, ' ').replace(/\[[^\]]*\]/g, ' ')
  const cut = s.search(/\b(2160p|1080p|720p|480p|WEB[- ]?DL|WEBRip|BluRay|BDRip|DVDRip|HDTV|x264|x265|HEVC|H ?26[45]|AAC|AC3|DDP?|REMUX|10bit|PROPER|REPACK)\b/i)
  if (cut > 0) s = s.slice(0, cut)
  s = s.replace(/\s+/g, ' ').trim().replace(/[-–—]+$/, '').trim()
  return s || 'Download'
}

/** Best-effort season number for a transfer, from metadata or the release name. */
function downloadSeason(dl: DownloadItem): number | null {
  const raw = dl.metadata?.season
  if (typeof raw === 'number' && Number.isFinite(raw)) return raw
  if (typeof raw === 'string' && /^\d+$/.test(raw)) return Number(raw)
  const match = dl.name.match(/\bS(\d{1,2})(?:E\d{1,3})?\b/i)
    || dl.name.match(/\bSeason[ ._-]?(\d{1,2})\b/i)
  if (match) return Number(match[1])
  return null
}

const COMPLETED_STATUSES: DownloadItem['status'][] = ['seeding', 'completed', 'organized']

// --- Contract statement (always visible, in the page header) ---

function ContractStatement({ summary }: { summary: string }) {
  if (!summary) return null
  const lines = summary.split(' · ')
  return (
    <div className="mt-6 inline-flex max-w-full items-start gap-3 rounded-xl border border-white/[0.12] bg-white/[0.05] px-4 py-3 backdrop-blur-xl">
      <ShieldCheck size={16} className="mt-0.5 shrink-0 text-primary-light" />
      <div className="min-w-0">
        {lines.map((line, i) => (
          <p
            key={i}
            className={clsx(
              'break-words',
              i === 0 ? 'text-sm font-semibold text-text' : 'mt-0.5 text-xs text-muted',
            )}
          >
            {line}
          </p>
        ))}
      </div>
    </div>
  )
}

// --- Title-level summary ("Downloading Season 1 · 3 of 8 ready · 42% overall · …") ---

interface RequestScope {
  label: string
  ready: number
  total: number
  hasCounts: boolean
}

function computeScope(job: Job, show: ShowDetail): RequestScope {
  if (show.media_type === 'movie') {
    return { label: 'the movie', ready: show.in_library ? 1 : 0, total: 1, hasCounts: true }
  }
  const wanted = job.wanted_episodes || {}
  const seasonNumbers = Object.keys(wanted)
    .map(Number)
    .filter(n => Number.isFinite(n))
    .sort((a, b) => a - b)
  const countedSeasons = seasonNumbers.length > 0
    ? show.seasons.filter(s => seasonNumbers.includes(s.season_number))
    : show.seasons.filter(s => s.episode_count > 0)
  let ready = 0
  let total = 0
  let hasCounts = false
  for (const season of countedSeasons) {
    const wantedEpisodes = seasonNumbers.length > 0 ? wanted[String(season.season_number)] : []
    if (wantedEpisodes && wantedEpisodes.length > 0 && season.episodes.length > 0) {
      const wantedSet = new Set(wantedEpisodes)
      ready += season.episodes.filter(e => wantedSet.has(e.episode) && e.have).length
      total += wantedEpisodes.length
      hasCounts = true
    } else if (season.episode_count > 0) {
      ready += Math.min(season.have_count, season.episode_count)
      total += season.episode_count
      hasCounts = true
    }
  }
  const label = seasonNumbers.length === 0
    ? 'the whole show'
    : seasonNumbers.length === 1
      ? `Season ${seasonNumbers[0]}`
      : `Seasons ${seasonNumbers.join(', ')}`
  return { label, ready, total, hasCounts }
}

/** One honest, compact line. Segments we can't compute are simply omitted. */
function titleSummaryLine(detail: JobDetail, show: ShowDetail, nowSeconds: number): string {
  const { job, downloads } = detail
  const scope = computeScope(job, show)
  const segments: string[] = []

  const active = downloads.filter(dl => dl.status === 'downloading' || dl.status === 'queued')
  const anyDownloading = downloads.some(dl => dl.status === 'downloading')

  if (job.status === 'paused') segments.push(`Paused — ${scope.label}`)
  else if (job.status === 'complete') segments.push(`Finished ${scope.label}`)
  else if (job.status === 'abandoned') segments.push(`Stopped working on ${scope.label}`)
  else segments.push(`${anyDownloading ? 'Downloading' : 'Working on'} ${scope.label}`)

  if (scope.hasCounts && scope.total > 0) {
    segments.push(`${scope.ready} of ${scope.total} ready`)
  }

  const sized = active.filter(dl => dl.size_bytes > 0)
  if (sized.length > 0) {
    const downloaded = sized.reduce((sum, dl) => sum + (dl.downloaded_bytes || 0), 0)
    const totalBytes = sized.reduce((sum, dl) => sum + dl.size_bytes, 0)
    if (totalBytes > 0) {
      segments.push(`${Math.round((downloaded / totalBytes) * 100)}% overall`)
    }
  }

  if (job.status === 'active') {
    const freshEtas = active
      .filter(dl => !isStale(dl, nowSeconds))
      .map(dl => dl.eta_seconds)
      .filter(s => s > 0)
    if (freshEtas.length > 0) {
      segments.push(formatEtaRemaining(Math.max(...freshEtas)))
    }
  }

  return segments.join(' · ')
}

// --- Transfers (grouped, plain words; raw evidence lives in Advanced) ---

function transferStatusBits(dl: DownloadItem, nowSeconds: number): string {
  const pct = Math.round((dl.progress || 0) * 100)
  if (dl.status === 'queued' && pct === 0) return 'Waiting to start'
  if (dl.status === 'paused') return `Paused at ${pct}%`
  if (dl.status === 'organizing') return 'Tidying up'
  const bits: string[] = [`${pct}%`]
  if (!isStale(dl, nowSeconds)) {
    const speed = formatSpeed(dl.download_speed)
    if (speed) bits.push(speed)
    if (dl.eta_seconds > 0) bits.push(formatEtaShort(dl.eta_seconds))
  }
  return bits.join(' · ')
}

function ActiveTransferRow({ dl, nowSeconds }: { dl: DownloadItem; nowSeconds: number }) {
  const stale = isStale(dl, nowSeconds)
  const showBar = dl.status === 'downloading' || dl.status === 'queued' || dl.status === 'paused'
  return (
    <li className="py-2.5">
      <div className="flex min-w-0 items-center justify-between gap-3">
        <p className="min-w-0 truncate text-sm text-text/90">{humanizeDownloadName(dl.name)}</p>
        <span className="shrink-0 text-xs text-muted">{transferStatusBits(dl, nowSeconds)}</span>
      </div>
      {showBar && <Progress value={(dl.progress || 0) * 100} className="mt-2" />}
      <p className="mt-1 text-[10px] text-muted/60">
        {dl.stats_updated_at > 0 ? (
          stale ? (
            <span className="text-amber-200/80">
              Stale — these numbers were last confirmed <RelativeTime ts={dl.stats_updated_at} />
            </span>
          ) : (
            <>Updated <RelativeTime ts={dl.stats_updated_at} /></>
          )
        ) : (
          'No progress report yet'
        )}
      </p>
    </li>
  )
}

function FailedTransferRow({ dl }: { dl: DownloadItem }) {
  return (
    <li className="py-2.5">
      <div className="flex min-w-0 items-center justify-between gap-3">
        <p className="min-w-0 truncate text-sm text-text/90">{humanizeDownloadName(dl.name)}</p>
        <span className="shrink-0 text-xs text-rose-300">Hit a snag</span>
      </div>
      {dl.error_message && (
        <p className="mt-1 break-words text-xs text-rose-200/80">{dl.error_message}</p>
      )}
      <p className="mt-1 text-[10px] text-muted/60">
        <RelativeTime ts={dl.stats_updated_at || dl.added_at} />
      </p>
    </li>
  )
}

function CompletedTransferRow({ dl }: { dl: DownloadItem }) {
  return (
    <li className="flex min-w-0 items-center justify-between gap-3 py-2">
      <p className="min-w-0 truncate text-sm text-text/80">{humanizeDownloadName(dl.name)}</p>
      <span className="inline-flex shrink-0 items-center gap-2 text-xs text-muted">
        <span className="text-emerald-300">Done</span>
        <RelativeTime ts={dl.completed_at || dl.added_at} className="text-[11px] text-muted/70" />
      </span>
    </li>
  )
}

function TransfersSection({ downloads, nowSeconds }: {
  downloads: DownloadItem[]
  nowSeconds: number
}) {
  const failed = downloads.filter(dl => dl.status === 'error')
  const completed = downloads.filter(dl => COMPLETED_STATUSES.includes(dl.status))
  const active = downloads.filter(dl =>
    dl.status !== 'error' && !COMPLETED_STATUSES.includes(dl.status))

  // Group active transfers by season when determinable; otherwise one group.
  const activeGroups = useMemo(() => {
    const bySeason = new Map<number | null, DownloadItem[]>()
    const anySeason = active.some(dl => downloadSeason(dl) != null)
    for (const dl of active) {
      const key = anySeason ? downloadSeason(dl) : null
      const bucket = bySeason.get(key)
      if (bucket) bucket.push(dl)
      else bySeason.set(key, [dl])
    }
    return [...bySeason.entries()].sort((a, b) => {
      if (a[0] == null) return 1
      if (b[0] == null) return -1
      return a[0] - b[0]
    })
  }, [active])

  if (downloads.length === 0) {
    return <p className="text-sm text-muted">No transfers yet.</p>
  }

  return (
    <div className="space-y-4">
      {failed.length > 0 && (
        <div>
          <p className="inline-flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-rose-300">
            <AlertTriangle size={13} /> Needs attention
          </p>
          <ul className="mt-1 divide-y divide-white/[0.06]">
            {failed.map(dl => <FailedTransferRow key={dl.id} dl={dl} />)}
          </ul>
        </div>
      )}

      {activeGroups.map(([season, items]) => {
        const label = season != null ? `Season ${season}` : 'On the way'
        const sized = items.filter(dl => dl.size_bytes > 0)
        const downloadedBytes = sized.reduce((sum, dl) => sum + (dl.downloaded_bytes || 0), 0)
        const totalBytes = sized.reduce((sum, dl) => sum + dl.size_bytes, 0)
        const pct = totalBytes > 0 ? Math.round((downloadedBytes / totalBytes) * 100) : null
        return (
          <details
            key={season ?? 'ungrouped'}
            open={activeGroups.length === 1}
            className="rounded-xl border border-white/[0.08] bg-white/[0.02] px-3 py-2"
          >
            <summary className="cursor-pointer select-none text-xs font-semibold text-text/80 hover:text-text">
              {label} — {items.length} transfer{items.length === 1 ? '' : 's'}
              {pct != null && <span className="ml-1 text-muted">· {pct}%</span>}
            </summary>
            <ul className="mt-1 divide-y divide-white/[0.06]">
              {items.map(dl => <ActiveTransferRow key={dl.id} dl={dl} nowSeconds={nowSeconds} />)}
            </ul>
          </details>
        )
      })}

      {completed.length > 0 && (
        <details className="rounded-xl border border-white/[0.08] bg-white/[0.02] px-3 py-2">
          <summary className="cursor-pointer select-none text-xs font-semibold text-muted hover:text-text">
            {completed.length} finished — show
          </summary>
          <ul className="mt-1 divide-y divide-white/[0.06]">
            {completed.map(dl => <CompletedTransferRow key={dl.id} dl={dl} />)}
          </ul>
        </details>
      )}
    </div>
  )
}

// --- Advanced / technical evidence (raw names, hashes, execution trace) ---

function ExecutionRow({ event }: { event: ActivityEvent }) {
  const [open, setOpen] = useState(false)
  const hasDetail = Boolean(event.detail?.trim())
  return (
    <li className="py-2.5">
      <div className="flex min-w-0 items-start gap-3">
        <span className={clsx(
          'mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full',
          event.level === 'error' ? 'bg-rose-400'
            : event.level === 'warning' ? 'bg-amber-300'
              : event.level === 'success' ? 'bg-emerald-400' : 'bg-white/30',
        )} />
        <div className="min-w-0 flex-1">
          <div className="flex min-w-0 items-baseline justify-between gap-3">
            <p className="min-w-0 break-words text-sm text-text/85">{event.message}</p>
            <RelativeTime ts={event.timestamp} className="shrink-0 text-[11px] text-muted/70" />
          </div>
          {hasDetail && (
            <button
              type="button"
              className="mt-1 text-[11px] font-semibold text-muted hover:text-text"
              onClick={() => setOpen(prev => !prev)}
            >
              {open ? 'Hide technical details' : 'Technical details'}
            </button>
          )}
          {hasDetail && open && (
            <pre className="mt-2 max-h-72 overflow-auto whitespace-pre-wrap break-words rounded-lg border border-white/10 bg-black/25 p-3 font-mono text-[10px] leading-relaxed text-muted">
              {event.detail}
            </pre>
          )}
        </div>
      </div>
    </li>
  )
}

function TraceRow({ entry }: { entry: ExecutionTraceEntry }) {
  const [open, setOpen] = useState(false)
  return (
    <li className="py-2.5">
      <div className="flex min-w-0 items-start gap-3">
        <span className={clsx(
          'mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full',
          entry.is_error ? 'bg-amber-300' : 'bg-white/30',
        )} />
        <div className="min-w-0 flex-1">
          <p className="break-words text-sm text-text/85">{entry.message}</p>
          <button
            type="button"
            className="mt-1 text-[11px] font-semibold text-muted hover:text-text"
            onClick={() => setOpen(prev => !prev)}
          >
            {open ? 'Hide technical details' : 'Technical details'}
          </button>
          {open && (
            <pre className="mt-2 max-h-72 overflow-auto whitespace-pre-wrap break-words rounded-lg border border-white/10 bg-black/25 p-3 font-mono text-[10px] leading-relaxed text-muted">
              {`Tool: ${entry.tool}\n\nInput:\n${JSON.stringify(entry.input, null, 2)}${entry.result ? `\n\nResult:\n${entry.result}` : ''}`}
            </pre>
          )}
        </div>
      </div>
    </li>
  )
}

function AdvancedSection({ detail }: { detail: JobDetail }) {
  const execution = detail.activity || []
  const trace = detail.trace || []
  const { downloads } = detail
  return (
    <details className="mt-5 border-t border-white/[0.08] pt-4">
      <summary className="cursor-pointer text-xs font-semibold uppercase tracking-wide text-muted/70 hover:text-text">
        Advanced — technical evidence
      </summary>

      {downloads.length > 0 && (
        <div className="mt-3">
          <p className="text-[10px] font-semibold uppercase tracking-wide text-muted/60">Raw transfer records</p>
          <ul className="mt-1 divide-y divide-white/[0.06]">
            {downloads.map(dl => (
              <li key={dl.id} className="py-2">
                <p className="break-all font-mono text-[10px] leading-relaxed text-muted">{dl.name}</p>
                <p className="mt-0.5 break-all font-mono text-[10px] text-muted/60">
                  {dl.status} · {Math.round((dl.progress || 0) * 100)}%
                  {dl.torrent_hash ? ` · ${dl.torrent_hash}` : ''}
                  {dl.quality ? ` · ${dl.quality}` : ''}
                </p>
              </li>
            ))}
          </ul>
        </div>
      )}

      {execution.length === 0 && trace.length === 0 ? (
        <p className="mt-3 text-sm text-muted">Detailed steps will appear here on the next agent run.</p>
      ) : (
        <div className="mt-3 max-h-96 overflow-y-auto pr-1">
          {execution.length > 0 && (
            <>
              <p className="py-2 text-[10px] font-semibold uppercase tracking-wide text-muted/60">Live execution</p>
              <ul className="divide-y divide-white/[0.06]">
                {execution.map(event => <ExecutionRow key={event.id} event={event} />)}
              </ul>
            </>
          )}
          {trace.length > 0 && (
            <>
              <p className="border-t border-white/[0.06] py-2 text-[10px] font-semibold uppercase tracking-wide text-muted/60">
                Saved session history
              </p>
              <ul className="divide-y divide-white/[0.06]">
                {[...trace].reverse().map(entry => <TraceRow key={entry.id} entry={entry} />)}
              </ul>
            </>
          )}
        </div>
      )}
    </details>
  )
}

// --- Journal (plain-language latest updates) ---

function JournalSection({ journal }: { journal: JournalEntry[] }) {
  const newestFirst = useMemo(() => [...journal].reverse(), [journal])
  const latest = newestFirst.slice(0, 5)
  const earlier = newestFirst.slice(5)

  if (newestFirst.length === 0) {
    return <p className="text-sm text-muted">No updates yet — Sparrow will explain its work here as it happens.</p>
  }

  const renderEntry = (entry: JournalEntry) => (
    <li key={entry.id} className="flex min-w-0 gap-3">
      <span className="flex w-3 shrink-0 justify-center pt-1.5">
        <span
          title={entry.agent}
          className={clsx('h-1.5 w-1.5 rounded-full', AGENT_DOT[entry.agent] || 'bg-white/30')}
        />
      </span>
      <div className="min-w-0 flex-1">
        <RelativeTime ts={entry.ts} className="mb-0.5 block text-[11px] text-muted/70" />
        <CollapsibleText text={entry.text} />
      </div>
    </li>
  )

  return (
    <>
      <ul className="space-y-3">{latest.map(renderEntry)}</ul>
      {earlier.length > 0 && (
        <details className="mt-3 border-t border-white/[0.06] pt-3">
          <summary className="cursor-pointer text-xs font-semibold text-muted hover:text-text">
            Earlier updates ({earlier.length})
          </summary>
          <ul className="mt-3 space-y-3">{earlier.map(renderEntry)}</ul>
        </details>
      )}
    </>
  )
}

// --- Progress tab ---

function ProgressTab({
  detail,
  show,
  now,
  busy,
  onNudge,
  onPause,
  onResume,
  onCancel,
}: {
  detail: JobDetail | null
  show: ShowDetail
  now: number
  busy: string | null
  onNudge: () => void
  onPause: () => void
  onResume: () => void
  onCancel: () => void
}) {
  if (!detail) {
    return (
      <div className="rounded-2xl border border-dashed border-white/[0.12] bg-panel/50 p-10 text-center">
        <Activity size={32} className="mx-auto text-muted/30" />
        <p className="mt-3 text-sm font-medium text-text">Nothing in progress</p>
        <p className="mt-1 text-sm text-muted">
          When you request something from the Watch tab, Sparrow's work shows up here.
        </p>
      </div>
    )
  }

  const { job, session } = detail
  const nowSeconds = Math.floor(now / 1000)
  const paused = job.status === 'paused'
  const running = session?.status === 'running'
  const terminal = job.status === 'complete' || job.status === 'abandoned'
  const wakeAt = job.next_wake_at || session?.wake_at || 0
  const secondsLeft = wakeAt > 0 ? wakeAt - nowSeconds : 0
  const spinner = <Loader2 size={13} className="animate-spin" />

  return (
    <div className="space-y-3">
      <Card className="p-5">
        <div className="flex flex-wrap items-center gap-3">
          <span className="relative flex h-2.5 w-2.5 shrink-0">
            {running && !paused && (
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-primary opacity-60" />
            )}
            <span className={clsx(
              'relative inline-flex h-2.5 w-2.5 rounded-full',
              paused ? 'bg-white/30' : running ? 'bg-primary' : 'bg-primary/60',
            )} />
          </span>
          <h2 className="text-sm font-semibold uppercase tracking-[0.18em] text-text/60">
            {job.status === 'complete'
              ? 'Sparrow finished this'
              : job.status === 'abandoned'
                ? 'Sparrow stopped this job'
                : "Sparrow's agent is working on this"}
          </h2>
          {paused && <Badge tone="neutral">Paused</Badge>}
        </div>

        <p className="mt-3 text-base font-medium text-text">
          {titleSummaryLine(detail, show, nowSeconds)}
        </p>
        {!paused && !terminal && (
          <p className="mt-1.5 inline-flex items-center gap-1.5 text-xs text-muted">
            <Clock size={12} />
            {running
              ? 'Working right now'
              : wakeAt > 0
                ? `Checking again in ${formatCountdown(secondsLeft)}`
                : 'Waiting for something to happen'}
          </p>
        )}

        {!terminal && (
          <>
            <div className="mt-4 flex flex-wrap items-center gap-2">
              <Button
                variant="secondary"
                size="sm"
                disabled={busy !== null || paused}
                onClick={onNudge}
                title="Ask the agent to check right now"
              >
                {busy === 'nudge' ? spinner : <RefreshCw size={13} />}
                Check now
              </Button>
              {paused ? (
                <Button variant="secondary" size="sm" disabled={busy !== null} onClick={onResume}>
                  {busy === 'resume' ? spinner : <Play size={13} />}
                  Resume
                </Button>
              ) : (
                <Button
                  variant="secondary"
                  size="sm"
                  disabled={busy !== null}
                  onClick={onPause}
                  title="Stops downloading. Nothing is deleted."
                >
                  {busy === 'pause' ? spinner : <Pause size={13} />}
                  Pause request
                </Button>
              )}
              <Button
                variant="danger"
                size="sm"
                disabled={busy !== null}
                onClick={onCancel}
                title="Stops unfinished downloads and removes their partial files. Episodes already in your library are kept."
              >
                {busy === 'cancel' ? spinner : <Trash2 size={13} />}
                Cancel pending work
              </Button>
            </div>
            <p className="mt-2 text-[11px] leading-relaxed text-muted/80">
              {paused
                ? 'Paused — nothing is downloading, and nothing was deleted.'
                : 'Pause request stops downloading; nothing is deleted. Cancel pending work removes unfinished downloads and their partial files — episodes already in your library are kept.'}
            </p>
          </>
        )}
      </Card>

      <Card className="p-5">
        <p className="text-xs font-semibold uppercase tracking-wide text-muted/70">Latest updates</p>
        <div className="mt-3">
          <JournalSection journal={detail.journal} />
        </div>
      </Card>

      <Card className="p-5">
        <p className="text-xs font-semibold uppercase tracking-wide text-muted/70">Transfers</p>
        <div className="mt-3">
          <TransfersSection downloads={detail.downloads} nowSeconds={nowSeconds} />
        </div>
        <AdvancedSection detail={detail} />
      </Card>
    </div>
  )
}

// --- Preferences tab ---

const MONITORING_CHOICES: Array<{ mode: MonitoringMode; label: string; hint: string }> = [
  {
    mode: 'exact',
    label: 'Exactly what I asked for',
    hint: 'Sparrow only gets what you explicitly requested — nothing more.',
  },
  {
    mode: 'keep_current',
    label: 'Keep current — grab new episodes as they air',
    hint: 'New episodes are picked up automatically; older seasons are left alone.',
  },
  {
    mode: 'seasons',
    label: 'Selected seasons',
    hint: 'Sparrow may fetch any episode from the seasons you tick below.',
  },
  {
    mode: 'backfill',
    label: 'Everything available (backfill)',
    hint: 'Sparrow may fetch every available episode, past and future.',
  },
]

function PreferencesTab({
  show,
  quality,
  onQualityChange,
  monitoringBusy,
  monitoringError,
  onMonitoring,
}: {
  show: ShowDetail
  quality: string
  onQualityChange: (value: string) => void
  monitoringBusy: boolean
  monitoringError: string
  onMonitoring: (mode: MonitoringMode, seasons: number[]) => void
}) {
  const isMovie = show.media_type === 'movie'
  const currentMode: MonitoringMode = show.mandate?.mode ?? 'exact'
  const currentSeasons = useMemo(() => show.mandate?.seasons ?? [], [show.mandate])

  const toggleSeason = (seasonNumber: number) => {
    const next = currentSeasons.includes(seasonNumber)
      ? currentSeasons.filter(n => n !== seasonNumber)
      : [...currentSeasons, seasonNumber].sort((a, b) => a - b)
    onMonitoring('seasons', next)
  }

  return (
    <div className="space-y-3">
      {!isMovie && (
        <Card className="p-5">
          <div className="flex items-center justify-between gap-3">
            <p className="text-xs font-semibold uppercase tracking-wide text-muted/70">Monitoring</p>
            {monitoringBusy && <Loader2 size={13} className="animate-spin text-muted" />}
          </div>
          <p className="mt-2 text-sm text-muted">
            This is your standing permission. Sparrow will never download outside it.
          </p>
          <div className="mt-4 space-y-2" role="radiogroup" aria-label="Monitoring scope">
            {MONITORING_CHOICES.map(choice => {
              const selected = currentMode === choice.mode
              return (
                <div key={choice.mode}>
                  <label
                    className={clsx(
                      'flex cursor-pointer items-start gap-3 rounded-xl border px-4 py-3 transition-colors',
                      selected
                        ? 'border-primary/40 bg-primary/10'
                        : 'border-white/10 bg-white/[0.03] hover:bg-white/[0.06]',
                    )}
                  >
                    <input
                      type="radio"
                      name="monitoring-mode"
                      className="mt-1 accent-current"
                      checked={selected}
                      disabled={monitoringBusy}
                      onChange={() => onMonitoring(
                        choice.mode,
                        choice.mode === 'seasons' ? currentSeasons : [],
                      )}
                    />
                    <span className="min-w-0">
                      <span className={clsx('block text-sm font-semibold', selected ? 'text-text' : 'text-text/85')}>
                        {choice.label}
                      </span>
                      <span className="mt-0.5 block text-xs text-muted">{choice.hint}</span>
                    </span>
                  </label>
                  {choice.mode === 'seasons' && selected && (
                    <div className="ml-7 mt-2 flex flex-wrap gap-1.5">
                      {show.seasons.map(season => {
                        const checked = currentSeasons.includes(season.season_number)
                        return (
                          <label
                            key={season.season_number}
                            className={clsx(
                              'inline-flex cursor-pointer items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-semibold transition-colors',
                              checked
                                ? 'border-primary/40 bg-primary/15 text-primary-light'
                                : 'border-white/10 bg-white/5 text-muted hover:text-text',
                            )}
                          >
                            <input
                              type="checkbox"
                              className="sr-only"
                              checked={checked}
                              disabled={monitoringBusy}
                              onChange={() => toggleSeason(season.season_number)}
                            />
                            {checked && <Check size={11} />}
                            {season.name || `Season ${season.season_number}`}
                          </label>
                        )
                      })}
                      {show.seasons.length === 0 && (
                        <p className="text-xs text-muted">No season details available yet.</p>
                      )}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
          {monitoringError && <p className="mt-3 text-sm text-rose-300">{monitoringError}</p>}
          {show.mandate && (
            <p className="mt-3 text-[11px] text-muted/70">
              Last changed <RelativeTime ts={show.mandate.updated_at || show.mandate.granted_at} />
            </p>
          )}
        </Card>
      )}

      <Card className="p-5">
        <p className="text-xs font-semibold uppercase tracking-wide text-muted/70">Quality</p>
        <p className="mt-2 text-sm text-muted">Applies to new requests you make from this page.</p>
        <div className="mt-3 flex flex-wrap gap-1.5">
          {QUALITY_OPTIONS.map(option => (
            <button
              key={option.value}
              type="button"
              onClick={() => onQualityChange(option.value)}
              className={clsx(
                'rounded-full border px-3 py-1.5 text-xs font-semibold transition-colors',
                quality === option.value
                  ? 'border-primary/40 bg-primary/15 text-primary-light'
                  : 'border-white/10 bg-white/5 text-muted hover:text-text',
              )}
            >
              {option.label}
            </button>
          ))}
        </div>
      </Card>
    </div>
  )
}

// --- Watch tab ---

function WatchTab({
  show,
  jobDetail,
  requesting,
  expandedSeasons,
  onToggleSeason,
  onRequestSeason,
  onOpenProgress,
}: {
  show: ShowDetail
  jobDetail: JobDetail | null
  requesting: string | null
  expandedSeasons: Set<number>
  onToggleSeason: (seasonNumber: number) => void
  onRequestSeason: (season: ShowSeason) => void
  onOpenProgress: () => void
}) {
  if (show.media_type === 'movie') {
    return (
      <Card className="p-6">
        <div className="flex flex-wrap items-center gap-3">
          <Film size={20} className="shrink-0 text-muted/60" />
          {show.in_library ? (
            <Badge tone="success" className="px-3 py-1.5 text-sm">
              <Check size={13} /> In your library — ready to watch
            </Badge>
          ) : jobDetail ? (
            <>
              <Badge tone="info" className="px-3 py-1.5 text-sm">
                <Sparkles size={13} /> On the way
              </Badge>
              <button
                type="button"
                className="text-xs font-semibold text-primary-light hover:underline"
                onClick={onOpenProgress}
              >
                See progress
              </button>
            </>
          ) : (
            <p className="text-sm text-muted">
              Not in your library yet — use “Get movie” above to request it.
            </p>
          )}
        </div>
      </Card>
    )
  }

  if (show.seasons.length === 0) {
    return (
      <div className="rounded-2xl border border-dashed border-white/[0.12] bg-panel/50 p-10 text-center">
        <Tv size={32} className="mx-auto text-muted/30" />
        <p className="mt-3 text-sm text-muted">No season details available for this show yet.</p>
      </div>
    )
  }

  return (
    <div className="space-y-3">
      {show.seasons.map(season => {
        const expanded = expandedSeasons.has(season.season_number)
        const complete = season.episode_count > 0 && season.have_count >= season.episode_count
        const partial = season.have_count > 0 && season.have_count < season.episode_count
        const key = `season-${season.season_number}`
        return (
          <Card key={season.season_number} className="overflow-hidden">
            <div className="flex flex-wrap items-center gap-3 p-4">
              <button
                type="button"
                className="flex min-w-0 flex-1 items-center gap-3 text-left"
                onClick={() => onToggleSeason(season.season_number)}
                aria-expanded={expanded}
              >
                <ChevronDown
                  size={16}
                  className={clsx('shrink-0 text-muted transition-transform', expanded && 'rotate-180')}
                />
                <span className="text-sm font-semibold text-text">
                  {season.name || `Season ${season.season_number}`}
                </span>
                <SeasonBadge season={season} />
              </button>
              {!complete && season.episode_count > 0 && !jobDetail && (
                <Button
                  variant="secondary"
                  size="sm"
                  className="ml-auto shrink-0"
                  disabled={requesting !== null}
                  onClick={() => onRequestSeason(season)}
                >
                  {requesting === key
                    ? <Loader2 size={13} className="animate-spin" />
                    : <Download size={13} />}
                  {partial ? 'Get missing episodes' : 'Get season'}
                </Button>
              )}
            </div>
            {expanded && (
              <div className="border-t border-white/[0.08] p-4">
                {season.episode_count === 0 ? (
                  <p className="text-xs text-muted">No episode details yet.</p>
                ) : (
                  <div className="flex flex-wrap gap-1.5">
                    {seasonEpisodes(season).map(episode => (
                      <EpisodePill key={episode.episode} episode={episode} />
                    ))}
                  </div>
                )}
              </div>
            )}
          </Card>
        )
      })}
    </div>
  )
}

// --- Page ---

const TABS: Array<{ id: TabId; label: string; icon: typeof Play }> = [
  { id: 'watch', label: 'Watch', icon: Play },
  { id: 'progress', label: 'Progress', icon: Activity },
  { id: 'preferences', label: 'Preferences', icon: SlidersHorizontal },
]

export default function Show() {
  const { tmdbId } = useParams()
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()

  const [show, setShow] = useState<ShowDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [overviewExpanded, setOverviewExpanded] = useState(false)
  const [expandedSeasons, setExpandedSeasons] = useState<Set<number>>(new Set())
  const [quality, setQuality] = useState('')
  const [requesting, setRequesting] = useState<string | null>(null) // 'movie' | 'show' | 'season-N'
  const [feedback, setFeedback] = useState('')
  const [requestError, setRequestError] = useState('')

  const [tab, setTab] = useState<TabId>('watch')
  const [jobDetail, setJobDetail] = useState<JobDetail | null>(null)
  const [jobBusy, setJobBusy] = useState<string | null>(null)
  const [monitoringBusy, setMonitoringBusy] = useState(false)
  const [monitoringError, setMonitoringError] = useState('')

  const showId = Number(tmdbId)
  const mediaType = searchParams.get('type') === 'movie' ? 'movie' : 'tv'

  const loadShow = useCallback(async (silent = false) => {
    if (!Number.isFinite(showId)) {
      setError('This page needs a valid show link.')
      setLoading(false)
      return
    }
    if (!silent) setLoading(true)
    try {
      setShow(await getShow(showId, mediaType))
      setError('')
    } catch (e: unknown) {
      if (!silent) setError(e instanceof Error ? e.message : "Couldn't load this title")
    } finally {
      if (!silent) setLoading(false)
    }
  }, [showId, mediaType])

  const loadJob = useCallback(async () => {
    if (!Number.isFinite(showId)) return
    try {
      const [active, paused, complete] = await Promise.allSettled([
        getJobs('active'), getJobs('paused'), getJobs('complete'),
      ])
      const jobs = [
        ...(active.status === 'fulfilled' ? active.value : []),
        ...(paused.status === 'fulfilled' ? paused.value : []),
        ...(complete.status === 'fulfilled' ? complete.value : []),
      ]
      const job = jobs.find(candidate =>
        candidate.tmdb_id === showId && candidate.media_type === mediaType)
      if (!job) {
        setJobDetail(null)
        return
      }
      setJobDetail(await getJob(job.id))
      setFeedback('')
    } catch {
      // keep whatever we have
    }
  }, [showId, mediaType])

  const refreshMandate = useCallback(async () => {
    if (!Number.isFinite(showId)) return
    try {
      const res = await getMandate(showId, mediaType)
      setShow(prev => (prev ? { ...prev, mandate: res.mandate, mandate_summary: res.summary } : prev))
    } catch {
      // the next full refresh will reconcile
    }
  }, [showId, mediaType])

  useEffect(() => { loadShow() }, [loadShow])
  useEffect(() => { loadJob() }, [loadJob])

  // Live tick for the next-wake countdown and staleness labels
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!jobDetail) return
    const timer = setInterval(() => setNow(Date.now()), 15000)
    return () => clearInterval(timer)
  }, [jobDetail?.job.id])

  // Throttled live refresh via websocket
  const refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  useEffect(() => () => {
    if (refreshTimer.current) clearTimeout(refreshTimer.current)
  }, [])
  const scheduleRefresh = useCallback(() => {
    if (refreshTimer.current) return
    refreshTimer.current = setTimeout(() => {
      refreshTimer.current = null
      loadShow(true)
      loadJob()
    }, 1200)
  }, [loadShow, loadJob])

  useWebSocket((event) => {
    const type = event.type as string
    if (type === 'journal') {
      const entry = event.data as JournalEntry
      setJobDetail(prev => (
        prev && prev.job.id === entry.job_id && !prev.journal.some(existing => existing.id === entry.id)
          ? { ...prev, journal: [...prev.journal, entry] }
          : prev
      ))
      return
    }
    if (type === 'job_added' || type === 'job_update') {
      const job = event.data as Job
      if (job.tmdb_id !== showId || job.media_type !== mediaType) return
      setJobDetail(prev => (prev && prev.job.id === job.id ? { ...prev, job } : prev))
      setNow(Date.now())
      scheduleRefresh()
      return
    }
    if (type === 'download_update') {
      const dl = event.data as Partial<DownloadItem> & { id?: string }
      const known = dl?.id && jobDetail?.downloads.some(existing => existing.id === dl.id)
      if (known) {
        // Live in-place update — no refetch needed for progress/speed/eta ticks.
        setJobDetail(prev => (
          prev
            ? {
                ...prev,
                downloads: prev.downloads.map(existing =>
                  existing.id === dl.id ? { ...existing, ...dl } : existing),
              }
            : prev
        ))
        setNow(Date.now())
        // Status transitions can change the inventory (e.g. organized) — reconcile quietly.
        if (dl.status && dl.status !== 'downloading') scheduleRefresh()
      } else {
        scheduleRefresh()
      }
      return
    }
    if (type === 'mandate_update') {
      const data = event.data as { tmdb_id?: number } | undefined
      if (!data || data.tmdb_id == null || data.tmdb_id === showId) refreshMandate()
      return
    }
    if (type === 'request_update' || type === 'library_update' || type === 'activity') {
      scheduleRefresh()
    }
  })

  const allComplete = useMemo(() => {
    if (!show) return false
    if (show.media_type === 'movie') return show.in_library
    if (show.seasons.length === 0) return false
    return show.seasons.every(
      season => season.episode_count > 0 && season.have_count >= season.episode_count,
    )
  }, [show])

  const request = async (key: string, wantedEpisodes?: Record<string, number[]>) => {
    if (!show) return
    setRequesting(key)
    setRequestError('')
    setFeedback('')
    try {
      const payload: CreateJobPayload = {
        tmdb_id: show.tmdb_id,
        media_type: show.media_type,
      }
      if (wantedEpisodes) payload.wanted_episodes = wantedEpisodes
      if (quality) payload.preferred_quality = quality
      await createJob(payload)
      setFeedback('Sparrow is on it')
      await Promise.all([loadShow(true), loadJob()])
    } catch (e: unknown) {
      if (e instanceof ApiError && e.status === 409) {
        setFeedback('Sparrow is already on it')
        await loadJob()
      } else {
        setRequestError('Something went wrong — please try again')
      }
    } finally {
      setRequesting(null)
    }
  }

  const requestSeason = (season: ShowSeason) => {
    const missing = seasonEpisodes(season)
      .filter(episode => !episode.have)
      .map(episode => episode.episode)
    const episodes = missing.length > 0
      ? missing
      : seasonEpisodes(season).map(episode => episode.episode)
    request(`season-${season.season_number}`, { [String(season.season_number)]: episodes })
  }

  const withJob = async (key: string, action: (id: string) => Promise<unknown>) => {
    if (!jobDetail) return
    setJobBusy(key)
    try {
      await action(jobDetail.job.id)
    } catch {
      // leave state as-is; next refresh will reconcile
    } finally {
      setJobBusy(null)
    }
  }

  const handleNudge = () => withJob('nudge', async (id) => {
    await nudgeJob(id)
    await loadJob()
  })
  const handlePause = () => withJob('pause', async (id) => {
    const job = await pauseJob(id)
    setJobDetail(prev => (prev ? { ...prev, job } : prev))
  })
  const handleResume = () => withJob('resume', async (id) => {
    const job = await resumeJob(id)
    setJobDetail(prev => (prev ? { ...prev, job } : prev))
  })
  const handleCancel = () => {
    if (!jobDetail) return
    if (!confirm(`Cancel pending work on "${jobDetail.job.title}"?\n\nStops unfinished downloads and removes their partial files. Episodes already in your library are kept.`)) return
    withJob('cancel', async (id) => {
      await cancelJob(id)
      setJobDetail(null)
      loadShow(true)
    })
  }

  const applyMonitoring = async (mode: MonitoringMode, seasons: number[]) => {
    if (!show) return
    setMonitoringBusy(true)
    setMonitoringError('')
    const previousMandate = show.mandate
    const previousSummary = show.mandate_summary
    const nowSeconds = Math.floor(Date.now() / 1000)
    const optimistic: Mandate = {
      tmdb_id: show.tmdb_id,
      media_type: show.media_type,
      mode,
      seasons,
      requested_episodes: previousMandate?.requested_episodes ?? {},
      granted_at: previousMandate?.granted_at ?? nowSeconds,
      created_at: previousMandate?.created_at ?? nowSeconds,
      updated_at: nowSeconds,
    }
    setShow(prev => (prev ? { ...prev, mandate: optimistic } : prev))
    try {
      const res = await setMonitoring(show.tmdb_id, mode, seasons, show.media_type)
      setShow(prev => (prev ? { ...prev, mandate: res.mandate, mandate_summary: res.summary } : prev))
    } catch {
      setShow(prev => (
        prev ? { ...prev, mandate: previousMandate, mandate_summary: previousSummary } : prev
      ))
      setMonitoringError("Couldn't update monitoring — please try again")
    } finally {
      setMonitoringBusy(false)
    }
  }

  const toggleSeason = (seasonNumber: number) => {
    setExpandedSeasons(prev => {
      const next = new Set(prev)
      if (next.has(seasonNumber)) next.delete(seasonNumber)
      else next.add(seasonNumber)
      return next
    })
  }

  const needsAttention = jobDetail?.downloads.some(dl => dl.status === 'error') ?? false

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-bg">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" />
      </div>
    )
  }

  if (error || !show) {
    return (
      <div className="mx-auto w-full max-w-3xl p-4 pt-24 sm:p-6 sm:pt-28">
        <Button variant="ghost" size="sm" onClick={() => navigate('/')}>
          <ArrowLeft size={14} /> Back
        </Button>
        <div className="mt-6 rounded-2xl border border-dashed border-white/[0.12] bg-panel/50 p-10 text-center">
          {mediaType === 'movie'
            ? <Film size={32} className="mx-auto text-muted/30" />
            : <Tv size={32} className="mx-auto text-muted/30" />}
          <p className="mt-3 text-sm font-medium text-text">Couldn't load this title</p>
          <p className="mt-1 text-sm text-muted">{error || 'Please try again in a moment.'}</p>
          <Button variant="secondary" size="sm" className="mt-4" onClick={() => loadShow()}>
            Try again
          </Button>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen w-full">
      {/* Header with backdrop */}
      <section className="relative overflow-hidden border-b border-white/[0.08]">
        {show.backdrop_url && (
          <>
            <div className="absolute inset-0 opacity-40">
              <img src={show.backdrop_url} alt="" className="h-full w-full object-cover" />
            </div>
            <div className="absolute inset-0 bg-[linear-gradient(180deg,rgba(5,5,7,0.55),rgba(5,5,7,0.85)_60%,rgba(5,5,7,1))]" />
          </>
        )}
        <div className="relative mx-auto w-full max-w-7xl p-4 pt-20 sm:p-6 sm:pt-24 lg:p-8 lg:pt-24">
          <Button variant="secondary" size="icon" aria-label="Back" onClick={() => navigate('/')}>
            <ArrowLeft size={15} />
          </Button>

          <div className="mt-6 flex flex-col gap-6 sm:flex-row">
            <div className="poster-surface aspect-[2/3] w-36 shrink-0 sm:w-44">
              {show.poster_url ? (
                <img src={show.poster_url} alt={show.title} className="h-full w-full object-cover" />
              ) : (
                <div className="flex h-full w-full flex-col items-center justify-center gap-2 bg-panel text-muted">
                  <span className="text-4xl font-semibold text-text/40">{show.title.charAt(0).toUpperCase()}</span>
                </div>
              )}
            </div>
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone="neutral">{show.media_type === 'movie' ? 'Movie' : 'Show'}</Badge>
                {show.year && <span className="text-sm text-muted">{show.year}</span>}
                {show.rating != null && show.rating > 0 && (
                  <span className="inline-flex items-center gap-1 text-sm text-amber-200">
                    <Star size={12} fill="currentColor" /> {show.rating.toFixed(1)}
                  </span>
                )}
              </div>
              <h1 className="mt-2 break-words text-3xl font-semibold leading-tight tracking-tight text-text sm:text-5xl">
                {show.title}
              </h1>
              {show.genres.length > 0 && (
                <p className="mt-2 text-sm text-muted">{show.genres.join(' · ')}</p>
              )}
              {show.overview && (
                <div className="mt-4 max-w-2xl">
                  <p className={clsx('text-sm leading-relaxed text-muted', !overviewExpanded && 'line-clamp-3')}>
                    {show.overview}
                  </p>
                  <button
                    type="button"
                    className="mt-1 text-xs font-semibold text-primary-light hover:underline"
                    onClick={() => setOverviewExpanded(prev => !prev)}
                  >
                    {overviewExpanded ? 'less' : 'more'}
                  </button>
                </div>
              )}

              {/* Primary action */}
              <div className="mt-6 flex flex-wrap items-center gap-4">
                {allComplete ? (
                  <Badge tone="success" className="px-4 py-2 text-sm">
                    <Check size={15} /> In your library — complete
                  </Badge>
                ) : jobDetail ? (
                  <button type="button" onClick={() => setTab('progress')} title="See progress">
                    <Badge tone="info" className="px-4 py-2 text-sm">
                      <Sparkles size={14} /> Sparrow is on it
                    </Badge>
                  </button>
                ) : (
                  <>
                    <Button
                      variant="primary"
                      disabled={requesting !== null}
                      onClick={() => request(show.media_type === 'movie' ? 'movie' : 'show')}
                    >
                      {requesting === (show.media_type === 'movie' ? 'movie' : 'show')
                        ? <Loader2 size={14} className="animate-spin" />
                        : <Download size={14} />}
                      {show.media_type === 'movie' ? 'Get movie' : 'Get the whole show'}
                    </Button>
                    <QualityDisclosure quality={quality} onChange={setQuality} />
                  </>
                )}
              </div>
              {feedback && !jobDetail && (
                <p className="mt-3 inline-flex items-center gap-2 rounded-full border border-emerald-400/20 bg-emerald-400/10 px-3 py-1.5 text-sm text-emerald-300">
                  <Sparkles size={13} /> {feedback}
                </p>
              )}
              {requestError && <p className="mt-3 text-sm text-rose-300">{requestError}</p>}

              {/* The user's contract — always visible, on every tab */}
              <ContractStatement summary={show.mandate_summary} />
            </div>
          </div>
        </div>
      </section>

      <main className="mx-auto w-full max-w-7xl p-4 sm:p-6 lg:p-8">
        {/* Tabs */}
        <div className="mb-4 inline-flex max-w-full items-center gap-1 rounded-full border border-white/[0.12] bg-white/[0.05] p-1 backdrop-blur-xl">
          {TABS.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              type="button"
              onClick={() => setTab(id)}
              aria-pressed={tab === id}
              className={clsx(
                'inline-flex items-center gap-1.5 rounded-full px-3.5 py-1.5 text-xs font-semibold transition-colors',
                tab === id
                  ? 'bg-white/[0.14] text-text'
                  : 'text-muted hover:text-text',
              )}
            >
              <Icon size={13} />
              {label}
              {id === 'progress' && needsAttention && (
                <span className="h-1.5 w-1.5 rounded-full bg-amber-300" title="Needs attention" />
              )}
            </button>
          ))}
        </div>

        {tab === 'watch' && (
          <WatchTab
            show={show}
            jobDetail={jobDetail}
            requesting={requesting}
            expandedSeasons={expandedSeasons}
            onToggleSeason={toggleSeason}
            onRequestSeason={requestSeason}
            onOpenProgress={() => setTab('progress')}
          />
        )}

        {tab === 'progress' && (
          <ProgressTab
            detail={jobDetail}
            show={show}
            now={now}
            busy={jobBusy}
            onNudge={handleNudge}
            onPause={handlePause}
            onResume={handleResume}
            onCancel={handleCancel}
          />
        )}

        {tab === 'preferences' && (
          <PreferencesTab
            show={show}
            quality={quality}
            onQualityChange={setQuality}
            monitoringBusy={monitoringBusy}
            monitoringError={monitoringError}
            onMonitoring={applyMonitoring}
          />
        )}
      </main>
    </div>
  )
}
