import { useState, useCallback, useRef, useEffect, useMemo } from 'react'
import { Search as SearchIcon, Download, Tv, Film, Users, Loader2, Zap, Brain, CheckCircle2, XCircle, Sparkles, BookOpen, ArrowDownCircle, ExternalLink, RefreshCw, Star, ChevronLeft, Package, Clapperboard } from 'lucide-react'
import { addDownload, searchSuggest, searchSuggestExpand, searchSuggestTV } from '../api/client'
import type { SuggestionItem, TVSeason, SuggestResponse } from '../api/client'
import type { SearchResult, MediaType, Quality } from '../types'
import clsx from 'clsx'
import { Badge, Button, Card, SectionHeader } from '../components/ui'

// ─── Event types ─────────────────────────────────────────────────────────────

type SearchEvent =
  | { type: 'trying';    strategy: string; query: string; attempt: number }
  | { type: 'hit';       strategy: string; query: string; count: number }
  | { type: 'miss';      strategy: string; query: string }
  | { type: 'evolving';  message: string }
  | { type: 'evolved';   new_queries: string[]; strategy_count: number; version: number }
  | { type: 'evolving_failed'; message: string }
  | { type: 'thinking';  text: string }
  | { type: 'no_results'; message: string }
  | { type: 'library_hit'; items: Array<{ id: string; title: string; media_type: string }> }
  | { type: 'downloading'; items: Array<{ id: string; name: string; status: string }> }
  | { type: 'title_correction'; original: string; canonical: string }
  | { type: 'done';      results: SearchResult[]; winner: string | null; attempts: number }
  | { type: 'error';     message: string }
  | { type: 'ping' }

// ─── Log entry types ──────────────────────────────────────────────────────────

interface QueryRow {
  key: string
  query: string
  strategy: string
  state: 'trying' | 'hit' | 'miss'
  count?: number
  startedAt: number
}

type StatusEntry =
  | { kind: 'evolving';  message: string }
  | { kind: 'evolved';   new_queries: string[]; strategy_count: number; version: number }
  | { kind: 'evolving_failed'; message: string }
  | { kind: 'no_results'; message: string }
  | { kind: 'library_hit'; items: Array<{ id: string; title: string; media_type: string }> }
  | { kind: 'downloading'; items: Array<{ id: string; name: string; status: string }> }
  | { kind: 'title_correction'; original: string; canonical: string }

type LogEntry =
  | { type: 'query'; row: QueryRow }
  | { type: 'status'; entry: StatusEntry; id: number }

interface SeasonEpRow {
  episode: number
  total: number
  state: 'searching' | 'found' | 'missing'
  name?: string
  duplicate?: boolean
}

const QUALITY_OPTIONS: { value: Quality; label: string }[] = [
  { value: '2160p', label: '4K' },
  { value: '1080p', label: '1080p' },
  { value: '720p',  label: '720p' },
  { value: 'any',   label: 'Any' },
]

const STORAGE_KEY = 'sparrow_last_search'

// ─── Helpers ──────────────────────────────────────────────────────────────────

function formatSize(bytes: number): string {
  if (!bytes) return '?'
  const gb = bytes / 1024 ** 3
  if (gb >= 1) return `${gb.toFixed(1)} GB`
  return `${(bytes / 1024 ** 2).toFixed(0)} MB`
}

function detectQuality(name: string): string {
  const n = name.toLowerCase()
  if (n.includes('2160p') || n.includes('4k')) return '4K'
  if (n.includes('1080p')) return '1080p'
  if (n.includes('720p'))  return '720p'
  if (n.includes('480p'))  return '480p'
  return ''
}

function QBadge({ quality }: { quality: string }) {
  if (!quality) return null
  const c: Record<string, string> = {
    '4K': 'bg-yellow-500/20 text-yellow-400',
    '1080p': 'bg-blue-500/20 text-blue-400',
    '720p':  'bg-green-500/20 text-green-400',
    '480p':  'bg-muted/20 text-muted',
  }
  return <span className={clsx('badge text-[10px]', c[quality] || 'bg-muted/20 text-muted')}>{quality}</span>
}

function ResultCard({
  result,
  isAlt,
  isAdded,
  isAdding,
  onDownload,
}: {
  result: SearchResult
  isAlt: boolean
  isAdded: boolean
  isAdding: boolean
  onDownload: () => void
}) {
  const quality = detectQuality(result.name)
  const isNumId = /^\d+$/.test(result.id)
  return (
    <Card className={clsx(
      'group p-4 transition-colors',
      isAlt ? 'border-primary/20 bg-primary/[0.035] hover:border-primary/40' : 'hover:border-primary/30',
    )}>
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            {quality && <QBadge quality={quality} />}
            {isAlt && <Badge tone="info">alternative</Badge>}
            {result.imdb_id && <Badge tone="warning">IMDb</Badge>}
            <span className="text-xs text-muted">{result.source}</span>
          </div>
          <h3 className="mt-2 break-words text-sm font-semibold leading-snug text-text">{result.name}</h3>
          <div className="mt-3 grid gap-2 text-xs text-muted sm:grid-cols-4">
            <div className="rounded-md bg-bg/45 px-2.5 py-2">
              <span className="block text-[10px] uppercase tracking-wider text-muted/70">Seeders</span>
              <span className="mt-0.5 block font-medium text-emerald-300">{result.seeders}</span>
            </div>
            <div className="rounded-md bg-bg/45 px-2.5 py-2">
              <span className="block text-[10px] uppercase tracking-wider text-muted/70">Size</span>
              <span className="mt-0.5 block font-medium text-text">{formatSize(result.size_bytes)}</span>
            </div>
            <div className="rounded-md bg-bg/45 px-2.5 py-2 sm:col-span-2">
              <span className="block text-[10px] uppercase tracking-wider text-muted/70">Uploader</span>
              <span className="mt-0.5 block truncate font-medium text-text">{result.uploader || 'unknown'}</span>
            </div>
          </div>
        </div>
        <div className="flex shrink-0 flex-row gap-2 sm:flex-col">
          <Button
            variant={isAdded ? 'secondary' : 'primary'}
            size="sm"
            onClick={onDownload}
            disabled={isAdding || isAdded}
            className={clsx('w-full sm:w-28', isAdded && 'border-emerald-400/20 bg-emerald-400/10 text-emerald-300')}
          >
            {isAdding ? <Loader2 size={13} className="animate-spin" /> : isAdded ? <CheckCircle2 size={13} /> : <Download size={13} />}
            {isAdding ? 'Adding' : isAdded ? 'Added' : 'Grab'}
          </Button>
          {isNumId && (
            <a
              href={`https://www.thepiratebay.org/torrent/${result.id}`}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex h-8 items-center justify-center gap-1 rounded-md border border-white/10 bg-white/5 px-3 text-xs text-muted transition-colors hover:bg-white/10 hover:text-text"
              onClick={e => e.stopPropagation()}
            >
              <ExternalLink size={12} /> Source
            </a>
          )}
        </div>
      </div>
    </Card>
  )
}

