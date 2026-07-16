import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  ArrowRight, CheckCircle2, Copy, DownloadCloud, ExternalLink, Film, FolderOpen, HardDrive,
  ImageOff, Info, Layers, Loader2, RefreshCw, ScanLine, Search, Sparkles, Star, Trash2, Tv, X,
} from 'lucide-react'
import clsx from 'clsx'
import { deleteLibraryItem, getLibrary, getLibraryView, scanLibrary, TMDB_POSTER_BASE } from '../api/client'
import type { LibraryEntryState, LibraryItem, LibraryViewEntry, MediaType } from '../types'
import { useWebSocket } from '../hooks/useWebSocket'
import { Badge, Button, Card, Progress, RelativeTime, SectionHeader } from '../components/ui'

type StateFilter = 'all' | 'ready' | 'in_progress' | 'attention'
type MediaFilter = 'all' | 'movie' | 'tv'

const IN_PROGRESS_STATES: LibraryEntryState[] = ['requested', 'queued', 'downloading', 'verifying']

const STATE_FILTERS: Array<{ id: StateFilter; label: string }> = [
  { id: 'all', label: 'All' },
  { id: 'ready', label: 'Ready' },
  { id: 'in_progress', label: 'In progress' },
  { id: 'attention', label: 'Needs attention' },
]

/** Websocket events that mean the library projection may have changed. */
const REFRESH_EVENTS = new Set([
  'library_update', 'library_removed', 'job_added', 'job_update',
  'download_update', 'download_added', 'mandate_update',
])

const STATE_META: Record<LibraryEntryState, {
  label: string
  tone: 'neutral' | 'success' | 'warning' | 'danger' | 'info'
  pulse?: boolean
}> = {
  requested: { label: 'Requested', tone: 'info' },
  queued: { label: 'Queued', tone: 'neutral' },
  downloading: { label: 'Downloading', tone: 'warning', pulse: true },
  verifying: { label: 'Verifying', tone: 'info' },
  ready: { label: 'Ready', tone: 'success' },
  paused: { label: 'Paused', tone: 'warning' },
}

function needsAttention(entry: LibraryViewEntry): boolean {
  return entry.needs_attention || entry.state === 'paused'
}

function matchesFilter(entry: LibraryViewEntry, filter: StateFilter): boolean {
  if (filter === 'all') return true
  if (filter === 'ready') return entry.state === 'ready'
  if (filter === 'in_progress') return IN_PROGRESS_STATES.includes(entry.state)
  return needsAttention(entry)
}

function formatSize(bytes: number): string {
  if (!bytes) return 'Unknown'
  const tb = bytes / 1024 ** 4
  if (tb >= 1) return `${tb.toFixed(2)} TB`
  const gb = bytes / 1024 ** 3
  if (gb >= 1) return `${gb.toFixed(1)} GB`
  return `${(bytes / 1024 ** 2).toFixed(0)} MB`
}

function mediaTypeLabel(type: MediaType): string {
  if (type === 'tv') return 'TV Show'
  if (type === 'movie') return 'Movie'
  return 'Media'
}

/**
 * Entries not yet in the library carry a raw TMDB path like "/abc.jpg";
 * organized items store full URLs. Handle both.
 */
function posterUrl(path: string): string {
  if (!path) return ''
  return path.startsWith('/') ? `${TMDB_POSTER_BASE}${path}` : path
}

/** Total episodes on disk from the per-season episode map; null when no data. */
function episodesOnDisk(item: LibraryItem): number | null {
  if (!item.episodes || Object.keys(item.episodes).length === 0) return null
  return Object.values(item.episodes).reduce((acc, eps) => acc + Object.keys(eps || {}).length, 0)
}

