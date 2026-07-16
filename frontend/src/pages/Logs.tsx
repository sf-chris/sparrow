import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Activity, AlertTriangle, CheckCircle2, ChevronDown, ChevronUp, Download,
  FileText, Info, Loader2, Radio, RefreshCw, Search, ShieldAlert, SlidersHorizontal,
  Sparkles, Trash2, X,
} from 'lucide-react'
import clsx from 'clsx'
import { getLogs } from '../api/client'
import { Badge, Button, Card, SectionHeader } from '../components/ui'

interface LogEntry {
  ts: number
  level: string
  logger: string
  msg: string
  [key: string]: unknown
}

const LEVELS = ['ALL', 'INFO', 'WARNING', 'ERROR'] as const
type Level = typeof LEVELS[number]

const COMPONENT_LABELS: Record<string, string> = {
  'sparrow.downloads': 'Downloads',
  'sparrow.organize': 'Organizer',
  'sparrow.artwork': 'Artwork',
  'sparrow.seeding': 'Seeding',
  'sparrow.search': 'Search',
  'sparrow.suggest': 'Suggestions',
  'sparrow': 'System',
}

function componentLabel(logger: string): string {
  return COMPONENT_LABELS[logger] || logger.replace('sparrow.', '').replace(/\b\w/g, c => c.toUpperCase())
}

function levelTone(level: string): 'neutral' | 'success' | 'warning' | 'danger' | 'info' {
  if (['ERROR', 'CRITICAL'].includes(level)) return 'danger'
  if (level === 'WARNING') return 'warning'
  if (level === 'INFO') return 'info'
  return 'neutral'
}

function levelIcon(level: string) {
  if (['ERROR', 'CRITICAL'].includes(level)) return <ShieldAlert size={14} />
  if (level === 'WARNING') return <AlertTriangle size={14} />
  if (level === 'INFO') return <Info size={14} />
  return <Activity size={14} />
}

function componentIcon(logger: string) {
  if (logger.includes('download')) return <Download size={13} />
  if (logger.includes('organize')) return <Sparkles size={13} />
  if (logger.includes('search') || logger.includes('suggest')) return <Search size={13} />
  if (logger.includes('seeding')) return <Radio size={13} />
  return <FileText size={13} />
}

