import type { ButtonHTMLAttributes, HTMLAttributes, ReactNode } from 'react'
import { useEffect, useState } from 'react'
import clsx from 'clsx'

export function formatRelativeTime(ts: number, now = Date.now() / 1000): string {
  const delta = now - ts
  const abs = Math.abs(delta)
  const suffix = delta >= 0 ? ' ago' : ' from now'
  if (abs < 45) return delta >= 0 ? 'just now' : 'in under a minute'
  const units: Array<[number, string]> = [
    [60, 'minute'], [60, 'hour'], [24, 'day'], [7, 'week'],
  ]
  let value = abs / 60
  let label = 'minute'
  for (let i = 1; i < units.length; i++) {
    if (value < units[i][0]) break
    value /= units[i][0]
    label = units[i][1]
  }
  const rounded = Math.round(value)
  return `${rounded} ${label}${rounded === 1 ? '' : 's'}${suffix}`
}

/**
 * Friendly relative time ("4 minutes ago") with the exact local date and
 * time on hover. Every user-visible operation should carry one of these.
 */
export function RelativeTime({ ts, className }: { ts: number | null | undefined; className?: string }) {
  const [, tick] = useState(0)
  useEffect(() => {
    const id = setInterval(() => tick(t => t + 1), 30_000)
    return () => clearInterval(id)
  }, [])
  if (!ts) return <span className={clsx('text-muted', className)}>—</span>
  const exact = new Date(ts * 1000).toLocaleString(undefined, {
    weekday: 'short', year: 'numeric', month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit',
  })
  return (
    <time title={exact} dateTime={new Date(ts * 1000).toISOString()} className={clsx('cursor-help', className)}>
      {formatRelativeTime(ts)}
    </time>
  )
}

type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger'
type ButtonSize = 'sm' | 'md' | 'icon'

export function Button({
  variant = 'secondary',
  size = 'md',
  className,
  children,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: ButtonVariant
  size?: ButtonSize
}) {
  return (
    <button
      className={clsx(
        'inline-flex min-w-0 max-w-full items-center justify-center gap-2 rounded-full font-semibold transition-all duration-200 disabled:cursor-not-allowed disabled:opacity-50',
        size === 'sm' && 'h-8 px-3 text-xs',
        size === 'md' && 'h-10 px-4 text-sm',
        size === 'icon' && 'h-8 w-8 p-0',
        variant === 'primary' && 'bg-primary text-bg shadow-glow hover:bg-primary-light',
        variant === 'secondary' && 'border border-white/[0.15] bg-white/[0.08] text-text backdrop-blur-xl hover:-translate-y-0.5 hover:bg-white/[0.14]',
        variant === 'ghost' && 'text-muted hover:bg-white/[0.07] hover:text-text',
        variant === 'danger' && 'bg-rose-600/20 text-rose-200 hover:bg-rose-600/30',
        className,
      )}
      {...props}
    >
      {children}
    </button>
  )
}

export function Card({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={clsx('min-w-0 max-w-full rounded-2xl border border-white/10 bg-white/[0.065] shadow-2xl shadow-black/25 backdrop-blur-xl', className)} {...props} />
}

export function Badge({
  tone = 'neutral',
  className,
  children,
}: HTMLAttributes<HTMLSpanElement> & {
  tone?: 'neutral' | 'success' | 'warning' | 'danger' | 'info'
}) {
  return (
    <span
      className={clsx(
        'inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-[11px] font-semibold',
        tone === 'neutral' && 'border-white/10 bg-white/5 text-muted',
        tone === 'success' && 'border-emerald-400/20 bg-emerald-400/10 text-emerald-300',
        tone === 'warning' && 'border-amber-300/20 bg-amber-300/[0.08] text-amber-200',
        tone === 'danger' && 'border-rose-400/20 bg-rose-400/10 text-rose-300',
        tone === 'info' && 'border-sky-400/20 bg-sky-400/10 text-sky-200',
        className,
      )}
    >
      {children}
    </span>
  )
}

export function Progress({ value, className }: { value: number; className?: string }) {
  return (
    <div className={clsx('h-2 overflow-hidden rounded-full bg-white/[0.12]', className)}>
      <div
        className="h-full rounded-full bg-gradient-to-r from-primary-dark via-primary to-primary-light transition-all"
        style={{ width: `${Math.max(0, Math.min(100, value))}%` }}
      />
    </div>
  )
}

export function SectionHeader({
  title,
  meta,
  icon,
}: {
  title: string
  meta?: string
  icon?: ReactNode
}) {
  return (
    <div className="mb-3 flex min-w-0 items-center justify-between">
      <div className="flex min-w-0 items-center gap-2">
        {icon}
        <h2 className="min-w-0 break-words text-sm font-semibold uppercase tracking-[0.18em] text-text/60">{title}</h2>
      </div>
      {meta && <span className="text-xs text-muted">{meta}</span>}
    </div>
  )
}
