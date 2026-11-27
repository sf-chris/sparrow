import { useEffect, useState } from "react";
import {
  BrowserRouter,
  Routes,
  Route,
  Navigate,
  useLocation,
  useNavigate,
} from "react-router-dom";
import { api, post, type Auth as AuthState } from "./product/api";
import { ErrorNote, Loading, useResource } from "./product/ui";
import { Logotype } from "./product/Brand";
import Auth from "./product/Auth";
import Landing from "./product/Landing";
import Shell from "./product/Shell";
import Collection from "./product/Collection";
import Title from "./product/Title";
import Activity, { RequestDetail } from "./product/Activity";
import Preferences from "./product/Preferences";
import Security from "./product/Security";
import SparrowConnect from "./product/SparrowConnect";
import Logs from "./product/Logs";
import Storage from "./product/Storage";
import { Defaults, People, ServerSettings } from "./product/Administration";
import Discover from "./product/Discover";
import Watch from "./product/Watch";
import Onboarding from "./product/Onboarding";
import "./product/product.css";
import "./product/themes.css";
import { applyTheme } from "./product/theme";

/** A QR code from Settings carries a one-time sign-in code (in the part of
 *  the address browsers never send to servers). Spend it, or fall back to
 *  the sign-in form with the reason. */
function DeviceSignIn({
  code,
  children,
  onSuccess,
}: {
  code: string;
  children: (problem: string) => React.ReactNode;
  onSuccess: (auth: AuthState) => void;
}) {
  const navigate = useNavigate();
  const [problem, setProblem] = useState("");
  useEffect(() => {
    let live = true;
    post<AuthState>("/auth/device-link/redeem", { code })
      .then((auth) => {
        if (!live) return;
        // The code opens the page it was made on; drop it from the address.
        navigate(window.location.pathname + window.location.search, {
          replace: true,
        });
        onSuccess(auth);
      })
      .catch((e) => {
        if (!live) return;
        navigate("/login", { replace: true });
        setProblem((e as Error).message);
      });
    return () => {
      live = false;
    };
  }, [code]);
  return problem ? <>{children(problem)}</> : <Loading page label="Signing you in" />;
}

function Guest({
  needsSetup,
  onSuccess,
}: {
  needsSetup: boolean;
  onSuccess: (auth: AuthState) => void;
}) {
  const location = useLocation();
  const navigate = useNavigate();
  const hash = new URLSearchParams(location.hash.slice(1));
  const setupLink = hash.has("setup_code");
  const [signin] = useState(() => hash.get("signin"));
  if (signin)
    return (
      <DeviceSignIn code={signin} onSuccess={onSuccess}>
        {(problem) => (
          <>
            <div className="device-signin-problem">
              <ErrorNote error={problem} />
            </div>
            <Auth needsSetup={needsSetup} onSuccess={onSuccess} />
          </>
        )}
      </DeviceSignIn>
    );
  if (location.pathname === "/" && !setupLink)
    return <Landing needsSetup={needsSetup} />;
  return (
    <Auth
      needsSetup={needsSetup}
      onSuccess={(auth) => {
        if (
          ["/login", "/setup", "/join"].includes(location.pathname) ||
          setupLink
        )
          navigate("/", { replace: true });
        onSuccess(auth);
      }}
    />
  );
}

/** The guide is the library; old library links keep their filters. */
function LibraryAddress() {
  const { search } = useLocation();
  return <Navigate to={"/" + search} replace />;
}

export default function App() {
  const auth = useResource(() => api<AuthState>("/auth/status"));
  const theme = auth.data?.user?.theme;
  useEffect(() => {
    // Signed out, the device keeps the last theme it showed.
    if (theme !== undefined) applyTheme(theme);
  }, [theme]);
  useEffect(() => {
    const signedOut = () => void auth.refresh();
    window.addEventListener("sparrow:signed-out", signedOut);
    return () => window.removeEventListener("sparrow:signed-out", signedOut);
  }, [auth.refresh]);
  if (!auth.data)
    return (
      <main className={`boot ${auth.error ? "" : "splash"}`}>
        {auth.error ? (
          <ErrorNote error={auth.error} retry={auth.refresh} />
        ) : (
          <div className="splash-mark" role="status">
            <Logotype />
            <span className="sr-only">Opening Sparrow</span>
          </div>
        )}
      </main>
    );
  const user = auth.data.user;
  if (!user)
    return (
      <BrowserRouter key="guest">
        <Guest needsSetup={!!auth.data.needs_setup} onSuccess={auth.setData} />
      </BrowserRouter>
    );
  return (
    <BrowserRouter key="signed-in">
      <Routes>
        {/* Watching is full screen, outside the app's navigation. */}
        {user.welcomed && <Route path="/watch/:assetId" element={<Watch />} />}
        <Route
          element={
            <Shell
              user={user}
              setupPending={
                !!auth.data.server_setup && !auth.data.server_setup.complete
              }
              onLogout={() => {
                window.history.replaceState(null, "", "/");
                auth.setData({ user: null, preferences: null });
              }}
            />
          }
        >
          {!user.welcomed ? (
            <Route
              path="*"
              element={
                <Preferences
                  user={user}
                  welcome
                  continueSetup={
                    !!auth.data.server_setup && !auth.data.server_setup.complete
                  }
                  onChanged={auth.refresh}
                />
              }
            />
          ) : (
            <>
              <Route
                path="/"
                element={
                  auth.data.server_setup &&
                  !auth.data.server_setup.complete &&
                  !auth.data.server_setup.deferred ? (
                    <Navigate to="/setup" replace />
                  ) : (
                    <Collection user={user} />
                  )
                }
              />
              <Route path="/library" element={<LibraryAddress />} />
              <Route
                path="/title/:mediaType/:tmdbId"
                element={<Title user={user} />}
              />
              <Route path="/items/:itemId" element={<Title user={user} />} />
              <Route path="/activity" element={<Activity user={user} />} />
              <Route path="/activity/:jobId" element={<RequestDetail />} />
              <Route path="/discover" element={<Discover />} />
              <Route
                path="/settings"
                element={<Preferences user={user} onChanged={auth.refresh} />}
              />
              <Route
                path="/settings/security"
                element={<Security admin={user.role === "admin"} />}
              />
              <Route path="/settings/logs" element={<Logs user={user} />} />
              {user.role === "admin" && (
                <>
                  <Route
                    path="/setup"
                    element={<Onboarding onChanged={auth.refresh} />}
                  />
                  <Route path="/settings/computers" element={<Storage />} />
                  <Route
                    path="/settings/storage"
                    element={<Navigate to="/settings/computers" replace />}
                  />
                  <Route path="/settings/server" element={<ServerSettings />} />
                  <Route path="/settings/connect" element={<SparrowConnect />} />
                  <Route
                    path="/settings/people"
                    element={<People currentUser={user} />}
                  />
                  <Route path="/settings/defaults" element={<Defaults />} />
                </>
              )}
              <Route
                path="/search"
                element={<Navigate to="/discover" replace />}
              />
              <Route path="*" element={<Navigate to="/" replace />} />
            </>
          )}
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