function Label({ kind }: { kind: 'cwm' | 'claude' }) {
  return (
    <span className={clsx(
      'text-[9px] font-mono font-semibold px-1 py-0.5 rounded shrink-0',
      kind === 'cwm' ? 'bg-muted/10 text-muted/60' : 'bg-primary/20 text-primary-light'
    )}>
      [{kind}]
    </span>
  )
}

// ─── Row renderers ────────────────────────────────────────────────────────────

function QueryRowView({ row, now }: { row: QueryRow; now: number }) {
  const elapsed = row.state === 'trying' ? Math.max(0, Math.floor((now - row.startedAt) / 1000)) : null

  if (row.state === 'trying') return (
    <div className="flex items-center gap-1.5 text-xs text-muted py-0.5 animate-pulse">
      <Label kind="cwm" />
      <Loader2 size={10} className="animate-spin text-primary shrink-0" />
      <span className="text-muted/50">{row.strategy}</span>
      <span className="truncate">{row.query}</span>
      {elapsed !== null && elapsed > 0 && <span className="ml-auto text-muted/40 shrink-0">{elapsed}s</span>}
    </div>
  )
  if (row.state === 'miss') return (
    <div className="flex items-center gap-1.5 text-xs text-muted/40 py-0.5">
      <Label kind="cwm" />
      <XCircle size={10} className="shrink-0" />
      <span className="text-muted/30">{row.strategy}</span>
      <span className="truncate line-through">{row.query}</span>
    </div>
  )
  if (row.state === 'hit') return (
    <div className="flex items-center gap-1.5 text-xs text-green-400 py-0.5 font-medium">
      <Label kind="cwm" />
      <CheckCircle2 size={10} className="shrink-0" />
      <span className="text-green-400/60">{row.strategy}</span>
      <span className="truncate">{row.query}</span>
      <span className="ml-auto shrink-0">{row.count} results</span>
    </div>
  )
  return null
}

function StatusEntryView({ entry }: { entry: StatusEntry }) {
  if (entry.kind === 'library_hit') return (
    <div className="flex items-center gap-1.5 text-xs text-yellow-400 py-1">
      <Label kind="cwm" />
      <BookOpen size={10} className="shrink-0" />
      <span>Already in library: {entry.items.map(i => i.title).join(', ')}</span>
    </div>
  )
  if (entry.kind === 'downloading') return (
    <div className="flex items-center gap-1.5 text-xs text-blue-400 py-1">
      <Label kind="cwm" />
      <ArrowDownCircle size={10} className="shrink-0" />
      <span>Downloading: {entry.items.map(i => i.name).join(', ')}</span>
    </div>
  )
  if (entry.kind === 'evolving') return (
    <div className="flex items-center gap-1.5 text-xs text-primary-light py-1 font-medium">
      <Label kind="claude" />
      <Brain size={10} className="animate-pulse shrink-0" />
      <span>{entry.message}</span>
    </div>
  )
  if (entry.kind === 'evolved') return (
    <div className="flex items-center gap-1.5 text-xs text-primary-light py-1 font-medium">
      <Label kind="cwm" />
      <Sparkles size={10} className="shrink-0" />
      <span>New strategy added</span>
      {entry.version > 0 && <span className="badge bg-primary/20 text-primary-light text-[9px]">v{entry.version}</span>}
      <span className="text-muted ml-1 truncate">({entry.new_queries.slice(0, 2).join(', ')})</span>
    </div>
  )
  if (entry.kind === 'evolving_failed') return (
    <div className="flex items-center gap-1.5 text-xs text-red-400 py-0.5">
      <Label kind="claude" />
      <XCircle size={10} className="shrink-0" />
      <span>{entry.message}</span>
    </div>
  )
  if (entry.kind === 'no_results') return (
    <div className="flex items-center gap-1.5 text-xs text-muted py-0.5">
      <Label kind="cwm" />
      <span>{entry.message}</span>
    </div>
  )
  if (entry.kind === 'title_correction') return (
    <div className="flex items-center gap-1.5 text-xs text-orange-400 py-1">
      <Label kind="cwm" />
      <RefreshCw size={10} className="shrink-0" />
      <span className="text-muted/50 line-through">{entry.original}</span>
      <span className="text-muted/40">→</span>
      <span className="font-medium">{entry.canonical}</span>
    </div>
  )
  return null
}

// ─── Suggestion dropdown ──────────────────────────────────────────────────────

function PosterThumb({ url, title }: { url: string | null; title: string }) {
  if (!url) return (
    <div className="w-8 h-12 rounded shrink-0 bg-border/40 flex items-center justify-center text-muted/30">
      <Film size={12} />
    </div>
  )
  return <img src={url} alt={title} className="w-8 h-12 rounded shrink-0 object-cover bg-border/40" />
}

function SuggestionRow({
  item, active, onSelect,
}: { item: SuggestionItem; active: boolean; onSelect: (item: SuggestionItem) => void }) {
  return (
    <button
      className={clsx(
        'w-full flex items-center gap-3 px-3 py-2 text-left transition-colors',
        active ? 'bg-primary/10' : 'hover:bg-card/80',
      )}
      onClick={() => onSelect(item)}
    >
      <PosterThumb url={item.poster_url} title={item.title} />
      <div className="flex-1 min-w-0">
        <p className="text-sm text-text truncate">{item.title}</p>
        <div className="flex items-center gap-2 mt-0.5">
          <span className={clsx(
            'text-[9px] px-1.5 py-0.5 rounded font-medium',
            item.media_type === 'movie' ? 'bg-blue-500/15 text-blue-400' : 'bg-violet-500/15 text-violet-400'
          )}>
            {item.media_type === 'movie' ? 'Movie' : 'TV'}
          </span>
          {item.year && <span className="text-[10px] text-muted/60">{item.year}</span>}
          {item.rating > 0 && (
            <span className="flex items-center gap-0.5 text-[10px] text-yellow-400/80">
              <Star size={8} className="fill-current" />{item.rating}
            </span>
          )}
        </div>
      </div>
      {item.media_type === 'tv' && (
        <ChevronLeft size={12} className="text-muted/40 rotate-180 shrink-0" />
      )}
    </button>
  )
}

