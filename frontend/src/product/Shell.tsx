import { useEffect, useState } from "react";
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
  SlidersHorizontal,
  Server,
  ChevronDown,
  ArrowUpRight,
  LogOut,
  ShieldCheck,
  ScrollText,
} from "lucide-react";
import { post, type User } from "./api";
import { ErrorNote } from "./ui";
import { Mark } from "./Brand";

const navigation = [
  { to: "/", label: "Home", icon: Home },
  { to: "/library", label: "Library", icon: Library },
  { to: "/discover", label: "Discover", icon: Compass },
  { to: "/activity", label: "Activity", icon: Activity },
];
const management = [
  { to: "/settings", label: "My preferences", icon: Settings },
  { to: "/settings/security", label: "Account & security", icon: ShieldCheck },
  { to: "/settings/logs", label: "Logs", icon: ScrollText },
  { to: "/settings/people", label: "People", icon: Users },
  { to: "/settings/storage", label: "Storage & import", icon: HardDrive },
  {
    to: "/settings/defaults",
    label: "Household defaults",
    icon: SlidersHorizontal,
  },
  { to: "/settings/server", label: "Server settings", icon: Server },
];

export default function Shell({
  user,
  onLogout,
}: {
  user: User;
  onLogout: () => void;
}) {
  const [signingOut, setSigningOut] = useState(false);
  const [logoutError, setLogoutError] = useState("");
  async function logout() {
    setSigningOut(true);
    setLogoutError("");
    try {
      await post("/auth/logout");
      onLogout();
    } catch (error) {
      setLogoutError((error as Error).message);
      setSigningOut(false);
    }
  }
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
            <h2>Settings</h2>
            <nav aria-label="Settings navigation">
              {management
                .filter((_, index) => index < 3 || user.role === "admin")
                .map(({ to, label, icon: Icon }) => (
                  <NavLink key={to} to={to} end>
                    <Icon size={18} />
                    <span>{label}</span>
                  </NavLink>
                ))}
            </nav>
            <div className="sp-settings-signout">
              <button
                className="sp-button quiet"
                onClick={logout}
                disabled={signingOut}
              >
                <LogOut size={18} />
                {signingOut ? "Signing out…" : "Sign out"}
              </button>
              <ErrorNote error={logoutError} />
            </div>
          </aside>
        )}
        {!user.welcomed && (
          <div className="sp-welcome-signout">
            <button
              className="sp-button quiet"
              onClick={logout}
              disabled={signingOut}
            >
              Sign out
            </button>
            <ErrorNote error={logoutError} />
          </div>
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
