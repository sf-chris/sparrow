import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Check, ChevronDown, Clock, Download, Film, Loader2, Search, Sparkles, Star, Tv, X,
} from 'lucide-react'
import clsx from 'clsx'
import {
  ApiError, createJob, getJobs, getLibrary, resolveSearch, TMDB_POSTER_BASE,
} from '../api/client'
import type { CreateJobPayload } from '../api/client'
import type { Job, JobUrgency, LibraryItem, ResolveCard } from '../types'
import { useWebSocket } from '../hooks/useWebSocket'
import { Badge, Button, Card, SectionHeader } from '../components/ui'

const QUALITY_OPTIONS = [
  { value: '', label: 'Best available' },
  { value: '2160p', label: '4K' },
  { value: '1080p', label: 'Full HD' },
  { value: '720p', label: 'HD' },
] as const

const MIN_QUALITY_OPTIONS = [
  { value: '', label: 'Anything watchable' },
  { value: '1080p', label: 'Full HD or better' },
  { value: '720p', label: 'HD or better' },
] as const

const AUDIO_OPTIONS = [
  { value: 'any', label: 'Any audio' },
  { value: 'english', label: 'English' },
  { value: 'original', label: 'Original language' },
] as const

const URGENCY_OPTIONS: Array<{ value: JobUrgency; label: string }> = [
  { value: 'tonight', label: 'Tonight' },
  { value: 'soon', label: 'Soon' },
  { value: 'whenever', label: 'No rush' },
]

type Knobs = {
  preferred_quality: string
  min_quality: string
  audio_pref: string
  urgency: JobUrgency | ''
}

const DEFAULT_KNOBS: Knobs = { preferred_quality: '', min_quality: '', audio_pref: 'any', urgency: '' }

function KnobRow<T extends string>({
  label,
  options,
  value,
  onChange,
}: {
  label: string
  options: ReadonlyArray<{ value: T; label: string }>
  value: string
  onChange: (value: T) => void
}) {
  return (
    <div className="min-w-0">
      <p className="text-[11px] font-semibold uppercase tracking-wide text-muted/70">{label}</p>
      <div className="mt-1.5 flex flex-wrap gap-1.5">
        {options.map(option => (
          <button
            key={option.value}
            type="button"
            onClick={() => onChange(option.value)}
            className={clsx(
              'rounded-full border px-2.5 py-1 text-[11px] font-semibold transition-colors',
              value === option.value
                ? 'border-primary/40 bg-primary/15 text-primary-light'
                : 'border-white/10 bg-white/5 text-muted hover:text-text',
            )}
          >
            {option.label}
          </button>
        ))}
      </div>
    </div>
  )
}

function CardPoster({ card }: { card: ResolveCard }) {
  if (card.poster_url) {
    return <img src={card.poster_url} alt={card.title} loading="lazy" className="h-full w-full object-cover" />
  }
  return (
    <div className="flex h-full w-full flex-col items-center justify-center gap-3 bg-gradient-to-b from-card to-panel px-4 text-center">
      {card.media_type === 'tv' ? <Tv size={28} className="text-primary/50" /> : <Film size={28} className="text-primary/50" />}
      <span className="line-clamp-3 text-sm font-semibold leading-snug text-text/70">{card.title}</span>
    </div>
  )
}

function LibraryPoster({ item }: { item: LibraryItem }) {
  if (item.poster_path) {
    return <img src={item.poster_path} alt={item.title} loading="lazy" className="h-full w-full object-cover" />
  }
  return (
    <div className="flex h-full w-full flex-col items-center justify-center gap-3 bg-gradient-to-b from-card to-panel px-4 text-center">
      {item.media_type === 'tv' ? <Tv size={28} className="text-primary/50" /> : <Film size={28} className="text-primary/50" />}
      <span className="line-clamp-3 text-sm font-semibold leading-snug text-text/70">{item.title}</span>
    </div>
  )
}