interface DropdownProps {
  direct: SuggestResponse | null
  expanded: SuggestionItem[]
  loading: boolean
  expandLoading: boolean
  tvPicked: SuggestionItem | null
  tvSeasons: TVSeason[] | null
  tvSeasonLoading: boolean
  activeIdx: number
  epS: string; epE: string
  setEpS: (v: string) => void; setEpE: (v: string) => void
  onSelect: (item: SuggestionItem) => void
  onSelectTV: (title: string, season: number | null, episode: number | null) => void
  onDownloadSeason: (item: SuggestionItem, season: TVSeason) => void
  onBack: () => void
}

function SuggestionDropdown(props: DropdownProps) {
  const { direct, expanded, loading, expandLoading, tvPicked, tvSeasons, tvSeasonLoading,
          activeIdx, epS, epE, setEpS, setEpE, onSelect, onSelectTV, onDownloadSeason, onBack } = props

  const allDirect = direct?.tmdb ?? []
  // Deduplicate expanded against direct
  const directIds = useMemo(() => new Set(allDirect.map(r => r.tmdb_id)), [allDirect])
  const allExpanded = useMemo(() => expanded.filter(r => !directIds.has(r.tmdb_id)), [expanded, directIds])

  const totalItems = (direct?.library.length ?? 0) + (direct?.downloads.length ?? 0) + allDirect.length + allExpanded.length

  if (!tvPicked && totalItems === 0 && !loading && !expandLoading) return null

  // ── TV season picker ────────────────────────────────────────────────────────
  if (tvPicked) return (
    <div className="absolute top-full left-0 right-0 mt-1 z-50 bg-card border border-border rounded-xl shadow-[0_8px_32px_rgba(0,0,0,0.6)] overflow-hidden">
      <button
        className="flex items-center gap-1.5 px-3 py-2.5 w-full text-left text-xs text-muted hover:text-text transition-colors border-b border-border/50"
        onClick={onBack}
      >
        <ChevronLeft size={12} />
        <PosterThumb url={tvPicked.poster_url} title={tvPicked.title} />
        <span className="font-medium text-text truncate">{tvPicked.title}</span>
      </button>

      <div className="px-3 py-1.5 text-[9px] font-semibold text-muted/50 uppercase tracking-wider bg-border/10">
        What do you want?
      </div>

      {/* Full series */}
      <button
        className="w-full flex items-center gap-2.5 px-3 py-2.5 text-sm text-left hover:bg-card/80 transition-colors"
        onClick={() => onSelectTV(tvPicked.title, null, null)}
      >
        <Package size={13} className="text-violet-400 shrink-0" />
        <span>Full series</span>
      </button>

      {/* Seasons */}
      {tvSeasonLoading && (
        <div className="flex items-center gap-2 px-3 py-2 text-xs text-muted">
          <Loader2 size={10} className="animate-spin" /> Loading seasons…
        </div>
      )}
      {tvSeasons?.map(s => (
        <button
          key={s.season_number}
          className="w-full flex items-center gap-2.5 px-3 py-2 text-sm text-left hover:bg-card/80 transition-colors"
          onClick={() => onDownloadSeason(tvPicked, s)}
        >
          <Package size={13} className="text-muted/50 shrink-0" />
          <span>{s.name}</span>
          <span className="ml-auto text-xs text-muted/50">{s.episode_count} eps</span>
        </button>
      ))}

      {/* Specific episode */}
      <div className="flex items-center gap-2 px-3 py-2.5 border-t border-border/50 mt-0.5">
        <Clapperboard size={13} className="text-muted/50 shrink-0" />
        <span className="text-sm text-muted">Episode</span>
        <div className="flex items-center gap-1 ml-2">
          <span className="text-xs text-muted">S</span>
          <input
            type="number" min="1" value={epS}
            onChange={e => setEpS(e.target.value)}
            onMouseDown={e => e.stopPropagation()}
            className="w-10 input text-xs py-1 px-1.5 text-center"
          />
          <span className="text-xs text-muted">E</span>
          <input
            type="number" min="1" value={epE}
            onChange={e => setEpE(e.target.value)}
            onMouseDown={e => e.stopPropagation()}
            className="w-10 input text-xs py-1 px-1.5 text-center"
          />
        </div>
        <button
          className="ml-auto btn-primary text-xs px-3 py-1"
          onClick={() => onSelectTV(tvPicked.title, parseInt(epS) || 1, parseInt(epE) || 1)}
        >
          Search
        </button>
      </div>
    </div>
  )

  // ── Main dropdown ───────────────────────────────────────────────────────────
  let idx = 0
  return (
    <div className="absolute top-full left-0 right-0 mt-1 z-50 bg-card border border-border rounded-xl shadow-[0_8px_32px_rgba(0,0,0,0.6)] overflow-hidden max-h-[420px] overflow-y-auto">

      {/* Library hits */}
      {(direct?.library ?? []).length > 0 && (
        <>
          <div className="px-3 py-1.5 text-[9px] font-semibold text-yellow-400/60 uppercase tracking-wider bg-yellow-500/5">
            In your library
          </div>
          {direct!.library.map(item => {
            const i = idx++
            return (
              <button
                key={item.id}
                className={clsx('w-full flex items-center gap-2.5 px-3 py-2 text-left transition-colors',
                  activeIdx === i ? 'bg-primary/10' : 'hover:bg-card/80')}
              >
                <BookOpen size={12} className="text-yellow-400 shrink-0" />
                <span className="text-sm text-text">{item.title}</span>
                <span className="ml-auto text-[10px] text-muted/50 shrink-0">{item.media_type}</span>
              </button>
            )
          })}
        </>
      )}

      {/* Active downloads */}
      {(direct?.downloads ?? []).length > 0 && (
        <>
          <div className="px-3 py-1.5 text-[9px] font-semibold text-blue-400/60 uppercase tracking-wider bg-blue-500/5">
            Downloading
          </div>
          {direct!.downloads.map(item => {
            const i = idx++
            return (
              <button
                key={item.id}
                className={clsx('w-full flex items-center gap-2.5 px-3 py-2 text-left transition-colors',
                  activeIdx === i ? 'bg-primary/10' : 'hover:bg-card/80')}
              >
                <ArrowDownCircle size={12} className="text-blue-400 shrink-0" />
                <span className="text-sm text-text">{item.name}</span>
                <span className="ml-auto text-[10px] text-muted/50 shrink-0">{item.status}</span>
              </button>
            )
          })}
        </>
      )}

      {/* TMDB direct */}
      {loading && allDirect.length === 0 && (
        <div className="flex items-center gap-2 px-3 py-3 text-xs text-muted">
          <Loader2 size={11} className="animate-spin" /> Searching…
        </div>
      )}
      {!loading && allDirect.length === 0 && expandLoading && (
        <div className="flex items-center gap-2 px-3 py-3 text-xs text-muted">
          <Loader2 size={11} className="animate-spin" /> Looking up suggestions…
        </div>
      )}
      {allDirect.length > 0 && (
        <>
          <div className="px-3 py-1.5 text-[9px] font-semibold text-muted/50 uppercase tracking-wider bg-border/10">
            Results
          </div>
          {allDirect.map(item => {
            const i = idx++
            return <SuggestionRow key={item.tmdb_id} item={item} active={activeIdx === i} onSelect={onSelect} />
          })}
        </>
      )}

      {/* Haiku-expanded */}
      {allExpanded.length > 0 && (
        <>
          <div className="px-3 py-1.5 text-[9px] font-semibold text-primary-light/50 uppercase tracking-wider bg-primary/5">
            You might also like
          </div>
          {allExpanded.map(item => {
            const i = idx++
            return <SuggestionRow key={item.tmdb_id} item={item} active={activeIdx === i} onSelect={onSelect} />
          })}
        </>
      )}

      {expandLoading && (
        <div className="flex items-center gap-1.5 px-3 py-2 text-[10px] text-muted/50">
          <Loader2 size={9} className="animate-spin" /> Finding related titles…
        </div>
      )}
    </div>
  )
}