function formatTime(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

function formatDate(ts: number): string {
  return new Date(ts * 1000).toLocaleDateString([], { month: 'short', day: 'numeric' })
}

function timeAgo(ts: number): string {
  const secs = Math.max(0, Math.floor(Date.now() / 1000 - ts))
  if (secs < 60) return 'just now'
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`
  return `${Math.floor(secs / 86400)}d ago`
}

function entryMatchesSearch(entry: LogEntry, query: string): boolean {
  if (!query.trim()) return true
  const haystack = Object.entries(entry)
    .map(([, value]) => String(value))
    .join(' ')
    .toLowerCase()
  return haystack.includes(query.trim().toLowerCase())
}

function extrasFor(entry: LogEntry): Record<string, unknown> {
  const skip = new Set(['ts', 'level', 'logger', 'msg', 'exc'])
  return Object.fromEntries(Object.entries(entry).filter(([key]) => !skip.has(key)))
}

function Stat({
  label,
  value,
  detail,
  tone = 'neutral',
}: {
  label: string
  value: string
  detail: string
  tone?: 'neutral' | 'warning' | 'danger'
}) {
  return (
    <Card className="p-4">
      <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-muted">{label}</p>
      <div className="mt-2 flex items-end justify-between gap-2">
        <span className="text-xl font-semibold tracking-tight text-text sm:text-2xl">{value}</span>
        <span className={clsx(
          'hidden rounded-md px-2 py-1 text-[11px] sm:inline-flex',
          tone === 'danger' && 'bg-rose-400/10 text-rose-300',
          tone === 'warning' && 'bg-amber-300/[0.08] text-amber-200',
          tone === 'neutral' && 'bg-white/5 text-muted',
        )}>{detail}</span>
      </div>
      <p className="mt-0.5 truncate text-xs text-muted sm:hidden">{detail}</p>
    </Card>
  )
}

function LogCard({
  entry,
  expanded,
  onToggle,
}: {
  entry: LogEntry
  expanded: boolean
  onToggle: () => void
}) {
  const extras = extrasFor(entry)
  const hasDetails = Object.keys(extras).length > 0 || typeof entry.exc === 'string'
  const preview = Object.entries(extras).slice(0, 3)

  return (
    <Card className={clsx(
      'overflow-hidden',
      ['ERROR', 'CRITICAL'].includes(entry.level) && 'border-rose-400/20',
      entry.level === 'WARNING' && 'border-amber-300/20',
    )}>
      <button
        type="button"
        className={clsx('w-full p-4 text-left', hasDetails && 'transition-colors hover:bg-white/[0.03]')}
        onClick={hasDetails ? onToggle : undefined}
      >
        <div className="flex items-start gap-3">
          <div className={clsx(
            'mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-md',
            ['ERROR', 'CRITICAL'].includes(entry.level) && 'bg-rose-400/10 text-rose-300',
            entry.level === 'WARNING' && 'bg-amber-300/[0.08] text-amber-200',
            entry.level === 'INFO' && 'bg-sky-400/10 text-sky-200',
            !['ERROR', 'CRITICAL', 'WARNING', 'INFO'].includes(entry.level) && 'bg-white/5 text-muted',
          )}>
            {levelIcon(entry.level)}
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone={levelTone(entry.level)}>{entry.level}</Badge>
              <span className="inline-flex items-center gap-1 rounded-md border border-white/10 bg-white/5 px-2 py-0.5 text-[11px] font-medium text-muted">
                {componentIcon(entry.logger)}
                {componentLabel(entry.logger)}
              </span>
              <span className="text-xs text-muted">{formatTime(entry.ts)}</span>
              <span className="text-xs text-muted/60">{timeAgo(entry.ts)}</span>
            </div>
            <p className="mt-2 break-words text-sm font-medium leading-relaxed text-text">{entry.msg}</p>
            {preview.length > 0 && (
              <div className="mt-2 flex flex-wrap gap-1.5">
                {preview.map(([key, value]) => (
                  <span key={key} className="max-w-full truncate rounded-md bg-bg/60 px-2 py-1 font-mono text-[11px] text-muted">
                    {key}={String(value)}
                  </span>
                ))}
              </div>
            )}
          </div>
          {hasDetails && (
            <span className="mt-1 shrink-0 text-muted">{expanded ? <ChevronUp size={16} /> : <ChevronDown size={16} />}</span>
          )}
        </div>
      </button>

      {expanded && (
        <div className="border-t border-white/10 bg-bg/60 px-4 py-3">
          {typeof entry.exc === 'string' && (
            <pre className="mb-3 overflow-x-auto whitespace-pre-wrap rounded-md border border-rose-400/20 bg-rose-400/10 p-3 text-[11px] leading-relaxed text-rose-200">
              {entry.exc}
            </pre>
          )}
          {Object.keys(extras).length > 0 && (
            <pre className="overflow-x-auto whitespace-pre-wrap rounded-md bg-bg p-3 text-[11px] leading-relaxed text-muted">
              {JSON.stringify(extras, null, 2)}
            </pre>
          )}
        </div>
      )}
    </Card>
  )
}

export default function Logs() {
  const [entries, setEntries] = useState<LogEntry[]>([])
  const [level, setLevel] = useState<Level>('ALL')
  const [component, setComponent] = useState('')
  const [search, setSearch] = useState('')
  const [loading, setLoading] = useState(false)
  const [tailing, setTailing] = useState(false)
  const [expanded, setExpanded] = useState<Set<number>>(new Set())
  const esRef = useRef<EventSource | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const data = await getLogs(500, level === 'ALL' ? '' : level)
      setEntries((data as LogEntry[]).reverse())
    } finally {
      setLoading(false)
    }
  }, [level])

  useEffect(() => { load() }, [load])
  useEffect(() => () => esRef.current?.close(), [])

  const startTail = useCallback(() => {
    esRef.current?.close()
    const es = new EventSource('/api/logs/stream')
    esRef.current = es
    es.onmessage = event => {
      try {
        const entry: LogEntry = JSON.parse(event.data)
        if (level !== 'ALL' && entry.level !== level) return
        setEntries(prev => [...prev, entry])
      } catch {}
    }
    es.onerror = () => {
      es.close()
      setTailing(false)
    }
    setTailing(true)
  }, [level])

  const stopTail = useCallback(() => {
    esRef.current?.close()
    esRef.current = null
    setTailing(false)
  }, [])

  const components = useMemo(() => {
    return Array.from(new Set(entries.map(entry => entry.logger))).sort()
  }, [entries])

  const visible = useMemo(() => {
    return entries.filter(entry => {
      if (component && entry.logger !== component) return false
      return entryMatchesSearch(entry, search)
    })
  }, [entries, component, search])

  const counts = useMemo(() => {
    return entries.reduce((acc, entry) => {
      acc.total += 1
      if (['ERROR', 'CRITICAL'].includes(entry.level)) acc.errors += 1
      if (entry.level === 'WARNING') acc.warnings += 1
      if (entry.level === 'INFO') acc.info += 1
      return acc
    }, { total: 0, errors: 0, warnings: 0, info: 0 })
  }, [entries])

  const latest = entries[entries.length - 1]
  let lastDate = ''

  return (
    <div className="cinema-page">
      <section className="cinema-hero min-h-[420px] border-b border-white/[0.08]">
        <div className="absolute inset-0 bg-[radial-gradient(circle_at_18%_8%,rgba(244,193,93,0.14),transparent_24rem),radial-gradient(circle_at_76%_16%,rgba(194,90,46,0.16),transparent_28rem),linear-gradient(180deg,rgba(5,5,7,0.62),rgba(5,5,7,1))]" />
        <div className="cinema-shell">
          <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-start">
            <div className="min-w-0">
              <Badge tone="info" className="border-primary/25 bg-primary/10 text-primary-light">
                <FileText size={12} /> Activity audit
              </Badge>
              <h1 className="cinema-title">Behind the scenes.</h1>
              <p className="cinema-copy">
                A readable studio log of searches, downloads, organization, artwork, and health behavior when you need to inspect the machinery.
              </p>
            </div>
            <div className="flex flex-wrap gap-2">
              <Button variant={tailing ? 'primary' : 'secondary'} size="sm" onClick={tailing ? stopTail : startTail}>
                <Radio size={13} className={tailing ? 'animate-pulse' : ''} />
                {tailing ? 'Stop live' : 'Live tail'}
              </Button>
              <Button variant="secondary" size="sm" onClick={load} disabled={loading}>
                {loading ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />}
                Refresh
              </Button>
            </div>
          </div>

          <div className="mt-5 grid grid-cols-2 gap-2 sm:grid-cols-4 sm:gap-3">
            <Stat label="Entries" value={String(counts.total)} detail={latest ? `latest ${timeAgo(latest.ts)}` : 'none yet'} />
            <Stat label="Info" value={String(counts.info)} detail="normal events" />
            <Stat label="Warnings" value={String(counts.warnings)} detail="watch list" tone={counts.warnings ? 'warning' : 'neutral'} />
            <Stat label="Errors" value={String(counts.errors)} detail="needs review" tone={counts.errors ? 'danger' : 'neutral'} />
          </div>
        </div>
      </section>

      <main className="max-w-7xl space-y-7 overflow-hidden p-4 sm:p-8">
        <Card className="p-4">
          <div className="flex flex-col gap-3 lg:flex-row lg:items-center">
            <div className="flex flex-wrap gap-1 rounded-full border border-white/10 bg-black/25 p-1">
              {LEVELS.map(option => (
                <button
                  key={option}
                  type="button"
                  onClick={() => setLevel(option)}
                  className={clsx(
                    'h-8 rounded-full px-3 text-xs font-semibold transition-colors',
                    level === option ? 'bg-primary text-bg' : 'text-muted hover:bg-white/[0.06] hover:text-text',
                  )}
                >
                  {option}
                </button>
              ))}
            </div>

            <div className="relative min-w-0 flex-1">
              <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
              <input
                className="input h-10 pl-9 pr-9"
                value={search}
                onChange={event => setSearch(event.target.value)}
                placeholder="Search messages, hashes, titles, paths"
              />
              {search && (
                <button
                  type="button"
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-muted hover:text-text"
                  onClick={() => setSearch('')}
                >
                  <X size={13} />
                </button>
              )}
            </div>

            <div className="flex items-center gap-2 text-xs text-muted">
              <SlidersHorizontal size={13} />
              {visible.length !== entries.length ? `${visible.length}/${entries.length}` : `${entries.length}`} shown
            </div>
          </div>

          {components.length > 0 && (
            <div className="mt-3 flex flex-wrap gap-1.5">
              <button
                type="button"
                onClick={() => setComponent('')}
                className={clsx(
                  'rounded-md border px-2.5 py-1 text-xs transition-colors',
                  component === '' ? 'border-primary/30 bg-primary/10 text-primary-light' : 'border-white/10 bg-white/5 text-muted hover:text-text',
                )}
              >
                all components
              </button>
              {components.map(logger => {
                const active = component === logger
                return (
                  <button
                    key={logger}
                    type="button"
                    onClick={() => setComponent(active ? '' : logger)}
                    className={clsx(
                      'inline-flex items-center gap-1 rounded-md border px-2.5 py-1 text-xs transition-colors',
                      active ? 'border-primary/30 bg-primary/10 text-primary-light' : 'border-white/10 bg-white/5 text-muted hover:text-text',
                    )}
                  >
                    {componentIcon(logger)}
                    {componentLabel(logger)}
                  </button>
                )
              })}
            </div>
          )}
        </Card>

        <section>
          <SectionHeader title="Activity reel" meta={`${visible.length} entries`} icon={<Activity size={15} className="text-primary-light" />} />
          {visible.length === 0 ? (
            <div className="glass-panel p-10 text-center">
              <FileText size={36} className="mx-auto text-muted/30" />
              <p className="mt-3 text-sm font-medium text-text">{loading ? 'Loading logs...' : 'No matching activity'}</p>
              <p className="mt-1 text-sm text-muted">Try clearing filters or refreshing the activity feed.</p>
            </div>
          ) : (
            <div className="space-y-3">
              {visible.map((entry, index) => {
                const date = formatDate(entry.ts)
                const showDate = date !== lastDate
                lastDate = date
                return (
                  <div key={`${entry.ts}-${index}`}>
                    {showDate && (
                      <div className="mb-2 flex items-center gap-2">
                        <span className="text-xs font-semibold uppercase tracking-wider text-muted">{date}</span>
                        <span className="h-px flex-1 bg-white/10" />
                      </div>
                    )}
                    <LogCard
                      entry={entry}
                      expanded={expanded.has(index)}
                      onToggle={() => {
                        setExpanded(prev => {
                          const next = new Set(prev)
                          next.has(index) ? next.delete(index) : next.add(index)
                          return next
                        })
                      }}
                    />
                  </div>
                )
              })}
            </div>
          )}
        </section>
      </main>
    </div>
  )
}
