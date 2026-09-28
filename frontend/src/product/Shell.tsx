import { useEffect, useState } from "react";
import { NavLink, Link, Outlet, useLocation, useNavigate } from "react-router-dom";
import { Home, Library, Search, Ticket, LogOut } from "lucide-react";
import { post, type User } from "./api";
import { ErrorNote } from "./ui";
import { Wordmark } from "./Brand";

const navigation = [
  { to: "/", label: "Home", icon: Home },
  { to: "/library", label: "Library", icon: Library },
  { to: "/discover", label: "Find", icon: Search },
  { to: "/activity", label: "Requests", icon: Ticket },
];
const management = [
  { to: "/settings", label: "Preferences" },
  { to: "/settings/security", label: "Account & security" },
  { to: "/settings/logs", label: "Activity log" },
  { to: "/settings/people", label: "People", admin: true },
  { to: "/settings/storage", label: "Storage & import", admin: true },
  { to: "/settings/defaults", label: "Household defaults", admin: true },
  { to: "/settings/server", label: "Server settings", admin: true },
  { to: "/setup", label: "Server setup", admin: true },
];
const titles: Record<string, string> = {
  "/": "Home",
  "/discover": "Find",
  "/activity": "Requests",
  "/library": "Library",
};

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
  const settingsLinks = management.filter((link) => !link.admin || user.role === "admin");
  useEffect(() => {
    window.scrollTo({ top: 0, behavior: "instant" });
    const current =
      titles[pathname] || management.find((item) => item.to === pathname)?.label;
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
  const signOut = (
    <button className="sp-btn sp-btn-ghost" onClick={logout} disabled={signingOut}>
      <LogOut size={16} aria-hidden="true" />
      {signingOut ? "Signing out…" : "Sign out"}
    </button>
  );
  return (
    <div className={`sp-app ${watching ? "sp-app-watching sp-dark" : ""}`}>
      <a className="sp-skip" href="#main-content">
        Skip to content
      </a>
      <header className="sp-bar">
        <div className="sp-bar-inner">
          <Link to="/" className="sp-bar-brand" aria-label="Sparrow home">
            <Wordmark />
          </Link>
          {user.welcomed && (
            <nav className="sp-bar-nav" aria-label="Main navigation">
              {navigation.map(({ to, label }) => (
                <NavLink key={to} to={to} end={to === "/"}>
                  {label}
                </NavLink>
              ))}
            </nav>
          )}
          {user.welcomed ? (
            <NavLink
              to="/settings"
              className={() =>
                `sp-bar-account ${pathname.startsWith("/settings") || pathname === "/setup" ? "active" : ""}`
              }
              aria-label={`${user.name}’s settings`}
            >
              <span className="sp-avatar" aria-hidden="true">
                {user.name.slice(0, 1).toUpperCase()}
              </span>
              <span className="sp-bar-account-name">{user.name}</span>
            </NavLink>
          ) : (
            <div className="sp-bar-account-out">
              {signOut}
            </div>
          )}
        </div>
      </header>
      {!user.welcomed && <ErrorNote error={logoutError} />}
      {setupPending && user.welcomed && pathname !== "/setup" && (
        <div className="sp-notice-strip" role="status">
          <span>Server setup isn’t finished.</span>
          <Link to="/setup">Continue setup</Link>
        </div>
      )}
      {settings ? (
        <div className="sp-settings">
          <aside className="sp-settings-index">
            <p className="sp-label">Settings</p>
            <nav aria-label="Settings navigation">
              <ol>
                {settingsLinks.map(({ to, label }) => (
                  <li key={to}>
                    <NavLink to={to} end>
                      {label}
                    </NavLink>
                  </li>
                ))}
              </ol>
            </nav>
            <div className="sp-settings-signout">
              {signOut}
              <ErrorNote error={logoutError} />
            </div>
          </aside>
          <Outlet />
        </div>
      ) : (
        <Outlet />
      )}
      {user.welcomed && (
        <nav className="sp-dock" aria-label="Mobile navigation">
          {navigation.map(({ to, label, icon: Icon }) => (
            <NavLink key={to} to={to} end={to === "/"}>
              <Icon size={20} strokeWidth={1.8} aria-hidden="true" />
              <span>{label}</span>
            </NavLink>
          ))}
        </nav>
      )}
    </div>
  );
}
