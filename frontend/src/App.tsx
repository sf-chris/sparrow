import { useEffect } from "react";
import {
  BrowserRouter,
  Routes,
  Route,
  Navigate,
  useLocation,
  useNavigate,
} from "react-router-dom";
import { api, type Auth as AuthState } from "./product/api";
import { ErrorNote, Loading, useResource } from "./product/ui";
import Auth from "./product/Auth";
import Landing from "./product/Landing";
import Shell from "./product/Shell";
import Collection from "./product/Collection";
import Title from "./product/Title";
import Activity from "./product/Activity";
import Preferences from "./product/Preferences";
import Storage from "./product/Storage";
import { Defaults, People, ServerSettings } from "./product/Administration";
import Discover from "./product/Discover";
import Watch from "./product/Watch";
import "./product/product.css";

function Guest({
  needsSetup,
  onSuccess,
}: {
  needsSetup: boolean;
  onSuccess: (auth: AuthState) => void;
}) {
  const location = useLocation();
  const navigate = useNavigate();
  const setupLink = new URLSearchParams(location.hash.slice(1)).has(
    "setup_code",
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

export default function App() {
  const auth = useResource(() => api<AuthState>("/auth/status"));
  useEffect(() => {
    const signedOut = () => void auth.refresh();
    window.addEventListener("sparrow:signed-out", signedOut);
    return () => window.removeEventListener("sparrow:signed-out", signedOut);
  }, [auth.refresh]);
  if (!auth.data)
    return (
      <main className="sp-auth">
        {auth.error ? (
          <ErrorNote error={auth.error} retry={auth.refresh} />
        ) : (
          <Loading label="Connecting to Sparrow…" />
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
        <Route element={<Shell user={user} />}>
          {!user.welcomed ? (
            <Route
              path="*"
              element={
                <Preferences
                  user={user}
                  welcome
                  onChanged={auth.refresh}
                  onLogout={() => {
                    window.history.replaceState(null, "", "/");
                    void auth.refresh();
                  }}
                />
              }
            />
          ) : (
            <>
              <Route path="/" element={<Collection user={user} home />} />
              <Route path="/library" element={<Collection user={user} />} />
              <Route
                path="/title/:mediaType/:tmdbId"
                element={<Title user={user} />}
              />
              <Route path="/items/:itemId" element={<Title user={user} />} />
              <Route path="/watch/:assetId" element={<Watch />} />
              <Route path="/activity" element={<Activity />} />
              <Route path="/discover" element={<Discover />} />
              <Route
                path="/settings"
                element={
                  <Preferences
                    user={user}
                    onChanged={auth.refresh}
                    onLogout={() => {
                      window.history.replaceState(null, "", "/");
                      void auth.refresh();
                    }}
                  />
                }
              />
              {user.role === "admin" && (
                <>
                  <Route path="/settings/storage" element={<Storage />} />
                  <Route path="/settings/server" element={<ServerSettings />} />
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