function CopyValue({ value, display }: { value: string; display?: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <button
      type="button"
      className="inline-flex min-w-0 items-center gap-1 text-left font-mono text-xs text-muted transition-colors hover:text-text"
      onClick={() => {
        navigator.clipboard.writeText(value)
        setCopied(true)
        setTimeout(() => setCopied(false), 1400)
      }}
      title="Copy"
    >
      <Copy size={11} className="shrink-0" />
      <span className="truncate">{copied ? 'Copied' : (display || value)}</span>
    </button>
  )
}

function StatCard({
  icon,
  label,
  value,
  detail,
}: {
  icon: React.ReactNode
  label: string
  value: string
  detail: string
}) {
  return (
    <Card className="bg-panel/75 p-3 sm:p-4">
      <div className="flex items-center gap-1.5 text-muted sm:gap-2">
        {icon}
        <span className="truncate text-[11px] font-medium sm:text-xs">{label}</span>
      </div>
      <p className="mt-2 text-xl font-semibold tracking-tight text-text sm:text-2xl">{value}</p>
      <p className="mt-0.5 truncate text-[11px] text-muted sm:text-xs">{detail}</p>
    </Card>
  )
}

function Poster({ item, className }: { item: LibraryItem; className?: string }) {
  if (item.poster_path) {
    return <img src={posterUrl(item.poster_path)} alt={item.title} className={clsx('h-full w-full object-cover', className)} />
  }
  return (
    <div className={clsx('flex h-full w-full flex-col items-center justify-center gap-2 bg-card text-muted', className)}>
      {item.media_type === 'tv' ? <Tv size={26} /> : <Film size={26} />}
      <span className="px-3 text-center text-xs leading-tight">{item.title}</span>
    </div>
  )
}

function EntryPoster({ entry, className }: { entry: LibraryViewEntry; className?: string }) {
  const url = posterUrl(entry.poster_path)
  if (url) {
    return <img src={url} alt={entry.title} className={clsx('h-full w-full object-cover', className)} />
  }
  return (
    <div className={clsx('flex h-full w-full flex-col items-center justify-center gap-2 bg-card text-muted', className)}>
      {entry.media_type === 'tv' ? <Tv size={26} /> : <Film size={26} />}
      <span className="px-3 text-center text-xs leading-tight">{entry.title}</span>
    </div>
  )
}

function StateChip({ entry }: { entry: LibraryViewEntry }) {
  const meta = STATE_META[entry.state]
  return (
    <Badge tone={meta.tone} className={clsx('bg-bg/75 backdrop-blur-sm', meta.pulse && 'animate-pulse')}>
      {meta.label}
    </Badge>
  )
}

