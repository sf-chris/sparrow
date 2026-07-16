import { useEffect, useMemo, useState } from 'react'
import {
  ArrowRight, Check, Database, DownloadCloud, FolderOpen, Gauge, KeyRound,
  Loader2, Play, RefreshCw, SearchCheck, ShieldCheck, Sparkles, Tv, Wand2,
} from 'lucide-react'
import clsx from 'clsx'
import {
  discoverClients, getConfig, getHealth, runHealthAction, testClient, updateConfig,
} from '../api/client'
import type { Config, Quality, TorrentClientInfo } from '../types'
import { Badge, Button, Card, Progress, SectionHeader } from '../components/ui'

interface Props {
  onComplete: () => void
}

type Health = Awaited<ReturnType<typeof getHealth>>

function Field({
  label,
  children,
  hint,
}: {
  label: string
  children: React.ReactNode
  hint?: string
}) {
  return (
    <label className="block min-w-0">
      <span className="mb-1 block text-xs font-medium text-muted">{label}</span>
      {children}
      {hint && <span className="mt-1 block break-words text-xs text-muted">{hint}</span>}
    </label>
  )
}

function Toggle({
  checked,
  onChange,
  title,
  description,
}: {
  checked: boolean
  onChange: () => void
  title: string
  description: string
}) {
  return (
    <button
      type="button"
      onClick={onChange}
      className="flex w-full min-w-0 items-center justify-between gap-3 rounded-md border border-white/10 bg-bg/40 p-3 text-left transition-colors hover:bg-white/[0.04]"
    >
      <span className="min-w-0">
        <span className="block text-sm font-medium text-text">{title}</span>
        <span className="mt-0.5 block text-xs leading-relaxed text-muted">{description}</span>
      </span>
      <span className={clsx(
        'relative h-5 w-10 shrink-0 rounded-full transition-colors',
        checked ? 'bg-primary' : 'bg-border',
      )}>
        <span className={clsx(
          'absolute left-0.5 top-0.5 h-4 w-4 rounded-full bg-bg shadow transition-transform',
          checked && 'translate-x-5',
        )} />
      </span>
    </button>
  )
}

function ReadinessItem({
  done,
  title,
  detail,
}: {
  done: boolean
  title: string
  detail: string
}) {
  return (
    <div className="flex items-start gap-3 rounded-md border border-white/10 bg-bg/35 p-3">
      <div className={clsx(
        'mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-md',
        done ? 'bg-primary text-bg' : 'bg-white/5 text-muted',
      )}>
        {done ? <Check size={14} /> : <ArrowRight size={14} />}
      </div>
      <div className="min-w-0">
        <p className="text-sm font-medium text-text">{title}</p>
        <p className="mt-0.5 text-xs leading-relaxed text-muted">{detail}</p>
      </div>
    </div>
  )
}