// ─── SSE consumer hook ────────────────────────────────────────────────────────

function useSearchStream(
  onLog: (entry: LogEntry) => void,
  onUpdateRow: (query: string, state: 'hit' | 'miss', count?: number) => void,
  onThinking: (text: string) => void,
  onEvolving: (v: boolean) => void,
  onDone: (results: SearchResult[], winner: string | null, attempts: number) => void,
  onStreamError: () => void,
) {
  const esRef          = useRef<EventSource | null>(null)
  const idRef          = useRef(0)
  const thinkingBufRef = useRef('')  // accumulates streamed thinking chunks

  const openStream = useCallback((url: string) => {
    esRef.current?.close()
    thinkingBufRef.current = ''

    const es = new EventSource(url)
    esRef.current = es

    es.onmessage = (e) => {
      try {
        const event: SearchEvent = JSON.parse(e.data)
        if (event.type === 'ping') return

        if (event.type === 'trying') {
          onLog({ type: 'query', row: { key: event.query, query: event.query, strategy: event.strategy, state: 'trying', startedAt: Date.now() } })
        } else if (event.type === 'hit' || event.type === 'miss') {
          onUpdateRow(event.query, event.type, event.type === 'hit' ? event.count : undefined)
        } else if (event.type === 'thinking') {
          // Backend streams incremental chunks — accumulate them
          thinkingBufRef.current += event.text
          onThinking(thinkingBufRef.current)
        } else if (event.type === 'evolving') {
          thinkingBufRef.current = ''
          onThinking('')
          onEvolving(true)
          onLog({ type: 'status', entry: { kind: 'evolving', message: event.message }, id: idRef.current++ })
        } else if (event.type === 'evolved') {
          thinkingBufRef.current = ''
          onThinking('')
          onEvolving(false)
          onLog({ type: 'status', entry: { kind: 'evolved', new_queries: event.new_queries, strategy_count: event.strategy_count, version: event.version }, id: idRef.current++ })
        } else if (event.type === 'evolving_failed') {
          thinkingBufRef.current = ''
          onThinking('')
          onEvolving(false)
          onLog({ type: 'status', entry: { kind: 'evolving_failed', message: event.message }, id: idRef.current++ })
        } else if (event.type === 'library_hit') {
          onLog({ type: 'status', entry: { kind: 'library_hit', items: event.items }, id: idRef.current++ })
        } else if (event.type === 'downloading') {
          onLog({ type: 'status', entry: { kind: 'downloading', items: event.items }, id: idRef.current++ })
        } else if (event.type === 'title_correction') {
          onLog({ type: 'status', entry: { kind: 'title_correction', original: event.original, canonical: event.canonical }, id: idRef.current++ })
        } else if (event.type === 'no_results') {
          onLog({ type: 'status', entry: { kind: 'no_results', message: event.message }, id: idRef.current++ })
        } else if (event.type === 'done') {
          onDone(event.results, event.winner, event.attempts)
          es.close()
        } else if (event.type === 'error') {
          onStreamError()
          es.close()
        }
      } catch {}
    }

    es.onerror = () => {
      es.close()
      esRef.current = null
      onStreamError()
    }
    return es
  }, [onLog, onUpdateRow, onThinking, onEvolving, onDone, onStreamError])

  const close = useCallback(() => {
    esRef.current?.close()
    esRef.current = null
  }, [])

  return { openStream, close }
}

// ─── Main component ───────────────────────────────────────────────────────────

