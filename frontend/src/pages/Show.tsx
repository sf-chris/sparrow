import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import {
  ArrowLeft, Check, ChevronDown, Clock, Download, Film, Loader2, Pause, Play,
  RefreshCw, Sparkles, Star, Trash2, Tv,
} from 'lucide-react'
import clsx from 'clsx'
import {
  ApiError, cancelJob, createJob, getJob, getJobs, getShow, nudgeJob, pauseJob, resumeJob,
} from '../api/client'
import type { CreateJobPayload } from '../api/client'
import type {
  ActivityEvent, Download as DownloadItem, ExecutionTraceEntry, Job, JobDetail, JournalEntry,
  ShowDetail, ShowEpisode, ShowSeason,
} from '../types'
import { useWebSocket } from '../hooks/useWebSocket'
import { Badge, Button, Card, Progress } from '../components/ui'

const QUALITY_OPTIONS = [
  { value: '', label: 'Best available' },
  { value: '2160p', label: '4K' },
  { value: '1080p', label: 'Full HD' },
  { value: '720p', label: 'HD' },
] as const

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

// --- Agent panel helpers ---

function formatCountdown(secondsLeft: number): string {
  if (secondsLeft <= 0) return 'any moment now'
  const minutes = Math.max(1, Math.floor(secondsLeft / 60))
  if (minutes < 60) return `${minutes}m`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h ${minutes % 60}m`
  const days = Math.floor(hours / 24)
  return `${days}d ${hours % 24}h`
}

function journalTime(ts: number): string {
  const date = new Date(ts * 1000)
  const time = date.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
  if (date.toDateString() === new Date().toDateString()) return time
  return `${date.toLocaleDateString([], { month: 'short', day: 'numeric' })}, ${time}`
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

/** Turn a release string into something a person would say — never show the raw name here. */
function humanizeDownloadName(name: string): string {
  let s = name.replace(/[._]/g, ' ').replace(/\[[^\]]*\]/g, ' ')
  const cut = s.search(/\b(2160p|1080p|720p|480p|WEB[- ]?DL|WEBRip|BluRay|BDRip|DVDRip|HDTV|x264|x265|HEVC|H ?26[45]|AAC|AC3|DDP?|REMUX|10bit|PROPER|REPACK)\b/i)
  if (cut > 0) s = s.slice(0, cut)
  s = s.replace(/\s+/g, ' ').trim().replace(/[-–—]+$/, '').trim()
  return s || 'Download'
}

function downloadStatusLine(dl: DownloadItem): string {
  switch (dl.status) {
    case 'queued': return 'Waiting to start'
    case 'downloading': {
      const pct = `${Math.round((dl.progress || 0) * 100)}%`
      if (dl.eta_seconds > 0) {
        const minutes = Math.max(1, Math.round(dl.eta_seconds / 60))
        const eta = minutes < 60
          ? `about ${minutes} min left`
          : `about ${Math.floor(minutes / 60)}h ${minutes % 60}m left`
        return `${pct} — ${eta}`
      }
      return pct
    }
    case 'seeding':
    case 'completed':
    case 'organized': return 'Done'
    case 'organizing': return 'Tidying up'
    case 'paused': return 'Paused'
    case 'error': return 'Hit a snag'
    default: return ''
  }
}

function TransferRow({ dl }: { dl: DownloadItem }) {
  const active = dl.status === 'downloading' || dl.status === 'queued'
  return (
    <li className="py-2.5">
      <div className="flex min-w-0 items-center justify-between gap-3">
        <p className="min-w-0 truncate text-sm text-text/90">{humanizeDownloadName(dl.name)}</p>
        <span className="shrink-0 text-xs text-muted">{downloadStatusLine(dl)}</span>
      </div>
      {active && <Progress value={(dl.progress || 0) * 100} className="mt-2" />}
      <details className="mt-1.5 text-[10px] text-muted/60">
        <summary className="cursor-pointer select-none hover:text-text">Details</summary>
        <p className="mt-1 break-all font-mono">{dl.name}</p>
      </details>
    </li>
  )
}

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
            <span className="shrink-0 text-[11px] text-muted/70">{journalTime(event.timestamp)}</span>
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

function AgentPanel({
  detail,
  now,
  busy,
  onNudge,
  onPause,
  onResume,
  onCancel,
}: {
  detail: JobDetail
  now: number
  busy: string | null
  onNudge: () => void
  onPause: () => void
  onResume: () => void
  onCancel: () => void
}) {
  const { job, journal, session, downloads } = detail
  const execution = detail.activity || []
  const trace = detail.trace || []
  const latestUpdate = journal[journal.length - 1]
  const earlierUpdates = journal.slice(0, -1).reverse()
  const paused = job.status === 'paused'
  const running = session?.status === 'running'

  const wakeAt = job.next_wake_at || session?.wake_at || 0
  const secondsLeft = wakeAt > 0 ? wakeAt - Math.floor(now / 1000) : 0

  const journalRef = useRef<HTMLDivElement | null>(null)
  useEffect(() => {
    const el = journalRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [journal.length])

  const terminal = job.status === 'complete' || job.status === 'abandoned'
  const activeTransfers = downloads.filter(dl =>
    ['queued', 'downloading', 'organizing', 'paused'].includes(dl.status))

  return (
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
        {!terminal && <div className="ml-auto flex shrink-0 items-center gap-1">
          <Button
            variant="secondary"
            size="sm"
            disabled={busy !== null || paused}
            onClick={onNudge}
            title="Ask the agent to check right now"
          >
            {busy === 'nudge' ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />}
            Check now
          </Button>
          {paused ? (
            <Button variant="ghost" size="icon" title="Resume" disabled={busy !== null} onClick={onResume}>
              <Play size={14} />
            </Button>
          ) : (
            <Button variant="ghost" size="icon" title="Pause" disabled={busy !== null} onClick={onPause}>
              <Pause size={14} />
            </Button>
          )}
          <Button variant="ghost" size="icon" title="Stop working on this" disabled={busy !== null} onClick={onCancel}>
            <Trash2 size={14} />
          </Button>
        </div>}
      </div>

      <p className="mt-3 text-sm text-text/90">{job.state_line || 'Working on it…'}</p>
      {!paused && !terminal && (
        <p className="mt-1 inline-flex items-center gap-1.5 text-xs text-muted">
          <Clock size={12} />
          {running
            ? 'Working right now'
            : wakeAt > 0
              ? `Checking again in ${formatCountdown(secondsLeft)}`
              : 'Waiting for something to happen'}
        </p>
      )}

      {activeTransfers.length > 0 && (
        <div className="mt-5">
          <p className="text-xs font-semibold uppercase tracking-wide text-muted/70">On the way</p>
          <ul className="mt-1 divide-y divide-white/[0.06]">
            {activeTransfers.map(dl => (
              <TransferRow key={dl.id} dl={dl} />
            ))}
          </ul>
        </div>
      )}

      <div className="mt-5">
        <p className="text-xs font-semibold uppercase tracking-wide text-muted/70">Latest update</p>
        {!latestUpdate ? (
          <p className="mt-2 text-sm text-muted">No update yet — the execution log will show work as it happens.</p>
        ) : (
          <div ref={journalRef} className="mt-2 flex min-w-0 gap-3">
            <span className="flex w-3 shrink-0 justify-center pt-1.5">
              <span
                title={latestUpdate.agent}
                className={clsx('h-1.5 w-1.5 rounded-full', AGENT_DOT[latestUpdate.agent] || 'bg-white/30')}
              />
            </span>
            <div className="min-w-0 flex-1">
              <span className="mb-0.5 block text-[11px] text-muted/70">{journalTime(latestUpdate.ts)}</span>
              <CollapsibleText text={latestUpdate.text} />
            </div>
          </div>
        )}
        {earlierUpdates.length > 0 && (
          <details className="mt-3 border-t border-white/[0.06] pt-3">
            <summary className="cursor-pointer text-xs font-semibold text-muted hover:text-text">
              Earlier updates ({earlierUpdates.length})
            </summary>
            <ul className="mt-3 space-y-3">
              {earlierUpdates.map(entry => (
                <li key={entry.id} className="flex min-w-0 gap-3">
                  <span className="w-14 shrink-0 text-[11px] text-muted/70">{journalTime(entry.ts)}</span>
                  <div className="min-w-0 flex-1"><CollapsibleText text={entry.text} /></div>
                </li>
              ))}
            </ul>
          </details>
        )}
      </div>

      <details className="mt-5 border-t border-white/[0.08] pt-4">
        <summary className="cursor-pointer text-xs font-semibold uppercase tracking-wide text-muted/70 hover:text-text">
          Execution log ({execution.length + trace.length})
        </summary>
        {execution.length === 0 && trace.length === 0 ? (
          <p className="mt-2 text-sm text-muted">Detailed steps will appear here on the next agent run.</p>
        ) : (
          <div className="mt-2 max-h-96 overflow-y-auto pr-1">
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
    </Card>
  )
}

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
  const [requesting, setRequesting] = useState<string | null>(null) // 'show' | 'season-N'
  const [feedback, setFeedback] = useState('')
  const [requestError, setRequestError] = useState('')

  const [jobDetail, setJobDetail] = useState<JobDetail | null>(null)
  const [jobBusy, setJobBusy] = useState<string | null>(null)

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

  useEffect(() => { loadShow() }, [loadShow])
  useEffect(() => { loadJob() }, [loadJob])

  // Live tick for the next-wake countdown
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
    if (type === 'request_update' || type === 'library_update' || type === 'download_update' || type === 'activity') {
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
    if (!confirm(`Stop working on "${jobDetail.job.title}"?\n\nAnything already in your library stays there.`)) return
    withJob('cancel', async (id) => {
      await cancelJob(id)
      setJobDetail(null)
      loadShow(true)
    })
  }

  const toggleSeason = (seasonNumber: number) => {
    setExpandedSeasons(prev => {
      const next = new Set(prev)
      if (next.has(seasonNumber)) next.delete(seasonNumber)
      else next.add(seasonNumber)
      return next
    })
  }

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
                  <Badge tone="info" className="px-4 py-2 text-sm">
                    <Sparkles size={14} /> Sparrow is on it
                  </Badge>
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
            </div>
          </div>
        </div>
      </section>

      <main className="mx-auto w-full max-w-7xl space-y-3 p-4 sm:p-6 lg:p-8">
        {/* Agent workspace */}
        {jobDetail && (
          <div className="pb-3">
            <AgentPanel
              detail={jobDetail}
              now={now}
              busy={jobBusy}
              onNudge={handleNudge}
              onPause={handlePause}
              onResume={handleResume}
              onCancel={handleCancel}
            />
          </div>
        )}

        {/* Seasons */}
        {show.media_type === 'movie' ? null : show.seasons.length === 0 ? (
          <div className="rounded-2xl border border-dashed border-white/[0.12] bg-panel/50 p-10 text-center">
            <Tv size={32} className="mx-auto text-muted/30" />
            <p className="mt-3 text-sm text-muted">No season details available for this show yet.</p>
          </div>
        ) : (
          show.seasons.map(season => {
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
                    onClick={() => toggleSeason(season.season_number)}
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
                      onClick={() => requestSeason(season)}
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
          })
        )}
      </main>
    </div>
  )
}