function ViewCard({
  entry,
  onOpen,
  onDetails,
}: {
  entry: LibraryViewEntry
  onOpen: () => void
  onDetails: (() => void) | null
}) {
  const totalEpisodes = entry.ready_count + entry.pending_count
  const transfers = entry.transfers
  return (
    <div
      role="button"
      tabIndex={0}
      className="group min-w-0 cursor-pointer text-left"
      onClick={onOpen}
      onKeyDown={event => { if (event.key === 'Enter') onOpen() }}
    >
      <div className="poster-surface relative aspect-[2/3] transition-all duration-300 group-hover:-translate-y-1 group-hover:scale-[1.018] group-hover:border-primary/35">
        <EntryPoster entry={entry} />
        <div className="absolute right-1.5 top-1.5 flex flex-col items-end gap-1">
          <StateChip entry={entry} />
          {entry.needs_attention && entry.state !== 'paused' && (
            <Badge tone="danger" className="bg-bg/75 backdrop-blur-sm">Needs attention</Badge>
          )}
          {entry.media_type === 'tv' && totalEpisodes > 0 && (
            <Badge
              tone={entry.ready_count >= totalEpisodes ? 'success' : 'neutral'}
              className="bg-bg/75 backdrop-blur-sm"
            >
              {entry.ready_count} of {totalEpisodes} ready
            </Badge>
          )}
        </div>
        <div className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-bg/95 via-bg/45 to-transparent p-2 opacity-0 transition-opacity group-hover:opacity-100">
          <div className="flex items-center justify-between gap-2">
            <Badge tone="neutral">{mediaTypeLabel(entry.media_type)}</Badge>
            {entry.job && entry.state !== 'ready' && (
              <span className="truncate rounded-md bg-bg/70 px-1.5 py-0.5 text-[11px] text-muted">
                {entry.job.state_line}
              </span>
            )}
          </div>
        </div>
      </div>
      <div className="mt-3 min-w-0">
        <p className="truncate text-sm font-semibold text-text">{entry.title}</p>
        <p className="mt-0.5 truncate text-xs text-muted">
          {entry.year ? `${entry.year} · ` : ''}
          {mediaTypeLabel(entry.media_type)}
        </p>
        {transfers && transfers.progress !== null && (
          <div className="mt-2">
            <Progress value={transfers.progress * 100} className="h-1.5" />
            {transfers.stale ? (
              <p className="mt-1 truncate text-[11px] text-amber-200">
                Out of date — last update <RelativeTime ts={transfers.stats_updated_at} />
              </p>
            ) : (
              <p className="mt-1 truncate text-[11px] text-muted">
                {Math.round(transfers.progress * 100)}% · updated <RelativeTime ts={transfers.stats_updated_at} />
              </p>
            )}
          </div>
        )}
        {onDetails && (
          <button
            type="button"
            className="mt-1.5 inline-flex items-center gap-1 text-[11px] text-muted transition-colors hover:text-text"
            onClick={event => {
              event.stopPropagation()
              onDetails()
            }}
          >
            <Info size={11} /> Details
          </button>
        )}
      </div>
    </div>
  )
}

