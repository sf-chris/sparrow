import { useEffect, useState } from "react";
import {
  NavLink,
  Link,
  Outlet,
  useLocation,
  useNavigate,
} from "react-router-dom";
import { Home, Library, Search, Activity } from "lucide-react";
import { post, type User } from "./api";
import { ErrorNote } from "./ui";
import { Mark } from "./Brand";

const navigation = [
  { to: "/", label: "Home", icon: Home },
  { to: "/library", label: "Library", icon: Library },
  { to: "/discover", label: "Discover", icon: Search },
  { to: "/activity", label: "Activity", icon: Activity },
];
const management = [
  { to: "/settings", label: "My preferences" },
  { to: "/settings/security", label: "Account & security" },
  { to: "/settings/logs", label: "Logs" },
  { to: "/settings/people", label: "People" },
  { to: "/settings/storage", label: "Storage & import" },
  { to: "/settings/defaults", label: "Household defaults" },
  { to: "/settings/server", label: "Server settings" },
  { to: "/setup", label: "Server setup" },
];

export default function Shell({
  user,
  onLogout,
  setupPending = false,
}: {
  user: User;
  onLogout: () => void;
  setupPending?: boolean;
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
      className={`sp-app${settings ? " is-settings" : ""}${watching ? " is-watching" : ""}`}
    >
      <a className="sp-skip" href="#main-content">
        Skip to content
      </a>
      {setupPending && user.welcomed && pathname !== "/setup" && (
        <div className="sp-notice" role="status">
          <span>Server setup isn’t finished.</span>
          <Link to="/setup">Continue setup</Link>
        </div>
      )}
      <header className="sp-bar">
        <Link to="/" className="sp-wordmark" aria-label="Sparrow home">
          <Mark />
          <span>sparrow</span>
        </Link>
        <nav className="sp-bar-nav" aria-label="Main navigation">
          {navigation.map(({ to, label }) => (
            <NavLink key={to} to={to} end={to === "/"}>
              {label}
            </NavLink>
          ))}
        </nav>
        <NavLink
          to="/settings"
          className="sp-me"
          aria-label={`${user.name}’s settings`}
        >
          <span className="sp-avatar">
            {user.name.slice(0, 1).toUpperCase()}
          </span>
        </NavLink>
      </header>
      <div className={settings ? "sp-settings" : "sp-stage"}>
        {settings && (
          <aside className="sp-settings-nav">
            <p className="sp-settings-who">
              <span className="sp-avatar">
                {user.name.slice(0, 1).toUpperCase()}
              </span>
              <span>
                <strong>{user.name}</strong>
                <small>
                  {user.role === "admin"
                    ? "Administrator"
                    : user.role === "viewer"
                      ? "Viewer"
                      : "Member"}
                </small>
              </span>
            </p>
            <nav aria-label="Settings navigation">
              {management
                .filter((_, index) => index < 3 || user.role === "admin")
                .map(({ to, label }) => (
                  <NavLink key={to} to={to} end>
                    {label}
                  </NavLink>
                ))}
            </nav>
            <button
              className="sp-button quiet sp-signout"
              onClick={logout}
              disabled={signingOut}
            >
              {signingOut ? "Signing out…" : "Sign out"}
            </button>
            <ErrorNote error={logoutError} />
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
      <nav className="sp-tabbar" aria-label="Mobile navigation">
        {navigation.map(({ to, label, icon: Icon }) => (
          <NavLink key={to} to={to} end={to === "/"}>
            <Icon size={20} strokeWidth={1.75} />
            <span>{label}</span>
          </NavLink>
        ))}
      </nav>
    </div>
  );
}
