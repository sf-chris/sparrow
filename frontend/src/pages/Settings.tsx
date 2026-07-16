import { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'
import {
  Activity, Bot, Check, ChevronRight, DownloadCloud, Gauge, HardDrive,
  KeyRound, ListChecks, Loader2, RefreshCw, Save, ScrollText, SlidersHorizontal, Wifi,
} from 'lucide-react'
import { discoverClients, getAgentPrompts, getConfig, testClient, updateConfig } from '../api/client'
import type { AgentPromptPreview, Config, TorrentClientInfo } from '../types'
import { Badge, Button, Card, SectionHeader } from '../components/ui'
import clsx from 'clsx'

const MODEL_OPTIONS = [
  { id: 'claude-sonnet-5', label: 'Claude Sonnet 5' },
  { id: 'claude-opus-4-8', label: 'Claude Opus 4.8' },
  { id: 'claude-sonnet-4-6', label: 'Claude Sonnet 4.6' },
  { id: 'claude-haiku-4-5', label: 'Claude Haiku 4.5' },
]

function Toggle({
  checked,
  onChange,
  label,
  description,
}: {
  checked: boolean
  onChange: () => void
  label: string
  description: string
}) {
  return (
    <button
      type="button"
      onClick={onChange}
      className="flex w-full min-w-0 max-w-full items-center justify-between gap-3 rounded-lg border border-white/10 bg-black/20 px-3 py-2.5 text-left transition-colors hover:bg-white/[0.04]"
    >
      <span className="min-w-0">
        <span className="block break-words text-sm font-semibold text-text">{label}</span>
        <span className="mt-0.5 block break-words text-[11px] leading-snug text-muted">{description}</span>
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

function BackstageLink({
  to,
  icon,
  title,
  description,
}: {
  to: string
  icon: React.ReactNode
  title: string
  description: string
}) {
  return (
    <Link
      to={to}
      className="group inline-flex min-w-0 items-center gap-2 rounded-full border border-white/10 bg-white/[0.05] px-3 py-1.5 text-xs text-muted transition-colors hover:border-primary/30 hover:text-text"
    >
      <span className="shrink-0 text-primary-light">
        {icon}
      </span>
      <span className="font-medium">{title}</span>
      <span className="sr-only">{description}</span>
      <ChevronRight size={13} className="shrink-0 transition-colors group-hover:text-primary-light" />
    </Link>
  )
}

export default function Settings() {
  const [config, setConfig] = useState<Config | null>(null)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [discovering, setDiscovering] = useState(false)
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState<{ ok: boolean; message: string } | null>(null)
  const [discovered, setDiscovered] = useState<TorrentClientInfo[]>([])
  const [prompts, setPrompts] = useState<AgentPromptPreview[]>([])
  const [promptLoading, setPromptLoading] = useState(true)

  useEffect(() => {
    getConfig().then(setConfig).catch(() => {})
    getAgentPrompts().then(setPrompts).catch(() => {}).finally(() => setPromptLoading(false))
  }, [])

  const update = (patch: Partial<Config>) => setConfig(prev => prev ? { ...prev, ...patch } : prev)
  const updateTC = (patch: Partial<Config['torrent_client']>) =>
    setConfig(prev => prev ? { ...prev, torrent_client: { ...prev.torrent_client, ...patch } } : prev)

  const save = async () => {
    if (!config) return
    setSaving(true)
    setSaved(false)
    try {
      const next = await updateConfig(config)
      setConfig(next)
      getAgentPrompts().then(setPrompts).catch(() => {})
      setSaved(true)
      setTimeout(() => setSaved(false), 1800)
    } finally {
      setSaving(false)
    }
  }

  const discover = async () => {
    setDiscovering(true)
    try {
      const clients = await discoverClients()
      setDiscovered(clients)
    } finally {
      setDiscovering(false)
    }
  }

  const testConnection = async () => {
    if (!config) return
    setTesting(true)
    setTestResult(null)
    try {
      const result = await testClient(config.torrent_client)
      setTestResult({
        ok: result.reachable,
        message: result.reachable
          ? `Connected to ${result.type} ${result.version ? `v${result.version}` : ''} on ${result.host}:${result.port}`
          : 'Could not connect. Start the client or enable remote/web access.',
      })
    } catch (e: unknown) {
      setTestResult({ ok: false, message: e instanceof Error ? e.message : 'Test failed' })
    } finally {
      setTesting(false)
    }
  }

  if (!config) {
    return (
      <div className="flex items-center gap-2 p-6 text-sm text-muted">
        <Loader2 size={14} className="animate-spin" /> Loading settings...
      </div>
    )
  }

  return (
    <div className="min-h-screen w-full min-w-0 max-w-full overflow-x-hidden bg-bg text-text">
      <section className="border-b border-white/[0.08] bg-surface/55 px-4 pb-5 pt-20 backdrop-blur-xl sm:px-6">
        <div className="flex w-full max-w-6xl flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
          <div className="min-w-0">
            <div className="mb-2 inline-flex items-center gap-2 rounded-full border border-white/10 bg-white/[0.05] px-3 py-1 text-xs font-medium text-muted">
              <SlidersHorizontal size={13} />
              Settings
            </div>
            <h1 className="text-2xl font-semibold tracking-tight text-text sm:text-3xl">Configuration</h1>
            <p className="mt-1 max-w-2xl text-sm leading-relaxed text-muted">
              Release preferences, storage paths, downloader connection, seeding, and keys.
            </p>
          </div>
          <Button variant="primary" onClick={save} disabled={saving} className="w-full min-w-0 sm:w-auto">
              {saving ? <Loader2 size={14} className="animate-spin" /> : saved ? <Check size={14} /> : <Save size={14} />}
              {saving ? 'Saving...' : saved ? 'Saved' : 'Save changes'}
          </Button>
        </div>
      </section>

      <div className="w-full min-w-0 max-w-full space-y-6 overflow-hidden p-4 sm:max-w-6xl sm:p-6">
        <section className="min-w-0">
          <SectionHeader title="Release selection" icon={<Gauge size={15} className="text-primary-light" />} />
          <div className="grid w-full min-w-0 gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
            <Card className="space-y-2.5 p-3 sm:p-4">
              <Toggle
                checked={config.prefer_season_packs}
                onChange={() => update({ prefer_season_packs: !config.prefer_season_packs })}
                label="Prefer season packs"
                description="Search full-season packs first, then fall back to individual episodes when the pack is missing or poor."
              />
              <Toggle
                checked={config.prefer_smaller_files}
                onChange={() => update({ prefer_smaller_files: !config.prefer_smaller_files })}
                label="Prefer smaller files"
                description="Prioritize x265/HEVC encodes and compact releases when scores are otherwise close."
              />
              <Toggle
                checked={config.auto_organize}
                onChange={() => update({ auto_organize: !config.auto_organize })}
                label="Organize silently"
                description="Move completed files to the library automatically, then verify the result after the fact."
              />
            </Card>

            <Card className="space-y-3 p-3 sm:p-4">
              <Field label="Preferred quality">
                <select
                  className="input"
                  value={config.quality_preference}
                  onChange={e => update({ quality_preference: e.target.value as Config['quality_preference'] })}
                >
                  <option value="2160p">4K (2160p)</option>
                  <option value="1080p">1080p</option>
                  <option value="720p">720p</option>
                  <option value="480p">480p</option>
                  <option value="any">Any</option>
                </select>
              </Field>
              <Field
                label="Season pack size cap"
                hint="0 means automatic size sanity based on quality and episode count."
              >
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
              <div className="min-w-0 rounded-lg border border-primary/15 bg-primary/[0.04] p-3">
                <p className="text-xs font-semibold uppercase tracking-[0.18em] text-primary-light">Current policy</p>
                <p className="mt-1 break-words text-xs text-muted">
                  {config.prefer_season_packs ? 'Try season packs first' : 'Use individual episodes'} at {config.quality_preference};
                  {config.season_pack_size_limit_gb > 0
                    ? ` reject packs above ${config.season_pack_size_limit_gb} GB.`
                    : ' reject packs only when they fail automatic size checks.'}
                </p>
              </div>
            </Card>
          </div>
        </section>

        <section className="min-w-0">
          <SectionHeader title="Storage" icon={<HardDrive size={15} className="text-primary-light" />} />
          <Card className="grid w-full gap-4 p-3 sm:p-4 lg:grid-cols-2">
            <Field label="Staging folder" hint="Downloads land here before Sparrow organizes them.">
              <input className="input" value={config.staging_dir} onChange={e => update({ staging_dir: e.target.value })} />
            </Field>
            <Field label="Library folder" hint="Sparrow creates Movies and TV Shows inside this folder.">
              <input className="input" value={config.library_dir} onChange={e => update({ library_dir: e.target.value })} />
            </Field>
          </Card>
        </section>

        <section className="min-w-0">
          <SectionHeader title="Torrent client" icon={<DownloadCloud size={15} className="text-primary-light" />} />
          <Card className="w-full space-y-4 p-3 sm:p-4">
            <div className="flex min-w-0 flex-col justify-between gap-3 sm:flex-row sm:items-center">
              <div className="min-w-0">
                <p className="text-sm font-medium text-text">Connection</p>
                <p className="break-words text-xs text-muted">Sparrow can drive Transmission or qBittorrent locally.</p>
              </div>
              <div className="flex flex-wrap gap-2">
                <Button variant="secondary" size="sm" onClick={discover} disabled={discovering}>
                  {discovering ? <Loader2 size={13} className="animate-spin" /> : <Wifi size={13} />}
                  Auto-detect
                </Button>
                <Button variant="secondary" size="sm" onClick={testConnection} disabled={testing}>
                  {testing ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />}
                  Test
                </Button>
              </div>
            </div>

            {testResult && (
              <div className={clsx(
                'min-w-0 break-words rounded-md border px-3 py-2 text-sm',
                testResult.ok
                  ? 'border-emerald-400/20 bg-emerald-400/10 text-emerald-200'
                  : 'border-rose-400/20 bg-rose-400/10 text-rose-200',
              )}>
                {testResult.message}
              </div>
            )}

            {discovered.length > 0 && (
              <div className="grid min-w-0 gap-2 sm:grid-cols-2">
                {discovered.map(client => (
                  <button
                    key={`${client.type}-${client.host}-${client.port}`}
                    onClick={() => updateTC({ type: client.type, host: client.host, port: client.port })}
                    className="min-w-0 break-words rounded-md border border-primary/25 bg-primary/10 px-3 py-2 text-left text-sm text-primary-light transition-colors hover:bg-primary/15"
                  >
                    Use {client.type} on {client.host}:{client.port}
                  </button>
                ))}
              </div>
            )}

            <div className="grid min-w-0 gap-4 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_8rem]">
              <Field label="Type">
                <select
                  className="input"
                  value={config.torrent_client.type}
                  onChange={e => updateTC({ type: e.target.value as Config['torrent_client']['type'] })}
                >
                  <option value="none">None</option>
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
            <div className="grid min-w-0 gap-4 sm:grid-cols-2">
              <Field label="Username">
                <input className="input" value={config.torrent_client.username} onChange={e => updateTC({ username: e.target.value })} />
              </Field>
              <Field label="Password">
                <input className="input" type="password" placeholder={config.torrent_client.password_configured ? 'Configured' : ''} value={config.torrent_client.password} onChange={e => updateTC({ password: e.target.value })} />
              </Field>
            </div>
          </Card>
        </section>

        <section className="min-w-0">
          <SectionHeader title="Seeding" icon={<RefreshCw size={15} className="text-primary-light" />} />
          <Card className="grid w-full gap-4 p-3 sm:grid-cols-2 sm:p-4">
            <Field label="Ratio limit" hint="0 means no ratio limit.">
              <input
                className="input"
                type="number"
                min="0"
                step="0.1"
                value={config.seeding_ratio_limit}
                onChange={e => update({ seeding_ratio_limit: parseFloat(e.target.value) || 0 })}
              />
            </Field>
            <Field label="Time limit" hint="Hours. 0 means no time limit.">
              <input
                className="input"
                type="number"
                min="0"
                step="0.5"
                value={config.seeding_time_hours}
                onChange={e => update({ seeding_time_hours: parseFloat(e.target.value) || 0 })}
              />
            </Field>
            <Field
              label="Concurrent downloads"
              hint="Hard cap on Sparrow-managed transfers running at once. Agents queue the rest."
            >
              <input
                className="input"
                type="number"
                min="1"
                step="1"
                value={config.max_active_transfers}
                onChange={e => update({ max_active_transfers: Math.max(1, parseInt(e.target.value) || 1) })}
              />
            </Field>
          </Card>
        </section>

        <section className="min-w-0">
          <SectionHeader title="Agent models" icon={<Bot size={15} className="text-primary-light" />} />
          <Card className="space-y-4 p-3 sm:p-4">
            <div className="grid gap-4 sm:grid-cols-2">
              <Field
                label="Smart tier"
                hint="Fetch Agents use this for search judgment and long-running decisions."
              >
                <input
                  className="input"
                  list="sparrow-models"
                  value={config.smart_model}
                  onChange={e => update({ smart_model: e.target.value })}
                />
              </Field>
              <Field
                label="Cheap tier"
                hint="Media Agents, the Librarian, and fuzzy title resolution use this."
              >
                <input
                  className="input"
                  list="sparrow-models"
                  value={config.cheap_model}
                  onChange={e => update({ cheap_model: e.target.value })}
                />
              </Field>
              <datalist id="sparrow-models">
                {MODEL_OPTIONS.map(model => <option key={model.id} value={model.id}>{model.label}</option>)}
              </datalist>
            </div>
            <div className="flex flex-col gap-2 rounded-lg border border-primary/15 bg-primary/[0.04] p-3 sm:flex-row sm:items-center sm:justify-between">
              <p className="text-xs leading-relaxed text-muted">
                Saved choices apply to new sessions. Existing sessions keep their assigned model so their context and cost ledger remain coherent.
              </p>
              <Link to="/activity" className="shrink-0 text-xs font-semibold text-primary-light hover:underline">
                Open usage &amp; cost center
              </Link>
            </div>
          </Card>
        </section>

        <section className="min-w-0">
          <SectionHeader title="Agent prompts" icon={<ScrollText size={15} className="text-primary-light" />} />
          <Card className="p-3 sm:p-4">
            <p className="mb-3 text-xs leading-relaxed text-muted">
              These are the exact standing prompts currently assembled for each agent, including the 360-character journal rule and current local context.
            </p>
            {promptLoading ? (
              <p className="inline-flex items-center gap-2 text-sm text-muted"><Loader2 size={13} className="animate-spin" /> Loading prompts...</p>
            ) : prompts.length === 0 ? (
              <p className="text-sm text-muted">Prompts are unavailable while the agent service is restarting.</p>
            ) : (
              <div className="divide-y divide-white/[0.08]">
                {prompts.map(prompt => (
                  <details key={prompt.agent} className="py-3">
                    <summary className="flex cursor-pointer list-none flex-wrap items-center gap-2 text-sm font-semibold text-text">
                      <ChevronRight size={14} className="text-muted" />
                      {prompt.label}
                      <Badge tone="neutral">{prompt.model}</Badge>
                      <span className="text-xs font-normal text-muted">{prompt.context}</span>
                    </summary>
                    <pre className="mt-3 max-h-[34rem] overflow-auto whitespace-pre-wrap break-words rounded-lg border border-white/10 bg-black/30 p-3 font-mono text-[11px] leading-relaxed text-muted">
                      {prompt.prompt}
                    </pre>
                    <p className="mt-3 text-[10px] font-semibold uppercase tracking-wide text-muted/60">
                      Tool instructions sent with the prompt ({prompt.tools.length})
                    </p>
                    <div className="mt-1 divide-y divide-white/[0.06] rounded-lg border border-white/[0.07] px-3">
                      {prompt.tools.map(tool => (
                        <details key={tool.name} className="py-2">
                          <summary className="cursor-pointer font-mono text-[11px] text-text/85">{tool.name}</summary>
                          <p className="mt-1 text-xs leading-relaxed text-muted">{tool.description}</p>
                          <pre className="mt-2 overflow-auto whitespace-pre-wrap break-words rounded bg-black/20 p-2 font-mono text-[10px] text-muted/80">
                            {JSON.stringify(tool.input_schema, null, 2)}
                          </pre>
                        </details>
                      ))}
                    </div>
                  </details>
                ))}
              </div>
            )}
          </Card>
        </section>

        <section className="min-w-0">
          <SectionHeader title="Keys" icon={<KeyRound size={15} className="text-primary-light" />} />
          <Card className="grid w-full gap-4 p-3 sm:p-4">
            <Field label="TMDB API key" hint={config.tmdb_api_key_configured ? 'Configured. Leave blank to keep the stored key.' : 'Required for posters, titles, seasons, and episode contracts.'}>
              <input className="input" type="password" placeholder={config.tmdb_api_key_configured ? 'Configured' : ''} value={config.tmdb_api_key} onChange={e => update({ tmdb_api_key: e.target.value })} />
            </Field>
            <Field label="Anthropic API key" hint={config.anthropic_api_key_configured ? 'Configured. Leave blank to keep the stored key.' : 'Required for the Fetch, Media, and Librarian agents.'}>
              <input className="input" type="password" placeholder={config.anthropic_api_key_configured ? 'Configured' : ''} value={config.anthropic_api_key} onChange={e => update({ anthropic_api_key: e.target.value })} />
            </Field>
          </Card>
        </section>

        <section className="min-w-0 border-t border-white/10 pt-4">
          <SectionHeader title="Advanced" />
          <p className="mb-3 text-xs text-muted">Power-user tools. Everyday use doesn't need these.</p>
          <div className="flex flex-wrap gap-2">
            <BackstageLink
              to="/search"
              icon={<ScrollText size={13} />}
              title="Release browser"
              description="Browse and pick individual releases by hand."
            />
            <BackstageLink
              to="/downloads"
              icon={<ListChecks size={13} />}
              title="Requests (classic)"
              description="The classic request and transfer view."
            />
            <BackstageLink
              to="/logs"
              icon={<Activity size={13} />}
              title="Logs"
              description="The studio log for searches, downloads, artwork, and organization."
            />
          </div>
        </section>
      </div>
    </div>
  )
}
