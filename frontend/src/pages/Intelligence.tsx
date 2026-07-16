import { useEffect, useMemo, useState } from 'react'
import {
  Activity, AlertTriangle, Bot, CheckCircle2, ChevronDown, ChevronRight, Code,
  Cpu, GitBranch, History, Loader2, RefreshCw, ShieldCheck, Sparkles, Wand2,
  XCircle,
} from 'lucide-react'
import clsx from 'clsx'
import {
  getCWMLogs, getCWMModel, getCWMVersion, getCWMVersionContent, getCWMDiff,
  getCWMVersions, getHealth, runHealthAction, runHealthCheck,
} from '../api/client'
import { Badge, Button, Card, SectionHeader } from '../components/ui'

interface VersionInfo {
  version: number
  updated_at: number | null
  strategy_added: string
  strategy_count: number
  pattern_count: number
}

interface VersionEntry {
  version: number
  filename: string
  timestamp: number
}

interface HealthResult {
  output: string
  model_evolved: boolean
  error: string | null
}

interface CWMLog {
  id: string
  timestamp: number
  event_type: string
  summary: string
  detail: string
  affected_ids: string[]
}

type Health = Awaited<ReturnType<typeof getHealth>>

function timeAgo(ts: number | null): string {
  if (!ts) return 'never'
  const secs = Math.max(0, Math.floor(Date.now() / 1000 - ts))
  if (secs < 60) return 'just now'
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`
  return `${Math.floor(secs / 86400)}d ago`
}

function DiffView({ diff }: { diff: string }) {
  if (!diff) return <p className="text-xs text-muted italic">No changes</p>
  return (
    <pre className="overflow-x-auto rounded-md bg-bg p-3 text-[10px] font-mono leading-relaxed">
      {diff.split('\n').map((line, i) => (
        <span key={i} className={clsx(
          'block',
          line.startsWith('+') && !line.startsWith('+++') ? 'bg-emerald-400/5 text-emerald-300' :
          line.startsWith('-') && !line.startsWith('---') ? 'bg-rose-400/5 text-rose-300' :
          line.startsWith('@@') ? 'text-sky-300' :
          'text-muted/70',
        )}>{line}</span>
      ))}
    </pre>
  )
}

function MetricCard({
  icon,
  label,
  value,
  detail,
  tone = 'neutral',
}: {
  icon: React.ReactNode
  label: string
  value: string
  detail: string
  tone?: 'neutral' | 'success' | 'warning'
}) {
  return (
    <Card className="p-4">
      <div className="flex items-center gap-2 text-muted">
        {icon}
        <span className="truncate text-[11px] font-medium sm:text-xs">{label}</span>
      </div>
      <div className="mt-2 flex items-end justify-between gap-3">
        <p className="text-xl font-semibold tracking-tight text-text sm:text-2xl">{value}</p>
        <span className={clsx(
          'hidden rounded-md px-2 py-1 text-[11px] sm:inline-flex',
          tone === 'success' && 'bg-emerald-400/10 text-emerald-300',
          tone === 'warning' && 'bg-amber-300/[0.08] text-amber-200',
          tone === 'neutral' && 'bg-white/5 text-muted',
        )}>{detail}</span>
      </div>
      <p className="mt-1 truncate text-xs text-muted sm:hidden">{detail}</p>
    </Card>
  )
}

function HealthIssueCard({
  issue,
  onFix,
  fixing,
}: {
  issue: Health['issues'][number]
  onFix: (id: string) => void
  fixing: boolean
}) {
  return (
    <Card className="border-amber-300/20 bg-amber-300/[0.08] p-4">
      <div className="flex items-start gap-3">
        <AlertTriangle size={17} className="mt-0.5 shrink-0 text-amber-200" />
        <div className="min-w-0 flex-1">
          <div className="flex flex-col justify-between gap-3 sm:flex-row sm:items-start">
            <div className="min-w-0">
              <p className="text-sm font-semibold text-amber-100">{issue.title}</p>
              <p className="mt-1 text-sm leading-relaxed text-amber-100/75">{issue.detail}</p>
            </div>
            <Button
              variant="secondary"
              size="sm"
              className="shrink-0 border-amber-200/20 bg-bg/45 text-amber-100"
              onClick={() => onFix(issue.id)}
              disabled={fixing}
            >
              {fixing ? <Loader2 size={13} className="animate-spin" /> : <Wand2 size={13} />}
              Try fix
            </Button>
          </div>
          <div className="mt-3 flex flex-wrap gap-1.5">
            {issue.actions.map(action => <Badge key={action} tone="warning">{action}</Badge>)}
          </div>
        </div>
      </div>
    </Card>
  )
}

function LogCard({ log }: { log: CWMLog }) {
  return (
    <Card className="p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone="neutral">{log.event_type.replace('_', ' ')}</Badge>
            <span className="text-xs text-muted">{timeAgo(log.timestamp)}</span>
          </div>
          <p className="mt-2 text-sm font-medium text-text">{log.summary}</p>
          {log.detail && (
            <p className="mt-1 line-clamp-3 text-xs leading-relaxed text-muted">{log.detail}</p>
          )}
        </div>
      </div>
    </Card>
  )
}

export default function Intelligence() {
  const [version, setVersion] = useState<VersionInfo | null>(null)
  const [versions, setVersions] = useState<VersionEntry[]>([])
  const [logs, setLogs] = useState<CWMLog[]>([])
  const [health, setHealth] = useState<Health | null>(null)
  const [healthRun, setHealthRun] = useState<HealthResult | null>(null)
  const [loading, setLoading] = useState(true)
  const [healthChecking, setHealthChecking] = useState(false)
  const [fixing, setFixing] = useState<string | null>(null)
  const [message, setMessage] = useState('')

  const [showCode, setShowCode] = useState(false)
  const [showHistory, setShowHistory] = useState(false)
  const [cwmCode, setCwmCode] = useState('')
  const [selectedVersion, setSelectedVersion] = useState<string | null>(null)
  const [versionContent, setVersionContent] = useState('')
  const [diff, setDiff] = useState('')
  const [diffFrom, setDiffFrom] = useState<string | null>(null)
  const [loadingDiff, setLoadingDiff] = useState(false)

  const load = async () => {
    setLoading(true)
    try {
      const [versionData, versionsData, logsData, healthData] = await Promise.all([
        getCWMVersion(),
        getCWMVersions(),
        getCWMLogs(5),
        getHealth(),
      ])
      setVersion(versionData as VersionInfo)
      setVersions(versionsData as VersionEntry[])
      setLogs(logsData as CWMLog[])
      setHealth(healthData)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  const maturity = useMemo(() => {
    const strategies = version?.strategy_count || 0
    const patterns = version?.pattern_count || 0
    const score = Math.min(100, Math.round(((strategies / 8) * 70) + ((patterns / 4) * 30)))
    if (score >= 80) return { score, label: 'Experienced' }
    if (score >= 45) return { score, label: 'Learning' }
    return { score, label: 'Early' }
  }, [version])

  const runCheck = async () => {
    setHealthChecking(true)
    setHealthRun(null)
    setMessage('')
    try {
      const result = await runHealthCheck()
      setHealthRun(result as HealthResult)
      await load()
    } catch (e: unknown) {
      setHealthRun({ output: e instanceof Error ? e.message : 'Health check failed', model_evolved: false, error: 'error' })
    } finally {
      setHealthChecking(false)
    }
  }

  const applyFix = async (issueId: string) => {
    setFixing(issueId)
    setMessage('')
    try {
      const result = await runHealthAction(issueId)
      setHealth(result.health)
      setMessage(result.message)
    } finally {
      setFixing(null)
    }
  }

  const toggleCode = async () => {
    if (!cwmCode) {
      const data = await getCWMModel()
      setCwmCode(data.content)
    }
    setShowCode(prev => !prev)
  }

  const viewVersion = async (filename: string) => {
    if (selectedVersion === filename) {
      setSelectedVersion(null)
      setVersionContent('')
      return
    }
    const data = await getCWMVersionContent(filename)
    setSelectedVersion(filename)
    setVersionContent(data.content)
    setDiff('')
    setDiffFrom(null)
  }

  const loadDiff = async (from: string, to: string) => {
    setLoadingDiff(true)
    try {
      const data = await getCWMDiff(from, to)
      setDiff(data.diff)
      setDiffFrom(from)
    } finally {
      setLoadingDiff(false)
    }
  }

  const issueCount = health?.issues.length ?? 0
  const healthy = health?.healthy ?? false

  return (
    <div className="cinema-page">
      <section className="cinema-hero min-h-[440px] border-b border-white/[0.08]">
        <div className="absolute inset-0 bg-[radial-gradient(circle_at_18%_8%,rgba(244,193,93,0.16),transparent_26rem),radial-gradient(circle_at_74%_18%,rgba(22,34,54,0.58),transparent_30rem),linear-gradient(180deg,rgba(5,5,7,0.60),rgba(5,5,7,1))]" />
        <div className="cinema-shell">
          <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-start">
            <div className="min-w-0">
              <Badge tone="info" className="border-primary/25 bg-primary/10 text-primary-light">
                <Bot size={12} /> Autopilot brain
              </Badge>
              <h1 className="cinema-title">The projection room.</h1>
              <p className="cinema-copy">
                Sparrow's model watches the pipeline in the background, learning from odd releases and turning local setup failures into repairable moments.
              </p>
            </div>
            <div className="flex flex-wrap gap-2">
              <Button variant="secondary" size="sm" onClick={load} disabled={loading}>
                {loading ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />}
                Refresh
              </Button>
              <Button variant="primary" size="sm" onClick={runCheck} disabled={healthChecking}>
                {healthChecking ? <Loader2 size={13} className="animate-spin" /> : <Activity size={13} />}
                Run check
              </Button>
            </div>
          </div>

          <div className="mt-5 grid grid-cols-2 gap-2 sm:gap-3 lg:grid-cols-4">
            <MetricCard
              icon={healthy ? <CheckCircle2 size={14} /> : <XCircle size={14} />}
              label="Live health"
              value={healthy ? 'Ready' : `${issueCount}`}
              detail={healthy ? 'no issues' : 'needs attention'}
              tone={healthy ? 'success' : 'warning'}
            />
            <MetricCard
              icon={<Cpu size={14} />}
              label="CWM version"
              value={version ? `v${version.version}` : '...'}
              detail={`updated ${timeAgo(version?.updated_at ?? null)}`}
            />
            <MetricCard
              icon={<Sparkles size={14} />}
              label="Strategies"
              value={String(version?.strategy_count ?? 0)}
              detail={version?.strategy_added || 'search logic'}
            />
            <MetricCard
              icon={<ShieldCheck size={14} />}
              label="Maturity"
              value={`${maturity.score}%`}
              detail={maturity.label}
              tone={maturity.score >= 80 ? 'success' : 'neutral'}
            />
          </div>
        </div>
      </section>

      <main className="grid max-w-6xl gap-5 p-4 sm:p-6 lg:grid-cols-[minmax(0,1fr)_minmax(320px,0.85fr)]">
        <div className="space-y-5">
        <section>
            <SectionHeader title="System readiness" icon={<Activity size={15} className="text-primary-light" />} />
            {health && health.issues.length > 0 ? (
              <div className="space-y-3">
                {health.issues.map(issue => (
                  <HealthIssueCard
                    key={issue.id}
                    issue={issue}
                    onFix={applyFix}
                    fixing={fixing === issue.id}
                  />
                ))}
              </div>
            ) : (
              <Card className="border-emerald-400/20 bg-emerald-400/10 p-4">
                <div className="flex items-start gap-3">
                  <CheckCircle2 size={18} className="mt-0.5 text-emerald-300" />
                  <div>
                    <p className="text-sm font-semibold text-emerald-200">Pipeline is clear</p>
                    <p className="mt-1 text-sm text-emerald-100/75">Sparrow can resolve requests, hand off torrents, and organize completed files.</p>
                  </div>
                </div>
              </Card>
            )}
            {message && <p className="mt-3 rounded-md border border-white/10 bg-panel px-3 py-2 text-sm text-muted">{message}</p>}
          </section>

          <section>
            <SectionHeader title="Autopilot notes" icon={<History size={15} className="text-primary-light" />} />
            <div className="grid gap-3">
              {logs.length === 0 ? (
                <Card className="p-5 text-sm text-muted">No CWM observations have been recorded yet.</Card>
              ) : logs.map(log => <LogCard key={log.id} log={log} />)}
            </div>
          </section>

          {healthRun && (
            <section>
              <SectionHeader title="Last health run" icon={<Activity size={15} className="text-primary-light" />} />
              <Card className={clsx('p-4', healthRun.error ? 'border-rose-400/20 bg-rose-400/10' : 'border-primary/20 bg-primary/[0.04]')}>
                <div className="mb-3 flex flex-wrap items-center gap-2">
                  <Badge tone={healthRun.error ? 'danger' : 'success'}>
                    {healthRun.error ? 'Issues found' : 'Completed'}
                  </Badge>
                  {healthRun.model_evolved && <Badge tone="info">Model evolved</Badge>}
                </div>
                <pre className="max-h-72 overflow-y-auto whitespace-pre-wrap rounded-md bg-bg p-3 text-xs leading-relaxed text-text/80">
                  {healthRun.output || '(no output)'}
                </pre>
              </Card>
            </section>
          )}
        </div>

        <aside className="space-y-5">
          <Card className="p-5">
            <div className="flex items-center justify-between gap-3">
              <div className="min-w-0">
                <p className="text-sm font-semibold text-text">Autopilot model</p>
                <p className="mt-0.5 text-xs text-muted">
                  {version ? `${version.strategy_count} strategies · ${version.pattern_count} patterns` : 'Loading model status'}
                </p>
              </div>
              <Badge tone="info">v{version?.version ?? '?'}</Badge>
            </div>
            <div className="mt-4 grid grid-cols-2 gap-3">
              <Card className="bg-bg/35 p-3">
                <p className="text-xs text-muted">Latest strategy</p>
                <p className="mt-1 truncate text-sm font-medium text-text">{version?.strategy_added || 'None yet'}</p>
              </Card>
              <Card className="bg-bg/35 p-3">
                <p className="text-xs text-muted">Snapshots</p>
                <p className="mt-1 text-sm font-medium text-text">{versions.length}</p>
              </Card>
            </div>
            <Button variant="secondary" className="mt-4 w-full" onClick={toggleCode}>
              <Code size={14} /> {showCode ? 'Hide source' : 'Inspect source'}
            </Button>
            {showCode && cwmCode && (
              <pre className="mt-3 max-h-80 overflow-y-auto whitespace-pre-wrap rounded-md bg-bg p-3 text-[10px] font-mono leading-relaxed text-text/70">
                {cwmCode}
              </pre>
            )}
          </Card>

          <Card className="p-5">
            <button
              type="button"
              className="flex w-full items-center gap-2 text-left"
              onClick={() => setShowHistory(prev => !prev)}
            >
              <GitBranch size={15} className="text-primary-light" />
              <span className="text-sm font-semibold text-text">Learning history</span>
              <span className="ml-auto text-muted">{showHistory ? <ChevronDown size={14} /> : <ChevronRight size={14} />}</span>
            </button>
            <p className="mt-1 text-xs text-muted">{versions.length} model snapshots captured.</p>
            {showHistory && (
              <div className="mt-3 space-y-2">
                {versions.length === 0 ? (
                  <p className="text-xs text-muted">No evolution snapshots yet.</p>
                ) : versions.map((entry, index) => {
                  const isSelected = selectedVersion === entry.filename
                  const previous = versions[index + 1]?.filename
                  return (
                    <div key={entry.filename} className="overflow-hidden rounded-md border border-white/10 bg-bg/35">
                      <div className="flex items-center gap-2 px-3 py-2 text-xs">
                        <Badge tone="info">v{entry.version}</Badge>
                        <span className="text-muted">{timeAgo(entry.timestamp)}</span>
                        <span className="min-w-0 flex-1 truncate font-mono text-muted/70">{entry.filename}</span>
                        {previous && (
                          <button className="text-muted hover:text-primary-light" onClick={() => loadDiff(previous, entry.filename)}>
                            {loadingDiff && diffFrom === previous ? '...' : 'diff'}
                          </button>
                        )}
                        <button className={clsx('text-muted hover:text-text', isSelected && 'text-primary-light')} onClick={() => viewVersion(entry.filename)}>
                          {isSelected ? 'hide' : 'view'}
                        </button>
                      </div>
                      {diffFrom === previous && diff && (
                        <div className="border-t border-white/10">
                          <DiffView diff={diff} />
                        </div>
                      )}
                      {isSelected && versionContent && (
                        <pre className="max-h-64 overflow-y-auto whitespace-pre-wrap border-t border-white/10 bg-bg p-3 text-[10px] font-mono text-text/70">
                          {versionContent}
                        </pre>
                      )}
                    </div>
                  )
                })}
                {versions.length > 0 && (
                  <Button variant="ghost" size="sm" onClick={() => loadDiff(versions[0].filename, 'current')}>
                    {loadingDiff && diffFrom === versions[0].filename ? <Loader2 size={13} className="animate-spin" /> : <GitBranch size={13} />}
                    Diff latest to current
                  </Button>
                )}
                {diffFrom === versions[0]?.filename && diff && <DiffView diff={diff} />}
              </div>
            )}
          </Card>
        </aside>
      </main>
    </div>
  )
}
