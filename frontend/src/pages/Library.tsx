import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  ArrowRight, Clock, Copy, DownloadCloud, ExternalLink, Film, FolderOpen,
  ImageOff, Info, Layers, Loader2, RefreshCw, ScanLine, Search, Star, Trash2, Tv, X,
} from 'lucide-react'
import clsx from 'clsx'
import { deleteLibraryItem, getLibrary, getLibraryView, scanLibrary, TMDB_POSTER_BASE } from '../api/client'
import type { LibraryEntryState, LibraryItem, LibraryViewEntry, MediaType } from '../types'
import { useWebSocket } from '../hooks/useWebSocket'
import { Badge, Button, Card, RelativeTime, SectionHeader } from '../components/ui'

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

/** Short human state labels + dot accents for the poster corner chip. */
const STATE_META: Record<LibraryEntryState, { label: string; dot: string; pulse?: boolean }> = {
  requested: { label: 'Requested', dot: 'bg-sky-300' },
  queued: { label: 'Queued', dot: 'bg-muted' },
  downloading: { label: 'Downloading', dot: 'bg-primary', pulse: true },
  verifying: { label: 'Verifying', dot: 'bg-sky-300', pulse: true },
  ready: { label: 'Ready', dot: 'bg-emerald-300' },
  paused: { label: 'Paused', dot: 'bg-amber-300' },
}

function needsAttention(entry: LibraryViewEntry): boolean {
  return entry.needs_attention || entry.state === 'paused'
}

function isInProgress(entry: LibraryViewEntry): boolean {
  return IN_PROGRESS_STATES.includes(entry.state)
}