export default function Onboarding({ onComplete }: Props) {
  const [config, setConfig] = useState<Config | null>(null)
  const [health, setHealth] = useState<Health | null>(null)
  const [clients, setClients] = useState<TorrentClientInfo[]>([])
  const [selectedClient, setSelectedClient] = useState<TorrentClientInfo | null>(null)
  const [discovering, setDiscovering] = useState(false)
  const [testing, setTesting] = useState(false)
  const [saving, setSaving] = useState(false)
  const [fixing, setFixing] = useState<string | null>(null)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    Promise.all([getConfig(), getHealth()])
      .then(([cfg, healthData]) => {
        setConfig({
          ...cfg,
          staging_dir: cfg.staging_dir || '~/Sparrow/Temp',
          library_dir: cfg.library_dir || '~/Sparrow/Library',
          quality_preference: cfg.quality_preference || '1080p',
          prefer_season_packs: cfg.prefer_season_packs ?? true,
          auto_organize: cfg.auto_organize ?? true,
        })
        setHealth(healthData)
      })
      .catch(e => setError(e instanceof Error ? e.message : 'Could not load setup'))
  }, [])

  const readiness = useMemo(() => {
    if (!config) return { count: 0, total: 6, pct: 0 }
    const items = [
      Boolean(config.staging_dir.trim()),
      Boolean(config.library_dir.trim()),
      selectedClient !== null || config.torrent_client.type !== 'none',
      Boolean(config.quality_preference),
      Boolean(config.tmdb_api_key.trim()) || Boolean(config.tmdb_api_key_configured),
      Boolean(config.anthropic_api_key.trim()) || Boolean(config.anthropic_api_key_configured),
    ]
    const count = items.filter(Boolean).length
    return { count, total: items.length, pct: Math.round((count / items.length) * 100) }
  }, [config, selectedClient])

  const update = (patch: Partial<Config>) => setConfig(prev => prev ? { ...prev, ...patch } : prev)
  const updateTC = (patch: Partial<Config['torrent_client']>) =>
    setConfig(prev => prev ? { ...prev, torrent_client: { ...prev.torrent_client, ...patch } } : prev)

  const discover = async () => {
    setDiscovering(true)
    setError('')
    setMessage('')
    try {
      const found = await discoverClients()
      setClients(found)
      if (found.length > 0) {
        setSelectedClient(found[0])
        updateTC({ type: found[0].type, host: found[0].host, port: found[0].port })
      } else {
        setMessage('No running client was found. You can still enter the connection manually.')
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Discovery failed')
    } finally {
      setDiscovering(false)
    }
  }

  const checkConnection = async () => {
    if (!config) return
    setTesting(true)
    setError('')
    setMessage('')
    try {
      const result = await testClient(config.torrent_client)
      setMessage(result.reachable
        ? `Connected to ${result.type}${result.version ? ` ${result.version}` : ''}.`
        : 'Could not reach the client yet. Sparrow will keep surfacing setup help.')
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Connection test failed')
    } finally {
      setTesting(false)
    }
  }

  const applyFix = async (issueId: string) => {
    setFixing(issueId)
    setError('')
    setMessage('')
    try {
      const result = await runHealthAction(issueId)
      setHealth(result.health)
      setMessage(result.message)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Could not apply fix')
    } finally {
      setFixing(null)
    }
  }

  const finish = async () => {
    if (!config) return
    setSaving(true)
    setError('')
    try {
      const torrentClient = selectedClient
        ? { ...config.torrent_client, type: selectedClient.type, host: selectedClient.host, port: selectedClient.port }
        : config.torrent_client
      await updateConfig({
        staging_dir: config.staging_dir,
        library_dir: config.library_dir,
        torrent_client: torrentClient,
        quality_preference: config.quality_preference,
        tmdb_api_key: config.tmdb_api_key,
        anthropic_api_key: config.anthropic_api_key,
        auto_organize: config.auto_organize,
        prefer_season_packs: config.prefer_season_packs,
        prefer_smaller_files: config.prefer_smaller_files,
        season_pack_size_limit_gb: config.season_pack_size_limit_gb,
        onboarding_complete: true,
      })
      onComplete()
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to save setup')
    } finally {
      setSaving(false)
    }
  }

  if (!config) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-bg text-sm text-muted">
        <Loader2 size={16} className="mr-2 animate-spin" /> Loading setup...
      </div>
    )
  }

  const canStart = readiness.count === readiness.total
  const healthIssues = health?.issues ?? []

  return (
    <div className="cinema-page w-full overflow-x-hidden">
      <section className="cinema-hero min-h-[620px] border-b border-white/[0.08]">
        <div className="absolute inset-0 bg-[radial-gradient(circle_at_18%_8%,rgba(244,193,93,0.22),transparent_30rem),radial-gradient(circle_at_78%_18%,rgba(194,90,46,0.18),transparent_30rem),linear-gradient(180deg,rgba(5,5,7,0.52),rgba(5,5,7,1))]" />
        <div className="cinema-shell mx-auto">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-full bg-primary text-base font-black text-bg shadow-glow sm:h-11 sm:w-11 sm:text-lg">S</div>
            <div>
              <h1 className="text-xl font-semibold tracking-tight text-text">Sparrow</h1>
              <p className="text-xs text-muted">One local autopilot for requests, downloads, and cleanup.</p>
            </div>
          </div>

          <div className="mt-6 grid gap-5 sm:mt-8 lg:grid-cols-[minmax(0,1.08fr)_minmax(320px,0.92fr)] lg:items-end">
            <div className="min-w-0">
              <Badge tone="info" className="cinema-kicker border-primary/25 bg-primary/10 text-primary-light">
                <Sparkles size={12} /> Consumer setup
              </Badge>
              <h2 className="cinema-title max-w-4xl">
                Build your private streaming room.
              </h2>
              <p className="cinema-copy">
                Point Sparrow at your downloader and library once. After that, a single request becomes release selection, handoff, organization, and verification.
              </p>
              <div className="mt-5 hidden max-w-3xl gap-3 sm:grid sm:grid-cols-3">
                {[
                  ['Season packs first', 'Fallback only when needed', Tv],
                  ['Verify before grab', 'Checks what a result actually contains', SearchCheck],
                  ['Self-healing setup', 'Fixes and clear guidance', ShieldCheck],
                ].map(([title, detail, Icon]) => (
                  <Card key={title as string} className="p-4">
                    <Icon size={16} className="text-primary-light" />
                    <p className="mt-2 text-sm font-medium text-text">{title as string}</p>
                    <p className="mt-0.5 text-xs text-muted">{detail as string}</p>
                  </Card>
                ))}
              </div>
            </div>

            <Card className="glass-panel p-4">
              <div className="flex items-center justify-between gap-4">
                <div>
                  <p className="text-sm font-semibold text-text">Setup readiness</p>
                  <p className="mt-0.5 text-xs text-muted">{readiness.count}/{readiness.total} essentials configured</p>
                </div>
                <Badge tone={canStart ? 'success' : 'warning'}>{canStart ? 'Ready' : 'Needs setup'}</Badge>
              </div>
              <Progress value={readiness.pct} className="mt-4" />
              <div className="mt-4 grid gap-2">
                <ReadinessItem done={Boolean(config.staging_dir.trim())} title="Staging folder" detail="Where downloads land before cleanup." />
                <ReadinessItem done={Boolean(config.library_dir.trim())} title="Library folder" detail="Where ready movies and shows are arranged." />
                <ReadinessItem done={selectedClient !== null || config.torrent_client.type !== 'none'} title="Torrent client" detail="Transmission or qBittorrent connection." />
                <ReadinessItem done={Boolean(config.quality_preference)} title="Release policy" detail="Quality and season-pack preferences." />
                <ReadinessItem done={Boolean(config.tmdb_api_key.trim()) || Boolean(config.tmdb_api_key_configured)} title="Metadata service" detail="TMDB establishes the title and episode contract." />
                <ReadinessItem done={Boolean(config.anthropic_api_key.trim()) || Boolean(config.anthropic_api_key_configured)} title="Agent service" detail="Powers Sparrow's persistent reasoning loops." />
              </div>
            </Card>
          </div>
        </div>
      </section>

      <main className="mx-auto grid max-w-6xl gap-5 p-4 sm:p-6 lg:grid-cols-[minmax(0,1fr)_minmax(320px,0.85fr)]">
        <div className="space-y-5">
          <section>
            <SectionHeader title="Folders" icon={<FolderOpen size={15} className="text-primary-light" />} />
            <Card className="grid gap-4 p-4 sm:grid-cols-2">
              <Field label="Temporary downloads" hint="Sparrow can create this folder from the health panel after setup.">
                <input className="input" value={config.staging_dir} onChange={e => update({ staging_dir: e.target.value })} />
              </Field>
              <Field label="Clean media library" hint="Movies and TV Shows folders live under this path.">
                <input className="input" value={config.library_dir} onChange={e => update({ library_dir: e.target.value })} />
              </Field>
            </Card>
          </section>

          <section>
            <SectionHeader title="Release policy" icon={<Gauge size={15} className="text-primary-light" />} />
            <Card className="grid gap-4 p-4 lg:grid-cols-2">
              <div className="space-y-3">
                <Toggle
                  checked={config.prefer_season_packs}
                  onChange={() => update({ prefer_season_packs: !config.prefer_season_packs })}
                  title="Prefer season packs"
                  description="Try complete-season releases first, then use individual episodes only when the pack is missing or unsuitable."
                />
                <Toggle
                  checked={config.auto_organize}
                  onChange={() => update({ auto_organize: !config.auto_organize })}
                  title="Organize silently"
                  description="Move completed files automatically and verify the library result after the move."
                />
              </div>
              <div className="grid gap-4">
                <Field label="Preferred quality">
                  <select
                    className="input"
                    value={config.quality_preference}
                    onChange={e => update({ quality_preference: e.target.value as Quality })}
                  >
                    <option value="2160p">4K (2160p)</option>
                    <option value="1080p">1080p</option>
                    <option value="720p">720p</option>
                    <option value="480p">480p</option>
                    <option value="any">Any</option>
                  </select>
                </Field>
                <Field label="Season pack cap" hint="0 lets Sparrow estimate a sane size from quality and episode count.">
                  <div className="flex min-w-0 items-center gap-2">
                    <input
                      className="input"
                      type="number"
                      min="0"
                      step="0.5"
                      value={config.season_pack_size_limit_gb}
                      onChange={e => update({ season_pack_size_limit_gb: parseFloat(e.target.value) || 0 })}
                    />
                    <span className="text-sm text-muted">GB</span>
                  </div>
                </Field>
              </div>
            </Card>
          </section>

          <section>
            <SectionHeader title="Torrent handoff" icon={<DownloadCloud size={15} className="text-primary-light" />} />
            <Card className="space-y-4 p-4">
              <div className="flex flex-col justify-between gap-3 sm:flex-row sm:items-center">
                <div>
                  <p className="text-sm font-medium text-text">Connect a local client</p>
                  <p className="mt-0.5 text-xs text-muted">Sparrow queues magnets into Transmission or qBittorrent.</p>
                </div>
                <div className="flex flex-wrap gap-2">
                  <Button variant="secondary" size="sm" onClick={discover} disabled={discovering}>
                    {discovering ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />}
                    Auto-detect
                  </Button>
                  <Button variant="secondary" size="sm" onClick={checkConnection} disabled={testing}>
                    {testing ? <Loader2 size={13} className="animate-spin" /> : <Wand2 size={13} />}
                    Test
                  </Button>
                </div>
              </div>

              {clients.length > 0 && (
                <div className="grid gap-2 sm:grid-cols-2">
                  {clients.map(client => (
                    <button
                      key={`${client.type}-${client.host}-${client.port}`}
                      type="button"
                      onClick={() => {
                        setSelectedClient(client)
                        updateTC({ type: client.type, host: client.host, port: client.port })
                      }}
                      className={clsx(
                        'rounded-md border px-3 py-2 text-left text-sm transition-colors',
                        selectedClient?.type === client.type && selectedClient?.port === client.port
                          ? 'border-primary/40 bg-primary/10 text-primary-light'
                          : 'border-white/10 bg-bg/40 text-muted hover:border-primary/25 hover:text-text',
                      )}
                    >
                      <span className="block font-medium capitalize">{client.type}</span>
                      <span className="text-xs">{client.host}:{client.port}</span>
                    </button>
                  ))}
                </div>
              )}

              <div className="grid gap-4 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_8rem]">
                <Field label="Type">
                  <select
                    className="input"
                    value={config.torrent_client.type}
                    onChange={e => updateTC({ type: e.target.value as Config['torrent_client']['type'] })}
                  >
                    <option value="none">None yet</option>
                    <option value="transmission">Transmission</option>
                    <option value="qbittorrent">qBittorrent</option>
                  </select>
                </Field>
                <Field label="Host">
                  <input className="input" value={config.torrent_client.host} onChange={e => updateTC({ host: e.target.value })} />
                </Field>
                <Field label="Port">
                  <input className="input" type="number" value={config.torrent_client.port} onChange={e => updateTC({ port: parseInt(e.target.value) || 0 })} />
                </Field>
              </div>
            </Card>
          </section>
        </div>

        <aside className="space-y-5">
          <section>
            <SectionHeader title="Agent services" icon={<KeyRound size={15} className="text-primary-light" />} />
            <Card className="space-y-4 p-4">
              <Field label="TMDB API key" hint={config.tmdb_api_key_configured ? 'Configured. Leave blank to keep the stored key.' : 'Required for canonical titles, seasons, and episode contracts.'}>
                <input className="input" type="password" placeholder={config.tmdb_api_key_configured ? 'Configured' : ''} value={config.tmdb_api_key} onChange={e => update({ tmdb_api_key: e.target.value })} />
              </Field>
              <Field label="Anthropic API key" hint={config.anthropic_api_key_configured ? 'Configured. Leave blank to keep the stored key.' : 'Required for the Fetch, Media, and Librarian agent loops.'}>
                <input className="input" type="password" placeholder={config.anthropic_api_key_configured ? 'Configured' : ''} value={config.anthropic_api_key} onChange={e => update({ anthropic_api_key: e.target.value })} />
              </Field>
            </Card>
          </section>

          <section>
            <SectionHeader title="Health preview" icon={<Database size={15} className="text-primary-light" />} />
            <Card className="space-y-3 p-4">
              {healthIssues.length === 0 ? (
                <div className="rounded-md border border-emerald-400/20 bg-emerald-400/10 p-3 text-sm text-emerald-200">
                  No current setup issues detected.
                </div>
              ) : (
                healthIssues.slice(0, 3).map(issue => (
                  <div key={issue.id} className="rounded-md border border-amber-300/20 bg-amber-300/[0.08] p-3">
                    <p className="text-sm font-medium text-amber-100">{issue.title}</p>
                    <p className="mt-1 text-xs leading-relaxed text-amber-100/70">{issue.detail}</p>
                    <Button
                      variant="secondary"
                      size="sm"
                      className="mt-3 w-full border-amber-200/20 bg-bg/40 text-amber-100"
                      onClick={() => applyFix(issue.id)}
                      disabled={fixing === issue.id}
                    >
                      {fixing === issue.id ? <Loader2 size={13} className="animate-spin" /> : <Wand2 size={13} />}
                      Try fix
                    </Button>
                  </div>
                ))
              )}
              {message && <p className="rounded-md border border-white/10 bg-bg/40 px-3 py-2 text-xs text-muted">{message}</p>}
              {error && <p className="rounded-md border border-rose-400/20 bg-rose-400/10 px-3 py-2 text-xs text-rose-200">{error}</p>}
            </Card>
          </section>

          <Card className="border-primary/20 bg-primary/[0.06] p-4">
            <p className="text-sm font-semibold text-text">What happens next</p>
            <div className="mt-3 space-y-2 text-sm text-muted">
              <p>Sparrow opens on the request dashboard.</p>
              <p>Try: <span className="text-primary-light">The Last of Us season 2</span>.</p>
              <p>Use <span className="text-primary-light">Compare first</span> to see the release decision before queueing.</p>
            </div>
            <Button variant="primary" className="mt-4 h-11 w-full" onClick={finish} disabled={!canStart || saving}>
              {saving ? <Loader2 size={15} className="animate-spin" /> : <Play size={15} />}
              Start using Sparrow
            </Button>
            {!canStart && (
              <p className="mt-2 text-xs text-muted">Add folders, release policy, and a torrent client to continue.</p>
            )}
          </Card>
        </aside>
      </main>
    </div>
  )
}