function ResolveCardTile({
  card,
  onGet,
  getting,
  gotten,
}: {
  card: ResolveCard
  onGet: (card: ResolveCard, knobs: Knobs) => void
  getting: boolean
  gotten: boolean
}) {
  const navigate = useNavigate()
  const [optionsOpen, setOptionsOpen] = useState(false)
  const [knobs, setKnobs] = useState<Knobs>(DEFAULT_KNOBS)

  const hasJob = Boolean(card.active_job)
  const posterClickable = true

  const openShow = () => navigate(`/show/${card.tmdb_id}?type=${card.media_type}`)

  return (
    <div className="group min-w-0">
      <button
        type="button"
        className={clsx('block w-full text-left', !posterClickable && 'cursor-default')}
        onClick={() => posterClickable && openShow()}
      >
        <div className={clsx(
          'poster-surface relative aspect-[2/3] transition-all duration-300',
          posterClickable && 'group-hover:-translate-y-1 group-hover:scale-[1.018] group-hover:border-primary/35',
        )}>
          <CardPoster card={card} />
          {card.in_library && (
            <span
              title="In your library"
              className="absolute right-1.5 top-1.5 flex h-6 w-6 items-center justify-center rounded-full border border-emerald-400/25 bg-bg/80 text-emerald-300 backdrop-blur"
            >
              <Check size={12} />
            </span>
          )}
        </div>
      </button>

      <div className="mt-3 min-w-0">
        <p className="line-clamp-2 text-sm font-semibold leading-snug text-text">{card.title}</p>
        <p className="mt-0.5 truncate text-xs text-muted">
          {card.year ? `${card.year} · ` : ''}
          {card.media_type === 'tv' ? 'Show' : 'Movie'}
          {card.rating > 0 && (
            <span className="ml-1.5 inline-flex items-center gap-0.5 text-amber-200">
              <Star size={10} fill="currentColor" /> {card.rating.toFixed(1)}
            </span>
          )}
        </p>

        <div className="mt-2 space-y-2">
          {hasJob ? (
            <button type="button" className="block w-full text-left" onClick={openShow}>
              <Badge tone="info" className="max-w-full">
                <Sparkles size={11} className="shrink-0" />
                <span className="line-clamp-2 whitespace-normal text-left">
                  {card.active_job!.state_line || 'Sparrow is on it'}
                </span>
              </Badge>
            </button>
          ) : gotten ? (
            <Badge tone="success" className="max-w-full">
              <Sparkles size={11} className="shrink-0" /> Sparrow is on it
            </Badge>
          ) : (
            <>
              <div className="flex items-center gap-2">
                <Button
                  variant="primary"
                  size="sm"
                  disabled={getting}
                  onClick={() => onGet(card, knobs)}
                >
                  {getting ? <Loader2 size={13} className="animate-spin" /> : <Download size={13} />}
                  Get
                </Button>
                <button
                  type="button"
                  aria-label="Options"
                  className="inline-flex items-center gap-1 text-xs text-muted transition-colors hover:text-text"
                  onClick={() => setOptionsOpen(prev => !prev)}
                >
                  Options
                  <ChevronDown size={12} className={clsx('transition-transform', optionsOpen && 'rotate-180')} />
                </button>
              </div>
              {optionsOpen && (
                <div className="space-y-3 rounded-2xl border border-white/10 bg-panel/70 p-3">
                  <KnobRow
                    label="Quality"
                    options={QUALITY_OPTIONS}
                    value={knobs.preferred_quality}
                    onChange={value => setKnobs(prev => ({ ...prev, preferred_quality: value }))}
                  />
                  <KnobRow
                    label="At least"
                    options={MIN_QUALITY_OPTIONS}
                    value={knobs.min_quality}
                    onChange={value => setKnobs(prev => ({ ...prev, min_quality: value }))}
                  />
                  <KnobRow
                    label="Audio"
                    options={AUDIO_OPTIONS}
                    value={knobs.audio_pref}
                    onChange={value => setKnobs(prev => ({ ...prev, audio_pref: value }))}
                  />
                  <KnobRow
                    label="How soon"
                    options={URGENCY_OPTIONS}
                    value={knobs.urgency}
                    onChange={value => setKnobs(prev => ({ ...prev, urgency: value }))}
                  />
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  )
}

function jobPosterUrl(job: Job): string | null {
  return job.poster_path ? `${TMDB_POSTER_BASE}${job.poster_path}` : null
}

export default function Home() {
  const navigate = useNavigate()

  const [query, setQuery] = useState('')
  const [cards, setCards] = useState<ResolveCard[]>([])
  const [searching, setSearching] = useState(false)

  const [jobs, setJobs] = useState<Job[]>([])
  const [library, setLibrary] = useState<LibraryItem[]>([])
  const [loading, setLoading] = useState(true)

  const [gettingId, setGettingId] = useState<string | null>(null)
  const [gottenIds, setGottenIds] = useState<Set<string>>(new Set())
  const [getError, setGetError] = useState('')

  // Initial load
  useEffect(() => {
    Promise.allSettled([getJobs('active'), getLibrary()]).then(([activeJobs, lib]) => {
      if (activeJobs.status === 'fulfilled') setJobs(activeJobs.value)
      if (lib.status === 'fulfilled') setLibrary(lib.value)
      setLoading(false)
    })
  }, [])

  // Debounced resolution search
  useEffect(() => {
    const q = query.trim()
    if (!q) {
      setCards([])
      setSearching(false)
      return
    }
    setSearching(true)
    let cancelled = false
    const timer = setTimeout(async () => {
      try {
        const results = await resolveSearch(q)
        if (!cancelled) setCards(results)
      } catch {
        if (!cancelled) setCards([])
      } finally {
        if (!cancelled) setSearching(false)
      }
    }, 350)
    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [query])

  // Throttled live refresh via websocket
  const refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  useEffect(() => () => {
    if (refreshTimer.current) clearTimeout(refreshTimer.current)
  }, [])
  useWebSocket((event) => {
    const type = event.type as string
    if (type !== 'job_added' && type !== 'job_update' && type !== 'library_update') return
    if (refreshTimer.current) return
    refreshTimer.current = setTimeout(() => {
      refreshTimer.current = null
      getJobs('active').then(setJobs).catch(() => {})
      getLibrary().then(setLibrary).catch(() => {})
    }, 1200)
  })

  const recentlyAdded = useMemo(
    () => [...library].sort((a, b) => (b.added_at || 0) - (a.added_at || 0)).slice(0, 12),
    [library],
  )

  const handleGet = async (card: ResolveCard, knobs: Knobs) => {
    const cardKey = `${card.media_type}-${card.tmdb_id}`
    setGettingId(cardKey)
    setGetError('')
    try {
      const payload: CreateJobPayload = {
        tmdb_id: card.tmdb_id,
        media_type: card.media_type,
      }
      if (knobs.preferred_quality) payload.preferred_quality = knobs.preferred_quality
      if (knobs.min_quality) payload.min_quality = knobs.min_quality
      if (knobs.audio_pref && knobs.audio_pref !== 'any') payload.audio_pref = knobs.audio_pref
      if (knobs.urgency) payload.urgency = knobs.urgency
      await createJob(payload)
      setGottenIds(prev => new Set(prev).add(cardKey))
      getJobs('active').then(setJobs).catch(() => {})
    } catch (e: unknown) {
      if (e instanceof ApiError && e.status === 409) {
        // Sparrow is already working on this one — show the workspace.
        navigate(`/show/${card.tmdb_id}?type=${card.media_type}`)
        return
      }
      setGetError('Something went wrong — please try again')
    } finally {
      setGettingId(null)
    }
  }

  const openLibraryItem = (item: LibraryItem) => {
    if (item.tmdb_id && item.media_type !== 'unknown') {
      navigate(`/show/${item.tmdb_id}?type=${item.media_type}`)
    }
    else navigate('/library')
  }

  const showingSearch = query.trim().length > 0

  return (
    <div className="min-h-screen w-full">
      <main className="mx-auto w-full max-w-7xl p-4 pt-24 sm:p-6 sm:pt-28 lg:p-8 lg:pt-28">
        {/* Hero search */}
        <section className="mx-auto max-w-3xl py-8 text-center sm:py-12">
          <h1 className="text-3xl font-semibold tracking-tight text-text sm:text-5xl">
            What do you want to watch?
          </h1>
          <p className="mt-3 text-sm text-muted sm:text-base">
            A title works. So does “the show where the teacher cooks meth”.
          </p>
          <div className="relative mt-6">
            <Search size={18} className="absolute left-5 top-1/2 -translate-y-1/2 text-muted" />
            <input
              className="input h-14 pl-12 pr-12 text-base"
              value={query}
              onChange={event => setQuery(event.target.value)}
              placeholder="Try “Bluey” or “Paddington”…"
              autoFocus
            />
            {query && (
              <button
                type="button"
                aria-label="Clear search"
                className="absolute right-4 top-1/2 -translate-y-1/2 text-muted transition-colors hover:text-text"
                onClick={() => setQuery('')}
              >
                <X size={16} />
              </button>
            )}
          </div>
        </section>

        {showingSearch ? (
          <section>
            {getError && <p className="mb-4 text-center text-sm text-rose-300">{getError}</p>}
            {searching && cards.length === 0 ? (
              <div className="flex items-center justify-center gap-2 py-10 text-sm text-muted">
                <Loader2 size={14} className="animate-spin" /> Searching…
              </div>
            ) : cards.length === 0 ? (
              <div className="rounded-2xl border border-dashed border-white/[0.12] bg-panel/50 p-10 text-center">
                <Search size={32} className="mx-auto text-muted/30" />
                <p className="mt-3 text-sm font-medium text-text">Nothing found for “{query.trim()}”</p>
                <p className="mt-1 text-sm text-muted">Check the spelling or try describing it differently.</p>
              </div>
            ) : (
              <div className={clsx(
                'grid grid-cols-2 gap-x-4 gap-y-7 transition-opacity sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6',
                searching && 'opacity-60',
              )}>
                {cards.map(card => (
                  <ResolveCardTile
                    key={`${card.media_type}-${card.tmdb_id}`}
                    card={card}
                    onGet={handleGet}
                    getting={gettingId === `${card.media_type}-${card.tmdb_id}`}
                    gotten={gottenIds.has(`${card.media_type}-${card.tmdb_id}`)}
                  />
                ))}
              </div>
            )}
          </section>
        ) : loading ? (
          <div className="flex items-center justify-center py-16">
            <div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" />
          </div>
        ) : (
          <div className="space-y-10">
            {jobs.length > 0 && (
              <section>
                <SectionHeader
                  title="In progress"
                  icon={<Clock size={14} className="text-primary-light" />}
                  meta={`${jobs.length} item${jobs.length === 1 ? '' : 's'}`}
                />
                <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                  {jobs.map(job => (
                    <Card
                      key={job.id}
                      className="cursor-pointer p-4 transition-colors hover:border-primary/35"
                      onClick={() => navigate(`/show/${job.tmdb_id}?type=${job.media_type}`)}
                    >
                      <div className="flex items-start gap-3">
                        {jobPosterUrl(job) && (
                          <img
                            src={jobPosterUrl(job)!}
                            alt=""
                            loading="lazy"
                            className="h-16 w-11 shrink-0 rounded-lg object-cover"
                          />
                        )}
                        <div className="min-w-0 flex-1">
                          <div className="flex items-start justify-between gap-3">
                            <p className="min-w-0 break-words text-sm font-semibold text-text">{job.title}</p>
                            <Badge tone="neutral" className="shrink-0">
                              {job.media_type === 'tv' ? 'Show' : 'Movie'}
                            </Badge>
                          </div>
                          <p className="mt-2 text-xs text-muted">
                            {job.state_line || 'Working on it…'}
                          </p>
                        </div>
                      </div>
                    </Card>
                  ))}
                </div>
              </section>
            )}

            <section>
              <SectionHeader
                title="Recently added"
                icon={<Sparkles size={14} className="text-primary-light" />}
              />
              {recentlyAdded.length === 0 ? (
                <div className="rounded-2xl border border-dashed border-white/[0.12] bg-panel/50 p-10 text-center">
                  <Film size={32} className="mx-auto text-muted/30" />
                  <p className="mt-3 text-sm font-medium text-text">Nothing here yet</p>
                  <p className="mt-1 text-sm text-muted">Search above for something to watch and it will show up here.</p>
                </div>
              ) : (
                <div className="grid grid-cols-2 gap-x-4 gap-y-7 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6">
                  {recentlyAdded.map(item => (
                    <button
                      key={item.id}
                      type="button"
                      className="group min-w-0 text-left"
                      onClick={() => openLibraryItem(item)}
                    >
                      <div className="poster-surface relative aspect-[2/3] transition-all duration-300 group-hover:-translate-y-1 group-hover:scale-[1.018] group-hover:border-primary/35">
                        <LibraryPoster item={item} />
                      </div>
                      <div className="mt-3 min-w-0">
                        <p className="truncate text-sm font-semibold text-text">{item.title}</p>
                        <p className="mt-0.5 truncate text-xs text-muted">
                          {item.year ? `${item.year} · ` : ''}
                          {item.media_type === 'tv' ? 'Show' : 'Movie'}
                        </p>
                      </div>
                    </button>
                  ))}
                </div>
              )}
            </section>
          </div>
        )}
      </main>
    </div>
  )
}