function matchesFilter(entry: LibraryViewEntry, filter: StateFilter): boolean {
  if (filter === 'all') return true
  if (filter === 'ready') return entry.state === 'ready'
  if (filter === 'in_progress') return isInProgress(entry)
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

function formatEta(seconds: number): string {
  if (seconds < 60) return 'under a minute left'
  const minutes = Math.round(seconds / 60)
  if (minutes < 60) return `${minutes} min left`
  const hours = Math.floor(minutes / 60)
  return `${hours}h ${minutes % 60}m left`
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

function entryKey(entry: LibraryViewEntry): string {
  return `${entry.media_type}:${entry.tmdb_id ?? entry.library_item_id ?? entry.title}`
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

/** Corner chip: one short human state label. Silent for clean, ready titles. */
function StateChip({ entry }: { entry: LibraryViewEntry }) {
  const attention = entry.needs_attention && entry.state !== 'paused'
  if (entry.state === 'ready' && !attention) return null
  const meta = STATE_META[entry.state]
  const pct = entry.transfers?.progress
  return (
    <span
      className={clsx(
        'inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[10px] font-semibold backdrop-blur-md',
        attention
          ? 'border-rose-400/40 bg-rose-950/70 text-rose-200'
          : entry.state === 'paused'
            ? 'border-amber-300/35 bg-amber-950/60 text-amber-200'
            : 'border-white/15 bg-bg/80 text-text/90',
      )}
    >
      <span className={clsx('h-1.5 w-1.5 rounded-full', attention ? 'bg-rose-300' : meta.dot, (meta.pulse || attention) && 'animate-pulse')} />
      {attention ? 'Needs attention' : meta.label}
      {entry.state === 'downloading' && pct !== null && pct !== undefined && (
        <span className="text-primary-light">{Math.round(pct * 100)}%</span>
      )}
    </span>
  )
}

/** One-line status under the title for anything that is not simply ready. */
function StatusLine({ entry }: { entry: LibraryViewEntry }) {
  const transfers = entry.transfers
  if (entry.needs_attention && entry.state !== 'paused') {
    return <p className="mt-0.5 truncate text-xs font-medium text-rose-300">Needs attention</p>
  }
  if (entry.state === 'paused') {
    return <p className="mt-0.5 truncate text-xs font-medium text-amber-200">Paused</p>
  }
  if (entry.state === 'downloading' && transfers && transfers.progress !== null) {
    if (transfers.stale) {
      return (
        <p className="mt-0.5 truncate text-xs text-amber-200">
          Stalled — updated <RelativeTime ts={transfers.stats_updated_at} />
        </p>
      )
    }
    return (
      <p className="mt-0.5 truncate text-xs text-muted">
        {Math.round(transfers.progress * 100)}%
        {transfers.eta_seconds ? ` · ${formatEta(transfers.eta_seconds)}` : ''}
      </p>
    )
  }
  if (isInProgress(entry)) {
    return <p className="mt-0.5 truncate text-xs text-muted">{STATE_META[entry.state].label}</p>
  }
  return (
    <p className="mt-0.5 truncate text-xs text-muted">
      {entry.year ? `${entry.year} · ` : ''}
      {mediaTypeLabel(entry.media_type)}
    </p>
  )
}

function PosterCard({
  entry,
  onOpen,
  onDetails,
  className,
}: {
  entry: LibraryViewEntry
  onOpen: () => void
  onDetails: (() => void) | null
  className?: string
}) {
  const attention = needsAttention(entry)
  const active = isInProgress(entry)
  const totalEpisodes = entry.ready_count + entry.pending_count
  const transfers = entry.transfers
  const showProgress = active && transfers && transfers.progress !== null
  return (
    <div
      role="button"
      tabIndex={0}
      className={clsx('group min-w-0 cursor-pointer text-left', className)}
      onClick={onOpen}
      onKeyDown={event => { if (event.key === 'Enter') onOpen() }}
    >
      <div
        className={clsx(
          'relative aspect-[2/3] overflow-hidden rounded-xl border bg-white/[0.05] shadow-lg shadow-black/40',
          'transition-all duration-300 group-hover:-translate-y-1 group-hover:scale-[1.03] group-hover:shadow-poster',
          attention
            ? entry.needs_attention && entry.state !== 'paused'
              ? 'border-rose-400/40 group-hover:border-rose-300/60'
              : 'border-amber-300/35 group-hover:border-amber-200/55'
            : 'border-white/10 group-hover:border-primary/40',
          entry.state === 'downloading' && 'poster-shimmer',
        )}
      >
        <EntryPoster entry={entry} className={clsx(active && 'opacity-90')} />

        <div className="absolute left-1.5 top-1.5 flex max-w-[calc(100%-0.75rem)] flex-col items-start gap-1">
          <StateChip entry={entry} />
        </div>

        {entry.media_type === 'tv' && totalEpisodes > 0 && entry.ready_count < totalEpisodes && (
          <span className="absolute bottom-2.5 right-1.5 rounded-full border border-white/15 bg-bg/80 px-2 py-0.5 text-[10px] font-semibold text-text/90 backdrop-blur-md">
            {entry.ready_count}/{totalEpisodes} eps
          </span>
        )}

        {entry.job && entry.state !== 'ready' && (
          <div className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-bg/95 via-bg/55 to-transparent p-2 pb-3 opacity-0 transition-opacity duration-200 group-hover:opacity-100">
            <p className="line-clamp-2 text-[11px] leading-snug text-text/85">{entry.job.state_line}</p>
          </div>
        )}

        {showProgress && (
          <div className="absolute inset-x-0 bottom-0 h-1 bg-black/60">
            <div
              className={clsx(
                'h-full rounded-r-full transition-all duration-500',
                transfers.stale
                  ? 'bg-amber-300/80'
                  : 'bg-gradient-to-r from-primary-dark via-primary to-primary-light',
              )}
              style={{ width: `${Math.max(2, Math.min(100, transfers.progress! * 100))}%` }}
            />
          </div>
        )}
      </div>

      <div className="mt-2.5 min-w-0 px-0.5">
        <p className="truncate text-sm font-semibold text-text">{entry.title}</p>
        <StatusLine entry={entry} />
        {onDetails && (
          <button
            type="button"
            className="mt-1 inline-flex items-center gap-1 text-[11px] text-muted/80 transition-colors hover:text-text"
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

function Shelf({
  title,
  icon,
  meta,
  children,
}: {
  title: string
  icon?: React.ReactNode
  meta?: string
  children: React.ReactNode
}) {
  return (
    <section className="min-w-0">
      <SectionHeader title={title} icon={icon} meta={meta} />
      <div className="nav-scroll -mx-4 flex gap-4 overflow-x-auto px-4 pb-2 pt-1 sm:-mx-8 sm:px-8">
        {children}
      </div>
    </section>
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
  const inProgressCount = view.filter(isInProgress).length
  const attentionCount = view.filter(needsAttention).length
  const storageBytes = view.reduce((acc, entry) => acc + (entry.size_bytes || 0), 0)

  /** True when nothing is narrowing the view — shelves only appear here. */
  const browsing = query.trim() === '' && filter === 'all' && mediaFilter === 'all'

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    return view.filter(entry => {
      if (!matchesFilter(entry, filter)) return false
      if (mediaFilter !== 'all' && entry.media_type !== mediaFilter) return false
      if (q && !`${entry.title} ${entry.year ?? ''}`.toLowerCase().includes(q)) return false
      return true
    })
  }, [view, query, filter, mediaFilter])

  /** Active work first: anything moving or stuck, most urgent (attention) leading. */
  const onTheWay = useMemo(() => {
    const active = view.filter(entry => isInProgress(entry) || needsAttention(entry))
    return [...active].sort((a, b) => Number(needsAttention(b)) - Number(needsAttention(a)))
  }, [view])

  const recentlyAdded = useMemo(() => {
    const addedAt = new Map(items.map(item => [item.id, item.added_at]))
    return view
      .filter(entry => entry.state === 'ready' && entry.library_item_id && addedAt.has(entry.library_item_id))
      .sort((a, b) => (addedAt.get(b.library_item_id!) ?? 0) - (addedAt.get(a.library_item_id!) ?? 0))
      .slice(0, 12)
  }, [view, items])

  const collection = useMemo(() => {
    const byTitle = (a: LibraryViewEntry, b: LibraryViewEntry) => a.title.localeCompare(b.title)
    if (!browsing) {
      return [{ title: 'Results', entries: [...filtered].sort(byTitle) }]
    }
    const shows = filtered.filter(entry => entry.media_type === 'tv').sort(byTitle)
    const movies = filtered.filter(entry => entry.media_type !== 'tv').sort(byTitle)
    return [
      { title: 'Shows', entries: shows },
      { title: 'Movies', entries: movies },
    ].filter(section => section.entries.length > 0)
  }, [filtered, browsing])

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

  const renderCard = (entry: LibraryViewEntry, className?: string) => {
    const drawerItem = drawerItemFor(entry)
    return (
      <PosterCard
        key={entryKey(entry)}
        entry={entry}
        className={className}
        onOpen={() => openEntry(entry)}
        onDetails={drawerItem ? () => setSelected(drawerItem) : null}
      />
    )
  }

  return (
    <div className="cinema-page">
      <main className="mx-auto w-full max-w-[1600px] space-y-9 px-4 pb-16 pt-20 sm:px-8 sm:pt-24">
        <div className="flex flex-col gap-4">
          <div className="flex flex-wrap items-end justify-between gap-3">
            <div className="min-w-0">
              <h1 className="text-2xl font-semibold tracking-tight text-text sm:text-3xl">Library</h1>
              <p className="mt-1 text-xs text-muted sm:text-sm">
                {view.length} title{view.length === 1 ? '' : 's'} · {readyCount} ready
                {inProgressCount > 0 && ` · ${inProgressCount} on the way`}
                {attentionCount > 0 && (
                  <span className="text-amber-200"> · {attentionCount} need{attentionCount === 1 ? 's' : ''} attention</span>
                )}
                {storageBytes > 0 && ` · ${formatSize(storageBytes)}`}
              </p>
            </div>
            <div className="flex shrink-0 gap-2">
              <Button variant="ghost" size="sm" onClick={refresh}>
                <RefreshCw size={13} /> Refresh
              </Button>
              <Button variant="secondary" size="sm" onClick={handleScan} disabled={scanning}>
                {scanning ? <Loader2 size={13} className="animate-spin" /> : <ScanLine size={13} />}
                Scan
              </Button>
            </div>
          </div>

          <div className="flex w-full min-w-0 flex-col gap-2.5 lg:flex-row lg:items-center">
            <div className="relative min-w-0 flex-1 lg:max-w-xs">
              <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
              <input
                className="input h-9 pl-9 text-sm"
                value={query}
                onChange={event => setQuery(event.target.value)}
                placeholder="Search titles"
              />
            </div>
            <div className="nav-scroll flex items-center gap-2 overflow-x-auto">
              <div className="flex shrink-0 rounded-full border border-white/10 bg-white/[0.06] p-1 backdrop-blur-xl">
                {STATE_FILTERS.map(option => (
                  <button
                    key={option.id}
                    type="button"
                    onClick={() => setFilter(option.id)}
                    className={clsx(
                      'flex h-7 items-center gap-1.5 rounded-full px-3 text-xs font-semibold transition-colors',
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
                      'flex h-7 items-center gap-1.5 rounded-full px-3 text-xs font-semibold transition-colors',
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
        ) : view.length === 0 ? (
          <div className="rounded-2xl border border-dashed border-white/[0.12] bg-panel/50 p-12 text-center">
            <Film size={36} className="mx-auto text-muted/30" />
            <p className="mt-3 text-sm font-medium text-text">Your library is empty</p>
            <p className="mt-1 text-sm text-muted">Request something from Discover, or scan your library folder to import what is already there.</p>
          </div>
        ) : (
          <>
            {browsing && onTheWay.length > 0 && (
              <Shelf
                title="On the way"
                icon={<DownloadCloud size={14} className="text-primary-light" />}
                meta={`${onTheWay.length} active`}
              >
                {onTheWay.map(entry => renderCard(entry, 'w-32 shrink-0 sm:w-36 lg:w-40'))}
              </Shelf>
            )}

            {browsing && recentlyAdded.length > 0 && (
              <Shelf
                title="Recently added"
                icon={<Clock size={14} className="text-primary-light" />}
              >
                {recentlyAdded.map(entry => renderCard(entry, 'w-32 shrink-0 sm:w-36 lg:w-40'))}
              </Shelf>
            )}

            {filtered.length === 0 ? (
              <div className="rounded-2xl border border-dashed border-white/[0.12] bg-panel/50 p-10 text-center">
                <Film size={36} className="mx-auto text-muted/30" />
                <p className="mt-3 text-sm font-medium text-text">No matching titles</p>
                <p className="mt-1 text-sm text-muted">Try a different filter, or request something new — it will appear here right away.</p>
              </div>
            ) : (
              collection.map(section => (
                <section key={section.title} className="min-w-0">
                  <SectionHeader
                    title={section.title}
                    icon={section.title === 'Shows' ? <Tv size={14} className="text-primary-light" /> : <Film size={14} className="text-primary-light" />}
                    meta={`${section.entries.length} title${section.entries.length === 1 ? '' : 's'}`}
                  />
                  <div className="grid grid-cols-2 gap-x-4 gap-y-7 pt-1 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 2xl:grid-cols-7">
                    {section.entries.map(entry => renderCard(entry))}
                  </div>
                </section>
              ))
            )}
          </>
        )}
      </main>

      {selected && (
        <DetailDrawer item={selected} onClose={() => setSelected(null)} onDelete={handleDelete} />
      )}
    </div>
  )
}