function DetailDrawer({
  item,
  onClose,
  onDelete,
}: {
  item: LibraryItem
  onClose: () => void
  onDelete: (item: LibraryItem) => void
}) {
  const navigate = useNavigate()
  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onClose])

  const tmdbUrl = item.tmdb_id
    ? `https://www.themoviedb.org/${item.media_type === 'tv' ? 'tv' : 'movie'}/${item.tmdb_id}`
    : ''

  const seasonEntries = item.media_type === 'tv' && item.episodes
    ? Object.entries(item.episodes).sort(([a], [b]) => Number(a) - Number(b))
    : []

  return (
    <>
      <button
        aria-label="Close details"
        className="fixed inset-0 z-40 cursor-default bg-black/75 backdrop-blur-md"
        onClick={onClose}
      />
      <aside className="fixed right-0 top-0 z-50 h-full w-full max-w-xl overflow-y-auto border-l border-white/10 bg-bg shadow-2xl">
        <div className="relative h-72 overflow-hidden bg-card">
          {item.backdrop_path ? (
            <img src={item.backdrop_path} alt="" className="h-full w-full object-cover" />
          ) : item.poster_path ? (
            <img src={posterUrl(item.poster_path)} alt="" className="h-full w-full scale-110 object-cover opacity-45 blur-sm" />
          ) : (
            <div className="flex h-full items-center justify-center text-border">
              <ImageOff size={42} />
            </div>
          )}
          <div className="absolute inset-0 bg-gradient-to-t from-bg via-bg/45 to-transparent" />
          <Button variant="secondary" size="icon" className="absolute right-3 top-3 bg-bg/70" onClick={onClose}>
            <X size={14} />
          </Button>
        </div>

        <div className="relative -mt-20 px-5 pb-8 sm:px-6">
          <div className="flex gap-4">
            <div className="poster-surface h-40 w-28 shrink-0">
              <Poster item={item} />
            </div>
            <div className="min-w-0 pt-20">
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone="info">{mediaTypeLabel(item.media_type)}</Badge>
                {item.year && <span className="text-sm text-muted">{item.year}</span>}
                {item.rating && (
                  <span className="inline-flex items-center gap-1 text-sm text-amber-200">
                    <Star size={12} fill="currentColor" /> {item.rating.toFixed(1)}
                  </span>
                )}
              </div>
              <h2 className="mt-2 text-3xl font-semibold leading-tight tracking-tight text-text">{item.title}</h2>
              {item.genres?.length > 0 && (
                <p className="mt-1 text-sm text-muted">{item.genres.join(' · ')}</p>
              )}
            </div>
          </div>

          {item.overview && (
            <p className="mt-5 text-sm leading-relaxed text-muted">{item.overview}</p>
          )}

          <div className="mt-6 grid gap-3 sm:grid-cols-3">
            <Card className="p-3">
              <p className="text-xs text-muted">Storage</p>
              <p className="mt-1 text-sm font-medium text-text">{formatSize(item.size_bytes)}</p>
            </Card>
            <Card className="p-3">
              <p className="text-xs text-muted">Type</p>
              <p className="mt-1 text-sm font-medium text-text">{mediaTypeLabel(item.media_type)}</p>
            </Card>
            <Card className="p-3">
              <p className="text-xs text-muted">{item.media_type === 'tv' ? 'Episodes' : 'Quality'}</p>
              <p className="mt-1 text-sm font-medium text-text">
                {item.media_type === 'tv' ? item.episode_count || 'Unknown' : String(item.metadata?.quality || 'Unknown')}
              </p>
            </Card>
          </div>

          {item.media_type === 'tv' && (seasonEntries.length > 0 || item.tmdb_id) && (
            <div className="mt-6 space-y-3 border-t border-white/10 pt-5">
              <SectionHeader
                title="Seasons"
                icon={<Layers size={14} className="text-primary-light" />}
                meta={item.episode_count ? `${episodesOnDisk(item) ?? 0} of ${item.episode_count} episodes` : undefined}
              />
              {seasonEntries.length > 0 ? (
                <div className="space-y-2">
                  {seasonEntries.map(([season, eps]) => {
                    const episodeNumbers = Object.keys(eps || {})
                      .map(Number)
                      .filter(n => !Number.isNaN(n))
                      .sort((a, b) => a - b)
                    return (
                      <Card key={season} className="p-3">
                        <p className="text-sm font-medium text-text">
                          Season {season} — {episodeNumbers.length} episode{episodeNumbers.length === 1 ? '' : 's'}
                        </p>
                        {episodeNumbers.length > 0 && (
                          <div className="mt-2 flex flex-wrap gap-1.5">
                            {episodeNumbers.map(num => (
                              <span
                                key={num}
                                className="inline-flex h-6 min-w-6 items-center justify-center rounded-full border border-emerald-400/25 bg-emerald-400/15 px-1.5 text-[11px] font-semibold text-emerald-300"
                              >
                                {num}
                              </span>
                            ))}
                          </div>
                        )}
                      </Card>
                    )
                  })}
                </div>
              ) : (
                <p className="text-sm text-muted">No episode details yet — try scanning your library.</p>
              )}
              {item.tmdb_id && (
                <Button
                  variant="primary"
                  size="sm"
                  className="w-full"
                  onClick={() => navigate(`/show/${item.tmdb_id}?type=${item.media_type}`)}
                >
                  Open title page <ArrowRight size={13} />
                </Button>
              )}
            </div>
          )}

          <div className="mt-6 space-y-3 border-t border-white/10 pt-5">
            <SectionHeader title="File room" icon={<FolderOpen size={14} className="text-primary-light" />} />
            <Card className="p-3">
              <div className="flex min-w-0 items-center justify-between gap-3">
                <span className="text-xs text-muted">Path</span>
                <CopyValue value={item.path} display={item.path.split('/').slice(-3).join('/')} />
              </div>
            </Card>
          </div>

          <div className="mt-5 space-y-3 border-t border-white/10 pt-5">
            <SectionHeader title="Credits" icon={<Info size={14} className="text-primary-light" />} />
            <div className="grid gap-2">
              {item.tmdb_id && (
                <Card className="p-3">
                  <div className="flex items-center justify-between gap-3">
                    <span className="text-xs text-muted">TMDB</span>
                    <div className="flex items-center gap-2">
                      <CopyValue value={String(item.tmdb_id)} />
                      {tmdbUrl && (
                        <a href={tmdbUrl} target="_blank" rel="noreferrer" className="text-muted transition-colors hover:text-primary-light">
                          <ExternalLink size={13} />
                        </a>
                      )}
                    </div>
                  </div>
                </Card>
              )}
              {item.imdb_id && (
                <Card className="p-3">
                  <div className="flex items-center justify-between gap-3">
                    <span className="text-xs text-muted">IMDb</span>
                    <CopyValue value={item.imdb_id} />
                  </div>
                </Card>
              )}
            </div>
          </div>

          <div className="mt-6 flex flex-wrap gap-2">
            <Button variant="secondary" size="sm" onClick={() => navigator.clipboard.writeText(item.path)}>
              <Copy size={13} /> Copy path
            </Button>
            <Button variant="danger" size="sm" className="ml-auto" onClick={() => onDelete(item)}>
              <Trash2 size={13} /> Remove
            </Button>
          </div>
        </div>
      </aside>
    </>
  )
}

