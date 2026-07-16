import { useEffect, useRef } from 'react'
import { Outlet, NavLink, useLocation } from 'react-router-dom'
import { Search, Film, Settings, Sparkles, ListChecks } from 'lucide-react'
import clsx from 'clsx'

const navItems = [
  { to: '/', icon: Search, label: 'Discover' },
  { to: '/library', icon: Film, label: 'Library' },
  { to: '/activity', icon: ListChecks, label: 'Activity' },
  { to: '/settings', icon: Settings, label: 'Settings' },
]

export default function Layout() {
  const location = useLocation()
  const activeLinkRef = useRef<HTMLAnchorElement | null>(null)

  useEffect(() => {
    activeLinkRef.current?.scrollIntoView({
      behavior: 'smooth',
      block: 'nearest',
      inline: 'center',
    })
  }, [location.pathname])

  return (
    <div className="min-h-screen w-screen max-w-[100vw] overflow-x-hidden bg-bg text-text">
      <header className="fixed inset-x-0 top-0 z-30 max-w-[100vw] overflow-hidden border-b border-white/[0.08] bg-black/35 backdrop-blur-2xl">
        <div className="flex w-full min-w-0 max-w-full items-center gap-2 px-3 py-3 sm:gap-3 sm:px-5">
          <NavLink to="/" className="flex shrink-0 items-center gap-2 rounded-full pr-1">
            <div className="flex h-9 w-9 items-center justify-center rounded-full bg-primary text-sm font-black text-bg shadow-glow">S</div>
            <div className="hidden sm:block">
              <span className="block text-base font-semibold tracking-tight text-text">Sparrow</span>
              <span className="hidden text-[11px] text-muted sm:block">Cinema autopilot</span>
            </div>
          </NavLink>

          <nav className="nav-scroll ml-auto flex min-w-0 flex-1 gap-1 overflow-x-auto rounded-full border border-white/[0.08] bg-white/[0.055] p-1 pr-5 sm:flex-none sm:pr-1">
          {navItems.map(({ to, icon: Icon, label }) => (
            <NavLink
              key={to}
              to={to}
              end={to === '/'}
              ref={location.pathname === to ? activeLinkRef : undefined}
              className={({ isActive }) =>
                clsx(
                  'flex shrink-0 items-center gap-2 rounded-full px-3 py-2 text-xs font-semibold transition-all sm:px-3.5 sm:text-sm',
                  isActive
                    ? 'bg-primary text-bg shadow-glow'
                    : 'text-muted hover:bg-white/[0.09] hover:text-text'
                )
              }
            >
              <Icon size={16} />
              <span>{label}</span>
            </NavLink>
          ))}
          </nav>

          <div className="hidden shrink-0 items-center gap-2 rounded-full border border-white/[0.08] bg-white/[0.055] px-3 py-2 text-xs text-muted lg:flex">
            <Sparkles size={13} className="text-primary-light" />
            Local-first
          </div>
        </div>
      </header>

      <main className="w-full min-w-0 max-w-full overflow-x-hidden">
        <Outlet />
      </main>
    </div>
  )
}
