import { useEffect, useRef, useState } from "react";
import {
  NavLink,
  Link,
  Outlet,
  useLocation,
  useNavigate,
} from "react-router-dom";
import { Tv, Search, Clock3, CircleUser, LogOut } from "lucide-react";
import { post, type User } from "./api";
import { ErrorNote, useSpatialNavigation } from "./ui";
import { Logotype } from "./Brand";

const navigation = [
  { to: "/", label: "Guide", icon: Tv },
  { to: "/discover", label: "Find", icon: Search },
  { to: "/activity", label: "Requests", icon: Clock3 },
];
const settingsGroups = [
  {
    label: "You",
    admin: false,
    items: [
      { to: "/settings", label: "Preferences" },
      { to: "/settings/security", label: "Account" },
      { to: "/settings/logs", label: "Logs" },
    ],
  },
  {
    label: "Household",
    admin: true,
    items: [
      { to: "/settings/people", label: "People" },
      { to: "/settings/defaults", label: "Defaults" },
    ],
  },
  {
    label: "Server",
    admin: true,
    items: [
      { to: "/settings/storage", label: "Storage" },
      { to: "/settings/server", label: "Connections" },
      { to: "/setup", label: "Setup" },
    ],
  },
];
const titles: Record<string, string> = {
  "/": "Guide",
  "/discover": "Find",
  "/activity": "Requests",
  ...Object.fromEntries(
    settingsGroups.flatMap((group) => group.items.map((i) => [i.to, i.label])),
  ),
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
  const index = useRef<HTMLElement>(null);
  const settings = pathname.startsWith("/settings") && user.welcomed;
  const watching = pathname.startsWith("/watch/");
  const guide = pathname === "/" && user.welcomed;
  useSpatialNavigation();
  useEffect(() => {
    window.scrollTo({ top: 0, behavior: "instant" });
    document.title = titles[pathname]
      ? `${titles[pathname]} · Sparrow`
      : "Sparrow";
    index.current
      ?.querySelector<HTMLElement>('[aria-current="page"]')
      ?.scrollIntoView({ block: "nearest", inline: "center" });
  }, [pathname]);
  useEffect(() => {
    const shortcut = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        navigate("/discover");
        return;
      }
      const typing = (event.target as HTMLElement).closest(
        "input, textarea, select, [contenteditable]",
      );
      if (event.key === "/" && !typing) {
        const search = document.querySelector<HTMLElement>("[data-search]");
        if (search) {
          event.preventDefault();
          search.focus();
        }
      }
    };
    window.addEventListener("keydown", shortcut);
    return () => window.removeEventListener("keydown", shortcut);
  }, [navigate]);
  const notice = setupPending && user.welcomed && pathname !== "/setup" && (
    <div className="notice" role="status">
      <span>Server setup isn’t finished.</span>
      <Link to="/setup">Continue setup</Link>
    </div>
  );
  const signOut = (
    <div className="sign-out">
      <button className="btn quiet" onClick={logout} disabled={signingOut}>
        <LogOut size={17} strokeWidth={2.25} />
        {signingOut ? "Signing out…" : "Sign out"}
      </button>
      <ErrorNote error={logoutError} />
    </div>
  );
  return (
    <div
      className={`app ${watching ? "on-air" : ""} ${guide ? "on-guide" : ""}`}
    >
      <a className="skip" href="#main-content">
        Skip to content
      </a>
      <header className="masthead">
        <div className="masthead-inner">
          <Link to="/" className="brand" aria-label="Sparrow guide">
            <Logotype />
          </Link>
          {user.welcomed && (
            <nav className="primary-nav" aria-label="Main navigation">
              {navigation.map(({ to, label }) => (
                <NavLink key={to} to={to} end={to === "/"}>
                  {label}
                </NavLink>
              ))}
            </nav>
          )}
          {user.welcomed && (
            <NavLink
              to="/settings"
              className="account"
              aria-label={`${user.name}’s settings`}
            >
              <span className="initial" aria-hidden="true">
                {user.name.slice(0, 1).toUpperCase()}
              </span>
              <span className="account-name">{user.name}</span>
            </NavLink>
          )}
        </div>
      </header>
      {!guide && !watching && notice}
      <div className={settings ? "settings-layout" : "workspace"}>
        {settings && (
          <aside className="settings-index">
            <nav aria-label="Settings navigation" ref={index}>
              {settingsGroups
                .filter((group) => !group.admin || user.role === "admin")
                .map((group) => (
                  <div className="settings-group" key={group.label}>
                    <span className="settings-group-label">{group.label}</span>
                    {group.items.map(({ to, label }) => (
                      <NavLink key={to} to={to} end>
                        {label}
                      </NavLink>
                    ))}
                  </div>
                ))}
            </nav>
            {signOut}
          </aside>
        )}
        {!user.welcomed && <div className="welcome-exit">{signOut}</div>}
        <Outlet context={{ notice: guide ? notice : null }} />
      </div>
      {user.welcomed && (
        <nav className="tabbar" aria-label="Mobile navigation">
          {[
            ...navigation,
            { to: "/settings", label: "You", icon: CircleUser },
          ].map(({ to, label, icon: Icon }) => (
            <NavLink key={to} to={to} end={to === "/"}>
              <Icon size={22} strokeWidth={2} aria-hidden="true" />
              <span>{label}</span>
            </NavLink>
          ))}
        </nav>
      )}
    </div>
  );
}