export default function Library() {
  const navigate = useNavigate()
  const [view, setView] = useState<LibraryViewEntry[]>([])
  const [items, setItems] = useState<LibraryItem[]>([])
  const [loading, setLoading] = useState(true)
  const [filter, setFilter] = useState<StateFilter>('all')
  const [mediaFilter, setMediaFilter] = useState<MediaFilter>('all')
  const [query, setQuery] = useState('')
  const [scanning, setScanning] = useState(false)
  const [scanResult, setScanResult] = useState('')
  const [selected, setSelected] = useState<LibraryItem | null>(null)
  const refreshTimer = useRef<number | null>(null)

  const refresh = useCallback(async () => {
    const [entries, libraryItems] = await Promise.all([getLibraryView(), getLibrary()])
    setView(entries)
    setItems(libraryItems)
  }, [])

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        await refresh()
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => {
      cancelled = true
      if (refreshTimer.current) window.clearTimeout(refreshTimer.current)
    }
  }, [refresh])

  useWebSocket((event) => {
    const type = event.type as string
    if (type === 'library_update') {
      const updated = event.data as LibraryItem
      setItems(prev => {
        const exists = prev.some(item => item.id === updated.id)
        return exists ? prev.map(item => item.id === updated.id ? { ...item, ...updated } : item) : [updated, ...prev]
      })
      setSelected(prev => prev?.id === updated.id ? { ...prev, ...updated } : prev)
    }
    if (type === 'library_removed') {
      const { id } = event.data as { id: string }
      setItems(prev => prev.filter(item => item.id !== id))
      setSelected(prev => prev?.id === id ? null : prev)
    }
    if (REFRESH_EVENTS.has(type)) {
      if (refreshTimer.current) window.clearTimeout(refreshTimer.current)
      refreshTimer.current = window.setTimeout(() => { refresh() }, 400)
    }
  })

  const readyCount = view.filter(entry => entry.state === 'ready').length
  const inProgressCount = view.filter(entry => IN_PROGRESS_STATES.includes(entry.state)).length
  const attentionCount = view.filter(needsAttention).length
  const storageBytes = view.reduce((acc, entry) => acc + (entry.size_bytes || 0), 0)
  const featured = items.find(item => item.backdrop_path || item.poster_path) || items[0] || null

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    return view.filter(entry => {
      if (!matchesFilter(entry, filter)) return false
      if (mediaFilter !== 'all' && entry.media_type !== mediaFilter) return false
      if (q && !`${entry.title} ${entry.year ?? ''}`.toLowerCase().includes(q)) return false
      return true
    })
  }, [view, query, filter, mediaFilter])

  const drawerItemFor = (entry: LibraryViewEntry): LibraryItem | null => {
    if (!entry.in_library || !entry.library_item_id) return null
    return items.find(item => item.id === entry.library_item_id) ?? null
  }

  const openEntry = (entry: LibraryViewEntry) => {
    if (entry.tmdb_id) {
      navigate(`/show/${entry.tmdb_id}?type=${entry.media_type === 'movie' ? 'movie' : 'tv'}`)
      return
    }
    const drawerItem = drawerItemFor(entry)
    if (drawerItem) setSelected(drawerItem)
  }

  const handleScan = async () => {
    setScanning(true)
    setScanResult('')
    try {
      const result = await scanLibrary()
      setScanResult(`Scanned ${result.scanned}; added ${result.added}.`)
      await refresh()
    } catch (e: unknown) {
      setScanResult(e instanceof Error ? e.message : 'Scan failed')
    } finally {
      setScanning(false)
    }
  }

  const handleDelete = async (item: LibraryItem) => {
    if (!confirm(`Remove "${item.title}" from Sparrow?\n\nFiles on disk are not deleted.`)) return
    await deleteLibraryItem(item.id)
    setItems(prev => prev.filter(existing => existing.id !== item.id))
    setSelected(prev => prev?.id === item.id ? null : prev)
    await refresh()
  }

  return (
    <div className="cinema-page">
      <section className="cinema-hero min-h-[560px] border-b border-white/[0.08]">
        {featured && (
          <>
            <div className="absolute inset-0 opacity-55">
              {featured.backdrop_path ? (
                <img src={featured.backdrop_path} alt="" className="h-full w-full object-cover" />
              ) : featured.poster_path ? (
                <img src={posterUrl(featured.poster_path)} alt="" className="h-full w-full scale-110 object-cover blur-sm" />
              ) : null}
            </div>
            <div className="absolute inset-0 bg-[linear-gradient(90deg,rgba(5,5,7,0.98),rgba(5,5,7,0.75)_48%,rgba(5,5,7,0.35)),linear-gradient(180deg,rgba(5,5,7,0.16),rgba(5,5,7,1))]" />
          </>
        )}
        {!featured && <div className="absolute inset-0 bg-[radial-gradient(circle_at_20%_10%,rgba(244,193,93,0.18),transparent_26rem),linear-gradient(180deg,rgba(12,10,12,0.8),rgba(5,5,7,1))]" />}
        <div className="cinema-shell">
          <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-start">
            <div className="min-w-0">
              <div className="cinema-kicker">
                <Sparkles size={12} /> Your library
              </div>
              <h1 className="cinema-title">{featured ? featured.title : 'Your private cinema shelf'}</h1>
              <p className="cinema-copy">
                Everything you have asked for — from the moment it is requested until it is ready to watch.
              </p>
            </div>
            <div className="flex flex-wrap gap-2">
              <Button variant="secondary" size="sm" onClick={refresh}>
                <RefreshCw size={13} /> Refresh
              </Button>
              <Button variant="primary" size="sm" onClick={handleScan} disabled={scanning}>
                {scanning ? <Loader2 size={13} className="animate-spin" /> : <ScanLine size={13} />}
                Scan
              </Button>
            </div>
          </div>

          <div className="mt-10 grid max-w-3xl grid-cols-1 gap-2 sm:grid-cols-3 sm:gap-3">
            <StatCard
              icon={<CheckCircle2 size={14} />}
              label="Ready"
              value={String(readyCount)}
              detail={readyCount ? 'Ready to watch' : 'None yet'}
            />
            <StatCard
              icon={<DownloadCloud size={14} />}
              label="In progress"
              value={String(inProgressCount)}
              detail={attentionCount ? `${attentionCount} need${attentionCount === 1 ? 's' : ''} attention` : 'On the way'}
            />
            <StatCard
              icon={<HardDrive size={14} />}
              label="Storage"
              value={formatSize(storageBytes)}
              detail={`${view.length} title${view.length === 1 ? '' : 's'}`}
            />
          </div>
        </div>
      </section>

      <main className="w-full max-w-7xl space-y-8 overflow-hidden p-4 sm:p-8">
        <div className="flex flex-col gap-3">
          <div className="flex w-full min-w-0 flex-col gap-3 lg:flex-row lg:items-center">
            <div className="relative min-w-0 flex-1 lg:max-w-md">
              <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
              <input
                className="input h-10 pl-9"
                value={query}
                onChange={event => setQuery(event.target.value)}
                placeholder="Search title or year"
              />
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <div className="flex shrink-0 rounded-full border border-white/10 bg-white/[0.06] p-1 backdrop-blur-xl">
                {STATE_FILTERS.map(option => (
                  <button
                    key={option.id}
                    type="button"
                    onClick={() => setFilter(option.id)}
                    className={clsx(
                      'flex h-8 items-center gap-1.5 rounded-full px-3 text-xs font-semibold transition-colors',
                      filter === option.id ? 'bg-primary text-bg' : 'text-muted hover:bg-white/[0.06] hover:text-text',
                    )}
                  >
                    {option.label}
                    {option.id === 'attention' && attentionCount > 0 && (
                      <span className={clsx(
                        'inline-flex h-4 min-w-4 items-center justify-center rounded-full px-1 text-[10px] font-bold',
                        filter === 'attention' ? 'bg-bg/25 text-bg' : 'bg-amber-300/20 text-amber-200',
                      )}>
                        {attentionCount}
                      </span>
                    )}
                  </button>
                ))}
              </div>
              <div className="flex shrink-0 rounded-full border border-white/10 bg-white/[0.06] p-1 backdrop-blur-xl">
                {(['all', 'movie', 'tv'] as const).map(option => (
                  <button
                    key={option}
                    type="button"
                    onClick={() => setMediaFilter(option)}
                    className={clsx(
                      'flex h-8 items-center gap-1.5 rounded-full px-3 text-xs font-semibold transition-colors',
                      mediaFilter === option ? 'bg-primary text-bg' : 'text-muted hover:bg-white/[0.06] hover:text-text',
                    )}
                  >
                    {option === 'movie' && <Film size={12} />}
                    {option === 'tv' && <Tv size={12} />}
                    {option === 'all' ? 'All' : option === 'movie' ? 'Movies' : 'TV'}
                  </button>
                ))}
              </div>
            </div>
          </div>
          {scanResult && <p className="text-xs text-muted">{scanResult}</p>}
        </div>

        {loading ? (
          <div className="flex items-center gap-2 text-sm text-muted">
            <Loader2 size={14} className="animate-spin" /> Loading library...
          </div>
        ) : filtered.length === 0 ? (
          <div className="rounded-lg border border-dashed border-white/[0.12] bg-panel/50 p-10 text-center">
            <Film size={36} className="mx-auto text-muted/30" />
            <p className="mt-3 text-sm font-medium text-text">No matching titles</p>
            <p className="mt-1 text-sm text-muted">Try a different filter, or request something new — it will appear here right away.</p>
          </div>
        ) : (
          <section>
            <SectionHeader title="Continue browsing" meta={`${filtered.length} shown`} />
            <div className="grid grid-cols-2 gap-x-4 gap-y-7 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6">
              {filtered.map(entry => {
                const drawerItem = drawerItemFor(entry)
                return (
                  <ViewCard
                    key={`${entry.media_type}:${entry.tmdb_id ?? entry.library_item_id ?? entry.title}`}
                    entry={entry}
                    onOpen={() => openEntry(entry)}
                    onDetails={drawerItem ? () => setSelected(drawerItem) : null}
                  />
                )
              })}
            </div>
          </section>
        )}
      </main>

      {selected && (
        <DetailDrawer item={selected} onClose={() => setSelected(null)} onDelete={handleDelete} />
      )}
    </div>
  )
}
