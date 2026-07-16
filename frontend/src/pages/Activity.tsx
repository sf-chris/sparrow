import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  AlertTriangle, ArrowUp, BookOpen, Check, ChevronDown, ChevronRight, Clock,
  Download, Info, Loader2, Moon, Search, Sparkles, WalletCards,
} from 'lucide-react'
import clsx from 'clsx'
import { getActivity, getJobs, getJournal, getUsageLedger } from '../api/client'
import type { ActivityEvent, AgentSession, Job, JobStatus, JournalEntry, UsageLedger } from '../types'
import { useWebSocket } from '../hooks/useWebSocket'
import { Badge, Card, SectionHeader } from '../components/ui'

const JOURNAL_LIMIT = 50

function formatCost(cost: number): string {
  return `$${cost.toFixed(cost < 1 ? 4 : 2)}`
}

function formatTokens(tokens: number): string {
  if (tokens < 1000) return String(tokens)
  if (tokens < 1_000_000) return `${(tokens / 1000).toFixed(tokens < 10_000 ? 1 : 0)}K`
  return `${(tokens / 1_000_000).toFixed(2)}M`
}

function relativeTime(ts: number): string {
  if (!ts) return ''
  const ms = ts > 1e12 ? ts : ts * 1000
  const diff = Date.now() - ms
  const min = Math.floor(diff / 60000)
  if (min < 1) return 'just now'
  if (min < 60) return `${min} min ago`
  const hours = Math.floor(min / 60)
  if (hours < 24) return `${hours} h ago`
  const days = Math.floor(hours / 24)
  return days === 1 ? 'yesterday' : `${days} d ago`
}