export default function Search() {
  const [query, setQuery]     = useState('')
  const [type, setType]       = useState<MediaType | ''>('')
  const [quality, setQuality] = useState<Quality>('1080p')

  // Primary search state
  const [log, setLog]           = useState<LogEntry[]>([])
  const [thinkingText, setThinkingText]   = useState('')
  const [isEvolving, setIsEvolving]       = useState(false)
  const [searching, setSearching]         = useState(false)
  const [results, setResults]             = useState<SearchResult[]>([])
  const [winner, setWinner]               = useState<string | null>(null)
  const [attempts, setAttempts]           = useState(0)

  // Deeper search state (shown below results)
  const [deepLog, setDeepLog]             = useState<LogEntry[]>([])
  const [deepThinking, setDeepThinking]   = useState('')
  const [deepEvolving, setDeepEvolving]   = useState(false)
  const [deepSearching, setDeepSearching] = useState(false)
  const [deepResults, setDeepResults]     = useState<SearchResult[]>([])

  // ── Season download state ─────────────────────────────────────────────────
  const [seasonRows, setSeasonRows]       = useState<Map<number, SeasonEpRow>>(new Map())
  const [seasonSearching, setSeasonSearching] = useState(false)
  const [seasonTitle, setSeasonTitle]     = useState('')
  const [seasonNum, setSeasonNum]         = useState(0)
  const [seasonSummary, setSeasonSummary] = useState<{ found: number; total: number; missing: number[] } | null>(null)
  const seasonEsRef = useRef<EventSource | null>(null)

  const [adding, setAdding]   = useState<string | null>(null)
  const [added, setAdded]     = useState<Set<string>>(new Set())

  // ── Suggestion state ──────────────────────────────────────────────────────
  const [showSug, setShowSug]               = useState(false)
  const [sugDirect, setSugDirect]           = useState<SuggestResponse | null>(null)
  const [sugExpanded, setSugExpanded]       = useState<SuggestionItem[]>([])
  const [sugLoading, setSugLoading]         = useState(false)
  const [sugExpandLoading, setSugExpandLoading] = useState(false)
  const [tvPicked, setTVPicked]             = useState<SuggestionItem | null>(null)
  const [tvSeasons, setTVSeasons]           = useState<TVSeason[] | null>(null)
  const [tvSeasonLoading, setTVSeasonLoading] = useState(false)
  const tvCacheRef = useRef<Map<number, TVSeason[]>>(new Map())
  const [sugActiveIdx, setSugActiveIdx]     = useState(-1)
  const [epS, setEpS]                       = useState('1')
  const [epE, setEpE]                       = useState('1')
  const suppressSugRef = useRef(false)
  const sugGenRef      = useRef(0)
  const inputWrapRef   = useRef<HTMLDivElement>(null)

  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    if (!searching && !deepSearching && !seasonSearching) return
    const id = setInterval(() => setNow(Date.now()), 500)
    return () => clearInterval(id)
  }, [searching, deepSearching, seasonSearching])

  const logRef     = useRef<HTMLDivElement>(null)
  const deepLogRef = useRef<HTMLDivElement>(null)

  useEffect(() => { if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight }, [log, thinkingText])
  useEffect(() => { if (deepLogRef.current) deepLogRef.current.scrollTop = deepLogRef.current.scrollHeight }, [deepLog, deepThinking])

  // ── Suggestion debounce + fetch ───────────────────────────────────────────
  useEffect(() => {
    if (suppressSugRef.current) { suppressSugRef.current = false; return }
    if (query.length < 2) { setShowSug(false); setSugDirect(null); setSugExpanded([]); return }

    const gen = ++sugGenRef.current
    const timer = setTimeout(async () => {
      setSugLoading(true)
      setSugExpanded([])
      setSugActiveIdx(-1)
      setTVPicked(null)
      setShowSug(true)

      try {
        const data = await searchSuggest(query)
        if (gen !== sugGenRef.current) return
        setSugDirect(data)
        setSugLoading(false)

        // Prefetch season data for all TV results so clicks are instant
        data.tmdb.filter(r => r.media_type === 'tv').forEach(r => {
          if (!tvCacheRef.current.has(r.tmdb_id)) {
            searchSuggestTV(r.tmdb_id).then(d => {
              tvCacheRef.current.set(r.tmdb_id, d.seasons)
            }).catch(() => {})
          }
        })

        // Fire Haiku expand after direct results land
        setSugExpandLoading(true)
        try {
          const { suggestions } = await searchSuggestExpand(query)
          if (gen !== sugGenRef.current) return
          setSugExpanded(suggestions)
          suggestions.filter(r => r.media_type === 'tv').forEach(r => {
            if (!tvCacheRef.current.has(r.tmdb_id)) {
              searchSuggestTV(r.tmdb_id).then(d => {
                tvCacheRef.current.set(r.tmdb_id, d.seasons)
              }).catch(() => {})
            }
          })
        } catch {} finally {
          if (gen === sugGenRef.current) setSugExpandLoading(false)
        }
      } catch {
        if (gen === sugGenRef.current) setSugLoading(false)
      }
    }, 300)

    return () => clearTimeout(timer)
  }, [query])

  // Close dropdown on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (inputWrapRef.current && !inputWrapRef.current.contains(e.target as Node)) {
        setShowSug(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  // Keyboard nav for suggestion dropdown
  const handleSuggestKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (!showSug) return
    if (e.key === 'Escape') { setShowSug(false); return }
    if (e.key === 'ArrowDown') { e.preventDefault(); setSugActiveIdx(i => i + 1) }
    if (e.key === 'ArrowUp')   { e.preventDefault(); setSugActiveIdx(i => Math.max(-1, i - 1)) }
  }, [showSug])

  const handleSelectSuggestion = useCallback((item: SuggestionItem) => {
    if (item.media_type === 'tv') {
      setTVPicked(item)
      const cached = tvCacheRef.current.get(item.tmdb_id)
      if (cached) {
        setTVSeasons(cached)
        setTVSeasonLoading(false)
      } else {
        setTVSeasons(null)
        setTVSeasonLoading(true)
        searchSuggestTV(item.tmdb_id)
          .then(d => { tvCacheRef.current.set(item.tmdb_id, d.seasons); setTVSeasons(d.seasons); setTVSeasonLoading(false) })
          .catch(() => setTVSeasonLoading(false))
      }
    } else {
      suppressSugRef.current = true
      setQuery(item.title)
      setType('movie')
      setShowSug(false)
    }
  }, [])

  const handleSelectTV = useCallback((title: string, season: number | null, episode: number | null) => {
    let q = title
    if (season !== null && episode !== null) {
      q = `${title} S${String(season).padStart(2, '0')}E${String(episode).padStart(2, '0')}`
    } else if (season !== null) {
      q = `${title} season ${season}`
    }
    suppressSugRef.current = true
    setQuery(q)
    setType('tv')
    setShowSug(false)
    setTVPicked(null)
  }, [])

  // ── Restore last search from localStorage ──────────────────────────────────
  useEffect(() => {
    try {
      const saved = localStorage.getItem(STORAGE_KEY)
      if (!saved) return
      const { query: q, type: t, quality: qual, results: r } = JSON.parse(saved)
      if (q) setQuery(q)
      if (t !== undefined) setType(t)
      if (qual) setQuality(qual)
      if (r?.length) setResults(r)
    } catch {}
  }, [])

  // Clear persisted state when query is emptied
  useEffect(() => {
    if (query === '') {
      localStorage.removeItem(STORAGE_KEY)
      setResults([])
      setLog([])
      setDeepResults([])
    }
  }, [query])

  // ── Primary search stream handlers ────────────────────────────────────────
  const appendLog  = useCallback((entry: LogEntry) => setLog(prev => [...prev, entry]), [])
  const updateRow  = useCallback((q: string, state: 'hit' | 'miss', count?: number) => {
    setLog(prev => prev.map(e => e.type === 'query' && e.row.key === q ? { ...e, row: { ...e.row, state, count } } : e))
  }, [])

  const { openStream: openMain, close: closeMain } = useSearchStream(
    appendLog, updateRow,
    setThinkingText, setIsEvolving,
    (res, w, att) => {
      setResults(res)
      setWinner(w)
      setAttempts(att)
      setSearching(false)
      setIsEvolving(false)
      // Persist to localStorage
      try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify({ query, type, quality, results: res }))
      } catch {}
    },
    () => { setSearching(false); setIsEvolving(false) },
  )

  // ── Deeper search stream handlers ─────────────────────────────────────────
  const appendDeepLog = useCallback((entry: LogEntry) => setDeepLog(prev => [...prev, entry]), [])
  const updateDeepRow = useCallback((q: string, state: 'hit' | 'miss', count?: number) => {
    setDeepLog(prev => prev.map(e => e.type === 'query' && e.row.key === q ? { ...e, row: { ...e.row, state, count } } : e))
  }, [])

  const { openStream: openDeeper, close: closeDeeper } = useSearchStream(
    appendDeepLog, updateDeepRow,
    setDeepThinking, setDeepEvolving,
    (res, _w, _att) => {
      setDeepResults(prev => {
        const existing = new Set(prev.map(r => r.info_hash))
        const merged = [...prev, ...res.filter(r => !existing.has(r.info_hash))]
        return merged
      })
      setDeepSearching(false)
      setDeepEvolving(false)
    },
    () => { setDeepSearching(false); setDeepEvolving(false) },
  )

  const doSearch = useCallback((e?: React.FormEvent) => {
    e?.preventDefault()
    if (!query.trim() || searching) return

    closeMain()
    setLog([])
    setThinkingText('')
    setIsEvolving(false)
    setResults([])
    setWinner(null)
    setAttempts(0)
    setDeepLog([])
    setDeepResults([])
    setDeepThinking('')
    setShowSug(false)
    setTVPicked(null)
    setSearching(true)
    setNow(Date.now())

    const params = new URLSearchParams({ q: query, limit: '30' })
    if (type) params.set('type', type)
    params.set('quality', quality)

    openMain(`/api/search/stream?${params}`)
  }, [query, type, quality, searching, closeMain, openMain])

  const doDeeper = useCallback(() => {
    if (deepSearching || !results.length) return

    closeDeeper()
    setDeepLog([])
    setDeepThinking('')
    setDeepEvolving(false)
    setDeepSearching(true)
    setNow(Date.now())

    const allHashes = [...results, ...deepResults].map(r => r.info_hash).join(',')
    const params = new URLSearchParams({ q: query, exclude: allHashes, limit: '20' })
    if (type) params.set('type', type)
    params.set('quality', quality)

    openDeeper(`/api/search/deeper/stream?${params}`)
  }, [query, type, quality, results, deepResults, deepSearching, closeDeeper, openDeeper])

  const doSeasonDownload = useCallback((item: SuggestionItem, season: TVSeason) => {
    seasonEsRef.current?.close()
    setSeasonRows(new Map())
    setSeasonSummary(null)
    setSeasonTitle(item.title)
    setSeasonNum(season.season_number)
    setSeasonSearching(true)
    setShowSug(false)
    setTVPicked(null)

    const params = new URLSearchParams({
      title: item.title,
      season: String(season.season_number),
      episodes: String(season.episode_count),
      quality,
    })
    if (item.tmdb_id) params.set('tmdb_id', String(item.tmdb_id))

    const es = new EventSource(`/api/search/season/stream?${params}`)
    seasonEsRef.current = es

    es.onmessage = (e) => {
      try {
        const ev = JSON.parse(e.data)
        if (ev.type === 'episode_start') {
          setSeasonRows(prev => {
            const next = new Map(prev)
            next.set(ev.episode, { episode: ev.episode, total: ev.total, state: 'searching' })
            return next
          })
        } else if (ev.type === 'episode_found') {
          setSeasonRows(prev => {
            const next = new Map(prev)
            next.set(ev.episode, { episode: ev.episode, total: prev.get(ev.episode)?.total ?? 0, state: 'found', name: ev.torrent_name, duplicate: ev.duplicate })
            return next
          })
        } else if (ev.type === 'episode_missing') {
          setSeasonRows(prev => {
            const next = new Map(prev)
            next.set(ev.episode, { episode: ev.episode, total: 0, state: 'missing' })
            return next
          })
        } else if (ev.type === 'season_done') {
          setSeasonSummary({ found: ev.found, total: ev.total, missing: ev.missing })
          setSeasonSearching(false)
          es.close()
        } else if (ev.type === 'error') {
          setSeasonSearching(false)
          es.close()
        }
      } catch {}
    }
    es.onerror = () => { es.close(); setSeasonSearching(false) }
  }, [quality])

  // Infer media type from TPB category code when not explicitly set
  // 201=Movies, 202=DVDR, 204=Clips, 207=HD Movies, 209=3D → movie
  // 205=TV, 208=HD TV → tv
  const inferMediaType = (category: string): MediaType => {
    if (type && type !== 'unknown') return type
    const cat = parseInt(category, 10)
    if ([201, 202, 204, 207, 209].includes(cat)) return 'movie'
    if ([205, 208].includes(cat)) return 'tv'
    return 'unknown'
  }

  const handleDownload = async (result: SearchResult) => {
    setAdding(result.id)
    try {
      await addDownload({ magnet_url: result.magnet_url, name: result.name, media_type: inferMediaType(result.category) })
      setAdded(prev => new Set([...prev, result.id]))
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : 'Failed to add download')
    } finally {
      setAdding(null)
    }
  }

  const showLog = log.length > 0 || thinkingText.length > 0
  const allResults = [...results, ...deepResults.filter(r => !results.some(r2 => r2.info_hash === r.info_hash))]

  return (
    <div className="cinema-page">
      <section className="cinema-hero min-h-[500px] border-b border-white/[0.08]">
        <div className="absolute inset-0 bg-[radial-gradient(circle_at_22%_8%,rgba(244,193,93,0.20),transparent_28rem),radial-gradient(circle_at_78%_18%,rgba(22,34,54,0.68),transparent_32rem),linear-gradient(180deg,rgba(5,5,7,0.65),rgba(5,5,7,1))]" />
        <div className="cinema-shell">
          <Badge tone="info" className="border-primary/25 bg-primary/10 text-primary-light">
            <SearchIcon size={12} /> Release browser
          </Badge>
          <h1 className="cinema-title">Browse every release like a collector.</h1>
          <p className="cinema-copy">
            Inspect quality, size, seeders, and Sparrow's trace when you want manual control over the next addition to your shelf.
          </p>

          <form onSubmit={doSearch} className="mt-8 max-w-full space-y-3 sm:max-w-5xl">
            <Card className="glass-panel overflow-visible p-2.5 sm:p-2">
              <div className="flex flex-col gap-2 sm:flex-row">
          <div ref={inputWrapRef} className="relative flex-1">
            <SearchIcon size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted pointer-events-none" />
            {query && (
              <button
                type="button"
                className="absolute right-3 top-1/2 -translate-y-1/2 text-muted/50 hover:text-muted transition-colors"
                onClick={() => { suppressSugRef.current = true; setQuery(''); setShowSug(false) }}
              >
                <XCircle size={15} />
              </button>
            )}
            <input
              className={clsx('h-14 w-full rounded-full bg-transparent pl-10 pr-3 text-base text-text outline-none placeholder:text-muted', query && 'pr-8')}
              placeholder="Search a title or release"
              value={query}
              onChange={e => setQuery(e.target.value)}
              onFocus={() => { if (query.length >= 2 && sugDirect) setShowSug(true) }}
              onKeyDown={handleSuggestKeyDown}
              autoFocus
            />
            {showSug && (
              <SuggestionDropdown
                direct={sugDirect}
                expanded={sugExpanded}
                loading={sugLoading}
                expandLoading={sugExpandLoading}
                tvPicked={tvPicked}
                tvSeasons={tvSeasons}
                tvSeasonLoading={tvSeasonLoading}
                activeIdx={sugActiveIdx}
                epS={epS} epE={epE}
                setEpS={setEpS} setEpE={setEpE}
                onSelect={handleSelectSuggestion}
                onSelectTV={handleSelectTV}
                onDownloadSeason={doSeasonDownload}
                onBack={() => setTVPicked(null)}
              />
            )}
          </div>
                <Button type="submit" variant="primary" className="h-14 w-full shrink-0 px-6 sm:w-auto" disabled={searching || !query.trim()}>
                  {searching ? <Loader2 size={14} className="animate-spin" /> : <SearchIcon size={14} />}
                  Search
                </Button>
              </div>
            </Card>
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex items-center gap-1 rounded-full border border-white/10 bg-white/[0.06] p-1 backdrop-blur-xl">
            {(['', 'movie', 'tv'] as const).map(t => (
              <button key={t} type="button" onClick={() => setType(t)}
                className={clsx('flex h-8 items-center gap-1 rounded-full px-3 text-xs font-semibold transition-colors',
                  type === t ? 'bg-primary text-bg' : 'text-muted hover:bg-white/[0.06] hover:text-text')}>
                {t === 'movie' && <Film size={11} />}
                {t === 'tv'    && <Tv size={11} />}
                {t === '' ? 'All' : t === 'movie' ? 'Movies' : 'TV Shows'}
              </button>
            ))}
          </div>
          <div className="flex items-center gap-1 rounded-full border border-white/10 bg-white/[0.06] p-1 backdrop-blur-xl">
            {QUALITY_OPTIONS.map(o => (
              <button key={o.value} type="button" onClick={() => setQuality(o.value)}
                className={clsx('h-8 rounded-full px-3 text-xs font-semibold transition-colors',
                  quality === o.value ? 'bg-primary text-bg' : 'text-muted hover:bg-white/[0.06] hover:text-text')}>
                {o.label}
              </button>
            ))}
          </div>
        </div>
          </form>
        </div>
      </section>

      <main className="w-full max-w-7xl space-y-7 overflow-hidden p-4 sm:p-8">

      {/* Season download progress */}
      {(seasonSearching || seasonRows.size > 0) && (
        <Card className="p-4">
          <div className="flex items-center gap-2 mb-2">
            <Tv size={12} className={clsx(seasonSearching ? 'text-primary animate-pulse' : 'text-muted')} />
            <span className="text-xs font-medium text-text truncate">
              {seasonTitle} - Season {seasonNum}
            </span>
            {seasonSummary ? (
              <span className={clsx('ml-auto text-xs shrink-0', seasonSummary.missing.length === 0 ? 'text-green-400' : 'text-yellow-400')}>
                {seasonSummary.found}/{seasonSummary.total} queued
              </span>
            ) : (
              <span className="ml-auto text-xs text-muted shrink-0">
                {Array.from(seasonRows.values()).filter(r => r.state !== 'searching').length}/{seasonRows.size > 0 ? Array.from(seasonRows.values())[0].total || seasonRows.size : '?'}
              </span>
            )}
          </div>
          <div className="space-y-0.5 max-h-52 overflow-y-auto font-mono">
            {Array.from(seasonRows.values()).map(row => (
              <div key={row.episode} className={clsx('flex items-center gap-2 text-xs py-0.5', {
                'text-muted animate-pulse': row.state === 'searching',
                'text-green-400': row.state === 'found',
                'text-red-400/60': row.state === 'missing',
              })}>
                <Label kind="cwm" />
                {row.state === 'searching' && <Loader2 size={9} className="animate-spin shrink-0" />}
                {row.state === 'found'     && <CheckCircle2 size={9} className="shrink-0" />}
                {row.state === 'missing'   && <XCircle size={9} className="shrink-0" />}
                <span className="text-muted/50 shrink-0">E{String(row.episode).padStart(2, '0')}</span>
                {row.name
                  ? <span className="truncate">{row.name}{row.duplicate ? ' (already queued)' : ''}</span>
                  : row.state === 'missing'
                    ? <span className="text-muted/40">not found</span>
                    : <span className="text-muted/40">searching…</span>
                }
              </div>
            ))}
          </div>
        </Card>
      )}

      {/* Live search log */}
      {showLog && (
        <Card className="p-4">
          <div className="flex items-center justify-between mb-2">
            <div className="flex items-center gap-2">
              <Zap size={12} className={clsx(searching ? 'text-primary animate-pulse' : 'text-muted')} />
              <span className={clsx(
                'text-[10px] font-mono font-semibold px-1.5 py-0.5 rounded',
                isEvolving ? 'bg-primary/20 text-primary-light' : 'bg-muted/10 text-muted'
              )}>
                {isEvolving ? '[claude]' : '[cwm]'}
              </span>
              <span className="text-xs font-medium text-muted">
                {searching
                  ? (isEvolving ? 'Refining the hunt...' : 'Checking release sources...')
                  : winner ? `Best path: ${winner}` : log.length > 0 ? 'No matching release' : ''}
              </span>
              {!searching && attempts > 0 && (
                <span className="text-xs text-muted/60">· {attempts} {attempts === 1 ? 'query' : 'queries'}</span>
              )}
            </div>
          </div>
          <div ref={logRef} className="space-y-0 max-h-48 overflow-y-auto font-mono">
            {log.map((entry, i) =>
              entry.type === 'query'
                ? <QueryRowView key={`q-${entry.row.key}`} row={entry.row} now={now} />
                : <StatusEntryView key={`s-${entry.id}`} entry={entry.entry} />
            )}
            {thinkingText && (
              <div className="mt-1 pl-2 border-l-2 border-primary/30">
                <div className="flex items-start gap-1.5 text-[10px] text-primary-light/70 font-mono leading-relaxed">
                  <Label kind="claude" />
                  <span className="whitespace-pre-wrap">{thinkingText}<span className="animate-pulse">▋</span></span>
                </div>
              </div>
            )}
          </div>
        </Card>
      )}

      {/* Results */}
      {allResults.length > 0 && (
        <div className="space-y-2">
          <SectionHeader
            title="Candidate reels"
            meta={`${allResults.length} results${deepResults.length > 0 ? ` · ${deepResults.length} alternatives` : ''}`}
            icon={<Download size={15} className="text-primary-light" />}
          />
          {allResults.map(r => {
            const isAdded  = added.has(r.id)
            const isAdding = adding === r.id
            const isAlt    = deepResults.some(d => d.info_hash === r.info_hash) && !results.some(r2 => r2.info_hash === r.info_hash)
            return (
              <ResultCard
                key={r.id}
                result={r}
                isAlt={isAlt}
                isAdded={isAdded}
                isAdding={isAdding}
                onDownload={() => !isAdded && handleDownload(r)}
              />
            )
          })}

          {/* Find alternatives */}
          {!searching && (
            <div className="pt-2">
              {(deepLog.length > 0 || deepThinking) && (
                  <Card className="mb-3 p-4">
                  <div className="flex items-center gap-2 mb-2">
                    <Zap size={12} className={clsx(deepSearching ? 'text-primary animate-pulse' : 'text-muted')} />
                    <span className={clsx(
                      'text-[10px] font-mono font-semibold px-1.5 py-0.5 rounded',
                      deepEvolving ? 'bg-primary/20 text-primary-light' : 'bg-muted/10 text-muted'
                    )}>
                      {deepEvolving ? '[claude]' : '[claude]'}
                    </span>
                    <span className="text-xs text-muted">
                      {deepSearching ? 'Looking for alternate cuts...' : `${deepResults.length} alternate cuts`}
                    </span>
                  </div>
                  <div ref={deepLogRef} className="space-y-0 max-h-32 overflow-y-auto font-mono">
                    {deepLog.map((entry, i) =>
                      entry.type === 'query'
                        ? <QueryRowView key={`dq-${entry.row.key}`} row={entry.row} now={now} />
                        : <StatusEntryView key={`ds-${entry.id}`} entry={entry.entry} />
                    )}
                    {deepThinking && (
                      <div className="mt-1 pl-2 border-l-2 border-primary/30">
                        <div className="flex items-start gap-1.5 text-[10px] text-primary-light/70 font-mono leading-relaxed">
                          <Label kind="claude" />
                          <span className="whitespace-pre-wrap">{deepThinking}<span className="animate-pulse">▋</span></span>
                        </div>
                      </div>
                    )}
                  </div>
                </Card>
              )}

              <Button
                variant="ghost"
                size="sm"
                onClick={doDeeper}
                disabled={deepSearching}
              >
                {deepSearching
                  ? <><Loader2 size={12} className="animate-spin" /> Looking...</>
                  : <><RefreshCw size={12} /> Alternate cuts</>
                }
              </Button>
            </div>
          )}
        </div>
      )}

      {/* Empty state */}
      {!searching && log.length === 0 && results.length === 0 && (
        <div className="glass-panel relative overflow-hidden p-10 text-center text-muted">
          <div className="absolute inset-0 bg-[radial-gradient(circle_at_center,rgba(244,193,93,0.10),transparent_24rem)]" />
          <div className="relative">
            <SearchIcon size={40} className="mx-auto mb-3 text-primary-light/40" />
            <p className="text-base font-semibold text-text">Collector mode is waiting</p>
            <p className="mx-auto mt-2 max-w-md text-sm text-muted">
              Search when you want to inspect the release pool by hand. Autopilot requests still live on Downloads.
            </p>
          </div>
        </div>
      )}
      </main>
    </div>
  )
}
