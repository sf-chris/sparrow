import { useEffect } from "react";
import {
  NavLink,
  Link,
  Outlet,
  useLocation,
  useNavigate,
} from "react-router-dom";
import {
  Home,
  Library,
  Compass,
  Activity,
  Settings,
  HardDrive,
  Users,
  Search,
  SlidersHorizontal,
  Server,
  ChevronDown,
  ArrowUpRight,
} from "lucide-react";
import type { User } from "./api";
import { Mark } from "./Brand";

const navigation = [
  { to: "/", label: "Home", icon: Home },
  { to: "/library", label: "Library", icon: Library },
  { to: "/discover", label: "Discover", icon: Compass },
  { to: "/activity", label: "Activity", icon: Activity },
];
const management = [
  { to: "/settings", label: "My preferences", icon: Settings },
  { to: "/settings/people", label: "People", icon: Users },
  { to: "/settings/storage", label: "Storage & import", icon: HardDrive },
  {
    to: "/settings/defaults",
    label: "Household defaults",
    icon: SlidersHorizontal,
  },
  { to: "/settings/server", label: "Server settings", icon: Server },
];

export default function Shell({ user }: { user: User }) {
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const settings = pathname.startsWith("/settings") && user.welcomed;
  const watching = pathname.startsWith("/watch/");
  useEffect(() => {
    window.scrollTo({ top: 0, behavior: "instant" });
    const current = [...navigation, ...management].find(
      (item) => item.to === pathname,
    )?.label;
    document.title = current ? `${current} · Sparrow` : "Sparrow";
  }, [pathname]);
  useEffect(() => {
    const shortcut = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        navigate("/discover");
      }
    };
    window.addEventListener("keydown", shortcut);
    return () => window.removeEventListener("keydown", shortcut);
  }, [navigate]);
  return (
    <div
      className={`sp-app ${settings ? "sp-settings-app" : ""} ${watching ? "sp-watching-app" : ""}`}
    >
      <a className="sp-skip" href="#main-content">
        Skip to content
      </a>
      <header className="sp-topbar">
        <div className="sp-topbar-inner">
          <Link to="/" className="sp-brand" aria-label="Sparrow home">
            <Mark />
            <span>sparrow</span>
          </Link>
          <nav className="sp-desktop-nav" aria-label="Main navigation">
            {navigation.map(({ to, label }) => (
              <NavLink key={to} to={to} end={to === "/"}>
                {label}
              </NavLink>
            ))}
          </nav>
          <div className="sp-topbar-actions">
            <Link
              className="sp-header-search"
              to="/discover"
              aria-label="Search movies and shows"
            >
              <Search size={19} />
              <span>Find something</span>
              <kbd>⌘ K</kbd>
            </Link>
            <span className="sp-header-divider" />
            <NavLink
              to="/settings"
              className="sp-account"
              aria-label={`${user.name}’s settings`}
            >
              <span className="sp-avatar">
                {user.name.slice(0, 1).toUpperCase()}
              </span>
              <span className="sp-account-name">{user.name}</span>
              <ChevronDown size={14} />
            </NavLink>
          </div>
        </div>
      </header>
      <div className={`sp-workspace ${settings ? "sp-settings-layout" : ""}`}>
        {settings && (
          <aside className="sp-settings-nav">
            <p className="sp-eyebrow">Make it yours</p>
            <h2>Settings</h2>
            <nav aria-label="Settings navigation">
              {management
                .filter((_, index) => index === 0 || user.role === "admin")
                .map(({ to, label, icon: Icon }) => (
                  <NavLink key={to} to={to} end>
                    <Icon size={18} />
                    <span>{label}</span>
                  </NavLink>
                ))}
            </nav>
            <div className="sp-settings-note">
              <Mark />
              <p>
                Your collection.
                <br />
                Your rules.
              </p>
            </div>
          </aside>
        )}
        <Outlet />
      </div>
      {!watching && (
        <footer className="sp-footer">
          <span>
            <Mark /> A little less managing. A lot more watching.
          </span>
          <Link to="/discover">
            The next good thing <ArrowUpRight size={14} />
          </Link>
        </footer>
      )}
      <nav className="sp-bottom-nav" aria-label="Mobile navigation">
        {navigation.map(({ to, label, icon: Icon }) => (
          <NavLink key={to} to={to} end={to === "/"}>
            <Icon size={20} />
            <span>{label}</span>
          </NavLink>
        ))}
      </nav>
    </div>
  );
}