function formatCountdown(secondsLeft: number): string {
  if (secondsLeft <= 0) return 'any moment'
  const minutes = Math.max(1, Math.floor(secondsLeft / 60))
  if (minutes < 60) return `${minutes}m`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h ${minutes % 60}m`
  const days = Math.floor(hours / 24)
  return `${days}d ${hours % 24}h`
}

const AGENT_LABEL: Record<string, string> = {
  fetch: 'Fetch agent',
  media: 'Media agent',
  librarian: 'Librarian',
}

const AGENT_DOT: Record<string, string> = {
  fetch: 'bg-sky-400',
  media: 'bg-violet-400',
  librarian: 'bg-emerald-400',
}

function KindIcon({ kind }: { kind: string }) {
  switch (kind) {
    case 'searching':
      return <Search size={14} />
    case 'found':
    case 'downloading':
      return <Download size={14} />
    case 'organized':
      return <Check size={14} />
    case 'upgraded':
      return <ArrowUp size={14} />
    case 'waiting':
      return <Clock size={14} />
    case 'error':
      return <AlertTriangle size={14} />
    default:
      return <Info size={14} />
  }
}

const DOT_COLOR: Record<ActivityEvent['level'], string> = {
  info: 'bg-white/30',
  success: 'bg-emerald-400',
  warning: 'bg-amber-300',
  error: 'bg-rose-400',
}

function SessionRow({ session, now }: { session: AgentSession; now: number }) {
  const running = session.status === 'running'
  const secondsLeft = session.wake_at > 0 ? session.wake_at - Math.floor(now / 1000) : 0
  return (
    <li className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 py-2.5">
      <span className="relative flex h-2 w-2 shrink-0">
        {running && (
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-primary opacity-60" />
        )}
        <span className={clsx(
          'relative inline-flex h-2 w-2 rounded-full',
          running ? 'bg-primary' : AGENT_DOT[session.agent] || 'bg-white/30',
        )} />
      </span>
      <span className="w-28 shrink-0 text-xs font-semibold text-text/80">
        {AGENT_LABEL[session.agent] || session.agent}
      </span>
      <span className="min-w-0 flex-1 truncate text-sm text-text/90">
        {session.job_title || '—'}
      </span>
      <span className="shrink-0 text-xs text-muted">
        {running ? (
          'working now'
        ) : session.status === 'hibernating' ? (
          <span className="inline-flex items-center gap-1">
            <Moon size={11} />
            {session.wake_at > 0 ? `wakes in ${formatCountdown(secondsLeft)}` : 'sleeping'}
          </span>
        ) : (
          'closed'
        )}
      </span>
      <span
        className="w-20 shrink-0 text-right text-xs tabular-nums text-muted"
        title="Recorded API token usage priced at the rate stored in Sparrow's cost ledger."
      >
        {formatCost(session.spend?.dollars ?? 0)}
      </span>
    </li>
  )
}

function JournalRow({
  entry,
  job,
}: {
  entry: JournalEntry
  job: { tmdb_id: number; title: string; media_type: 'tv' | 'movie' } | undefined
}) {
  const [open, setOpen] = useState(false)
  const long = entry.text.length > 280 || entry.text.includes('\n')
  return (
    <li className="flex min-w-0 gap-3 py-2.5">
      <span className="flex w-3 shrink-0 justify-center pt-1.5">
        <span
          title={AGENT_LABEL[entry.agent] || entry.agent}
          className={clsx('h-1.5 w-1.5 rounded-full', AGENT_DOT[entry.agent] || 'bg-white/30')}
        />
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex min-w-0 items-baseline justify-between gap-3">
          {job ? (
            <Link
              to={`/show/${job.tmdb_id}?type=${job.media_type}`}
              className="min-w-0 truncate text-xs font-semibold text-primary-light hover:underline"
            >
              {job.title}
            </Link>
          ) : (
            <span className="text-xs font-semibold text-muted">
              {AGENT_LABEL[entry.agent] || entry.agent}
            </span>
          )}
          <span className="shrink-0 whitespace-nowrap text-xs text-muted">{relativeTime(entry.ts)}</span>
        </div>
        <p className={clsx(
          'mt-0.5 whitespace-pre-line break-words text-sm leading-relaxed text-text/90',
          long && !open && 'line-clamp-3',
        )}>
          {entry.text}
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
      </div>
    </li>
  )
}

function EventRow({
  event,
  job,
}: {
  event: ActivityEvent
  job: { tmdb_id: number; title: string; media_type: 'tv' | 'movie' } | undefined
}) {
  const [open, setOpen] = useState(false)
  const hasDetail = Boolean(event.detail && event.detail.trim())

  return (
    <li className="flex min-w-0 gap-3 py-2.5">
      <span className="flex w-4 shrink-0 justify-center pt-1.5">
        <span className={clsx('h-2 w-2 rounded-full', DOT_COLOR[event.level] || DOT_COLOR.info)} />
      </span>
      <div className="min-w-0 flex-1">
        {job && (
          <Link
            to={`/show/${job.tmdb_id}?type=${job.media_type}`}
            className="mb-1 block min-w-0 truncate text-xs font-semibold text-primary-light hover:underline"
          >
            {job.title}
          </Link>
        )}
        <div className="flex min-w-0 items-start gap-2">
          <span className="mt-0.5 shrink-0 text-muted/60">
            <KindIcon kind={event.kind} />
          </span>
          <p className="min-w-0 flex-1 break-words text-sm text-text/90">{event.message}</p>
          <span
            className="shrink-0 cursor-help whitespace-nowrap pt-0.5 text-xs text-muted"
            title={new Date((event.timestamp > 1e12 ? event.timestamp : event.timestamp * 1000)).toLocaleString()}
          >
            {relativeTime(event.timestamp)}
          </span>
          {hasDetail && (
            <button
              type="button"
              className="shrink-0 pt-0.5 text-muted transition-colors hover:text-text"
              onClick={() => setOpen(prev => !prev)}
              title={open ? 'Hide details' : 'Show details'}
            >
              {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
            </button>
          )}
        </div>
        {hasDetail && open && (
          <pre className="mt-2 overflow-x-auto whitespace-pre-wrap break-words rounded-md border border-white/10 bg-black/25 p-2.5 font-mono text-[11px] leading-relaxed text-muted">
            {event.detail}
          </pre>
        )}
      </div>
    </li>
  )
}

function UsageCenter({ usage }: { usage: UsageLedger }) {
  return (
    <section className="min-w-0">
      <SectionHeader
        title="Usage & cost"
        icon={<WalletCards size={14} className="text-primary-light" />}
        meta={usage.totals.api_calls > 0
          ? `${usage.totals.api_calls} recorded API calls`
          : `${usage.totals.legacy_sessions} historical session aggregates`}
      />
      <Card className="overflow-hidden">
        <div className="grid grid-cols-2 gap-px bg-white/[0.06] sm:grid-cols-4">
          {[
            ['Total cost', formatCost(usage.totals.cost)],
            ['Input tokens', formatTokens(usage.totals.input_tokens)],
            ['Output tokens', formatTokens(usage.totals.output_tokens)],
            ['Models used', String(usage.models.length)],
          ].map(([label, value]) => (
            <div key={label} className="bg-panel/95 p-3">
              <p className="text-[10px] font-semibold uppercase tracking-wide text-muted/70">{label}</p>
              <p className="mt-1 text-lg font-semibold tabular-nums text-text">{value}</p>
            </div>
          ))}
        </div>
        <div className="p-4">
          <p className="text-xs leading-relaxed text-muted">
            Cost is calculated from the exact tokens returned by the API and the USD list rate recorded for each call. Account credits, taxes, or negotiated provider discounts are outside Sparrow's ledger.
          </p>
          <div className="mt-4 overflow-x-auto">
            <table className="w-full min-w-[620px] text-left text-xs">
              <thead className="text-[10px] uppercase tracking-wide text-muted/60">
                <tr>
                  <th className="pb-2 font-semibold">Model</th>
                  <th className="pb-2 text-right font-semibold">Sessions</th>
                  <th className="pb-2 text-right font-semibold">Calls</th>
                  <th className="pb-2 text-right font-semibold">Input</th>
                  <th className="pb-2 text-right font-semibold">Output</th>
                  <th className="pb-2 text-right font-semibold">Cost</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/[0.06]">
                {usage.models.map(model => (
                  <tr key={model.model}>
                    <td className="py-2.5 font-mono text-[11px] text-text/90">{model.model}</td>
                    <td className="py-2.5 text-right tabular-nums text-muted">{model.sessions}</td>
                    <td className="py-2.5 text-right tabular-nums text-muted">{model.api_calls}</td>
                    <td className="py-2.5 text-right tabular-nums text-muted">{formatTokens(model.input_tokens)}</td>
                    <td className="py-2.5 text-right tabular-nums text-muted">{formatTokens(model.output_tokens)}</td>
                    <td className="py-2.5 text-right font-semibold tabular-nums text-text">{formatCost(model.cost)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <details className="mt-3 border-t border-white/[0.08] pt-3">
            <summary className="cursor-pointer text-xs font-semibold text-muted hover:text-text">
              Session ledger ({usage.sessions.length})
            </summary>
            <ul className="mt-2 divide-y divide-white/[0.06]">
              {usage.sessions.map(session => (
                <li key={session.id} className="py-2.5 text-xs">
                  <details>
                    <summary className="grid cursor-pointer list-none gap-1 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto_auto] sm:items-center sm:gap-3">
                      <span className="min-w-0 truncate font-medium text-text/90">
                        {session.job_title || AGENT_LABEL[session.agent] || session.agent}
                      </span>
                      <span className="min-w-0 truncate font-mono text-[10px] text-muted">{session.model}</span>
                      <span className="tabular-nums text-muted">
                        {formatTokens(session.spend.input_tokens)} in · {formatTokens(session.spend.output_tokens)} out
                      </span>
                      <span className="text-right font-semibold tabular-nums text-text">{formatCost(session.spend.dollars)}</span>
                    </summary>
                    <div className="mt-2 space-y-1 rounded-lg border border-white/[0.07] bg-black/20 p-2">
                      {session.spend.entries.map((entry, index) => (
                        <div key={`${entry.ts}-${index}`} className="grid gap-1 rounded px-1 py-1.5 text-[10px] text-muted sm:grid-cols-[7rem_minmax(0,1fr)_auto_auto] sm:gap-3">
                          <span>{entry.legacy_aggregate ? 'Historical aggregate' : new Date(entry.ts * 1000).toLocaleString()}</span>
                          <span className="font-mono">{entry.model}</span>
                          <span className="tabular-nums">{formatTokens(entry.input_tokens)} in · {formatTokens(entry.output_tokens)} out</span>
                          <span className="text-right font-semibold tabular-nums text-text/90">{formatCost(entry.cost)}</span>
                        </div>
                      ))}
                    </div>
                  </details>
                </li>
              ))}
            </ul>
          </details>
        </div>
      </Card>
    </section>
  )
}

export default function Activity() {
  const [sessions, setSessions] = useState<AgentSession[]>([])
  const [journal, setJournal] = useState<JournalEntry[]>([])
  const [jobMap, setJobMap] = useState<Map<
    string,
    { tmdb_id: number; title: string; media_type: 'tv' | 'movie' }
  >>(new Map())
  const [events, setEvents] = useState<ActivityEvent[]>([])
  const [usage, setUsage] = useState<UsageLedger | null>(null)
  const [loading, setLoading] = useState(true)
  const [eventsOpen, setEventsOpen] = useState(true)

  const load = useCallback(async () => {
    const statuses: JobStatus[] = ['active', 'paused', 'complete', 'abandoned']
    const [usageRes, journalRes, activityRes, ...jobsRes] = await Promise.allSettled([
      getUsageLedger(),
      getJournal('', JOURNAL_LIMIT),
      getActivity(200),
      ...statuses.map(status => getJobs(status)),
    ])
    if (usageRes.status === 'fulfilled') {
      const ledger = usageRes.value as UsageLedger
      setUsage(ledger)
      setSessions(ledger.sessions)
    }
    if (journalRes.status === 'fulfilled') setJournal(journalRes.value as JournalEntry[])
    if (activityRes.status === 'fulfilled') setEvents(activityRes.value as ActivityEvent[])
    const map = new Map<
      string,
      { tmdb_id: number; title: string; media_type: 'tv' | 'movie' }
    >()
    for (const res of jobsRes) {
      if (res.status !== 'fulfilled') continue
      for (const job of res.value as Job[]) {
        map.set(job.id, {
          tmdb_id: job.tmdb_id,
          title: job.title,
          media_type: job.media_type,
        })
      }
    }
    if (map.size > 0) setJobMap(map)
    setLoading(false)
  }, [])

  useEffect(() => { load() }, [load])

  // Live tick for wake countdowns
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 15000)
    return () => clearInterval(timer)
  }, [])

  // Throttled refetch (~1s) for websocket bursts
  const refetchTimer = useRef<number | null>(null)
  const scheduleRefetch = useCallback(() => {
    if (refetchTimer.current !== null) return
    refetchTimer.current = window.setTimeout(() => {
      refetchTimer.current = null
      load()
    }, 1000)
  }, [load])

  useEffect(() => () => {
    if (refetchTimer.current !== null) window.clearTimeout(refetchTimer.current)
  }, [])

  useWebSocket((event) => {
    const type = event.type as string
    if (type === 'journal') {
      const entry = event.data as JournalEntry
      setJournal(prev => (
        prev.some(existing => existing.id === entry.id)
          ? prev
          : [...prev, entry].slice(-JOURNAL_LIMIT)
      ))
      return
    }
    if (type === 'session_update') {
      const update = event.data as Partial<AgentSession> & { id: string }
      setSessions(prev => {
        const known = prev.some(session => session.id === update.id)
        if (!known) {
          // A session we haven't seen (or one that reopened) — pick it up on the next refetch.
          scheduleRefetch()
          return prev
        }
        return prev
          .map(session => (session.id === update.id ? { ...session, ...update } : session))
          .filter(session => session.status !== 'closed')
      })
      setNow(Date.now())
      return
    }
    if (type === 'job_added' || type === 'job_update') {
      const job = event.data as Job
      setJobMap(prev => {
        const next = new Map(prev)
        next.set(job.id, {
          tmdb_id: job.tmdb_id,
          title: job.title,
          media_type: job.media_type,
        })
        return next
      })
      return
    }
    if (type === 'activity') {
      scheduleRefetch()
    }
  })

  // Console feed reads newest-first
  const journalNewestFirst = useMemo(() => [...journal].reverse(), [journal])
  const openSessions = useMemo(
    () => sessions.filter(session => session.status !== 'closed'),
    [sessions],
  )

  return (
    <div className="min-h-screen w-full min-w-0 max-w-full overflow-x-hidden bg-bg text-text">
      <section className="border-b border-white/[0.08] bg-surface/55 px-4 pb-5 pt-20 backdrop-blur-xl sm:px-6">
        <div className="w-full max-w-4xl">
          <div className="mb-2 inline-flex items-center gap-2 rounded-full border border-white/10 bg-white/[0.05] px-3 py-1 text-xs font-medium text-muted">
            <Sparkles size={13} />
            Activity
          </div>
          <h1 className="text-2xl font-semibold tracking-tight text-text sm:text-3xl">What Sparrow is doing</h1>
          <p className="mt-1 max-w-2xl text-sm leading-relaxed text-muted">
            Concise progress up top; every technical step is available in the execution log.
          </p>
        </div>
      </section>

      <div className="w-full min-w-0 max-w-4xl space-y-8 p-4 sm:p-6">
        {loading ? (
          <div className="flex items-center gap-2 text-sm text-muted">
            <Loader2 size={14} className="animate-spin" /> Loading...
          </div>
        ) : (
          <>
            <section className="min-w-0">
              <SectionHeader
                title="Agents"
                meta={openSessions.length ? `${openSessions.length} open` : undefined}
              />
              {openSessions.length === 0 ? (
                <Card className="p-6 text-center">
                  <Check size={22} className="mx-auto text-emerald-300/70" />
                  <p className="mt-2 text-sm font-medium text-text">All quiet</p>
                  <p className="mt-1 text-sm text-muted">No agents are working right now.</p>
                </Card>
              ) : (
                <Card className="px-4 py-1.5">
                  <ul className="divide-y divide-white/[0.06]">
                    {openSessions.map(session => (
                      <SessionRow key={session.id} session={session} now={now} />
                    ))}
                  </ul>
                </Card>
              )}
            </section>

            {usage && <UsageCenter usage={usage} />}

            <section className="min-w-0">
              <SectionHeader
                title="Progress updates"
                icon={<BookOpen size={14} className="text-primary-light" />}
                meta={journal.length ? `latest ${journal.length}` : undefined}
              />
              {journalNewestFirst.length === 0 ? (
                <Card className="p-6 text-center">
                  <Clock size={22} className="mx-auto text-muted/40" />
                  <p className="mt-2 text-sm text-muted">No user-facing progress updates yet.</p>
                </Card>
              ) : (
                <Card className="px-4 py-1.5">
                  <ul className="divide-y divide-white/[0.06]">
                    {journalNewestFirst.map(entry => (
                      <JournalRow key={entry.id} entry={entry} job={jobMap.get(entry.job_id)} />
                    ))}
                  </ul>
                </Card>
              )}
            </section>

            <section className="min-w-0">
              <button
                type="button"
                className="mb-3 flex items-center gap-2 text-sm font-semibold uppercase tracking-[0.18em] text-text/60 transition-colors hover:text-text"
                onClick={() => setEventsOpen(prev => !prev)}
              >
                {eventsOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                Execution log
                <span className="text-xs font-normal normal-case tracking-normal text-muted">({events.length})</span>
              </button>
              {eventsOpen && (
                events.length === 0 ? (
                  <Card className="p-6 text-center">
                    <Clock size={22} className="mx-auto text-muted/40" />
                    <p className="mt-2 text-sm text-muted">Detailed agent steps will appear on the next run.</p>
                  </Card>
                ) : (
                  <Card className="px-4 py-1.5">
                    <ul className="divide-y divide-white/[0.06]">
                      {events.map(event => (
                        <EventRow key={event.id} event={event} job={jobMap.get(event.request_id)} />
                      ))}
                    </ul>
                  </Card>
                )
              )}
            </section>
          </>
        )}
      </div>
    </div>
  )
}
