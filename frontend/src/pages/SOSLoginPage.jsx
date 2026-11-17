import { Typography } from "@mui/material";
import axiosPackage from "axios";
import React, { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { clearOnboardingRecovery, useAuthRedirectParams } from "src/auth/auth-url-intake";
import { SAML_AUTH_GENERATION_KEY, SESSION_KEYS_TO_CLEAR } from "src/auth/saml-bootstrap";
import { useAuthContext } from "src/auth/hooks";
import { paths } from "src/routes/paths";
import { ShowComponent } from "src/components/show";
import { HOST_API } from "src/config-global";

// CR-07d: the SOS link carries only a one-time code. It is
// redeemed by a JSON POST and both tokens come from that response body. These
// URL keys once carried a live session; they are never read, only removed
// from the address bar.
const LEGACY_TOKEN_PARAMS = [
  "access",
  "refresh",
  "sso_token",
  "access_token",
  "refresh_token",
];
const HANDOFF_URL_PARAMS = ["code", "kind"];
const HANDOFF_REDEEM_PATH = "/accounts/handoff/redeem/";

function urlParamName(pair) {
  const raw = pair.split("=", 1)[0].replace(/\+/g, " ");
  try {
    return decodeURIComponent(raw);
  } catch {
    return raw;
  }
}

// Remove `keys` from the address bar, keeping every other byte of the URL.
function removeUrlParams(keys) {
  try {
    const url = new URL(window.location.href);
    if (!url.search) return;
    const kept = url.search
      .slice(1)
      .split("&")
      .filter((pair) => !keys.includes(urlParamName(pair)));
    const search = kept.length ? `?${kept.join("&")}` : "";
    if (search === url.search) return;
    window.history.replaceState(
      window.history.state,
      "",
      `${url.origin}${url.pathname}${search}${url.hash}`,
    );
  } catch {
    // A refused history update must never block the SOS sign-in.
  }
}

// Raw axios: no stale Authorization header, no refresh-on-401 interceptor and
// no cookies ride along with the code.
async function redeemSosCode(code) {
  const response = await axiosPackage.post(
    `${HOST_API}${HANDOFF_REDEEM_PATH}`,
    { code, kind: "sos" },
    { headers: { "Content-Type": "application/json" } },
  );
  const access = response?.data?.access;
  const refresh = response?.data?.refresh;
  if (
    typeof access !== "string" ||
    !access ||
    typeof refresh !== "string" ||
    !refresh
  ) {
    throw new Error("SOS handoff refused");
  }
  return { access, refresh };
}

const SOSLoginPage = () => {
  const searchParams = useAuthRedirectParams();
  // Read once from the reader's snapshot; the address bar loses it at once.
  const [code] = useState(() => searchParams.get("code"));
  const [status, setStatus] = useState(code ? "redeeming" : "refused");
  const navigate = useNavigate();
  const { logout } = useAuthContext();
  const pendingSession = useRef(null);
  const redeemStarted = useRef(false);
  const mounted = useRef(false);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  useEffect(() => {
    removeUrlParams([...LEGACY_TOKEN_PARAMS, ...HANDOFF_URL_PARAMS]);
    // The code is held above; drop the in-memory handoff (and any legacy
    // token the first-script intake stashed) now rather than at unmount.
    searchParams.clear();
    if (!code || redeemStarted.current) return;
    redeemStarted.current = true;
    redeemSosCode(code).then(
      ({ access, refresh }) => {
        if (!mounted.current) return;
        // Do not expose B to the old document's provider. Its in-flight
        // initialize(A) can still finish and write A until this document exits.
        pendingSession.current = { access, refresh };
        setStatus("ready");
      },
      () => {
        if (mounted.current) setStatus("refused");
      },
    );
  }, [code, searchParams]);

  useEffect(() => {
    if (status !== "ready") {
      return undefined;
    }
    // The outgoing page's final synchronous commit wins over initialize(A).
    // A document replacement retires that provider and all its pending work;
    // the dashboard's fresh provider initializes only the redeemed session B.
    const commitAtExit = () => {
      const session = pendingSession.current;
      if (!session) return;
      pendingSession.current = null;
      clearOnboardingRecovery();
      const generation = crypto.randomUUID();
      sessionStorage.setItem(SAML_AUTH_GENERATION_KEY, generation);
      localStorage.setItem(SAML_AUTH_GENERATION_KEY, generation);
      SESSION_KEYS_TO_CLEAR.forEach((key) => sessionStorage.removeItem(key));
      sessionStorage.removeItem("fai_saml_next");
      localStorage.removeItem("rememberMe");
      localStorage.removeItem("redirectUrl");
      localStorage.setItem("initial-render", "done");
      localStorage.setItem("sosMode", "true");
      localStorage.setItem("accessToken", session.access);
      localStorage.setItem("refreshToken", session.refresh);
    };
    window.addEventListener("pagehide", commitAtExit, { once: true });
    searchParams.clear();
    window.location.replace("/dashboard/develop");
    return () => window.removeEventListener("pagehide", commitAtExit);
  }, [status, searchParams]);

  const signInAgain = async () => {
    try {
      // A previous browser session must not bounce back through GuestGuard.
      await logout();
    } catch {
      // Local credentials are cleared before logout's remote request.
    }
    if (mounted.current) navigate(paths.auth.jwt.login, { replace: true });
  };

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        height: "100vh",
      }}
    >
      <h2>
        {status === "refused" ? "SOS link unavailable" : "SOS Mode Activating..."}
      </h2>
      {status !== "refused" && <p>Setting up emergency access...</p>}
      <div
        style={{
          marginTop: "20px",
          padding: "10px",
          backgroundColor: "var(--bg-default)",
          borderRadius: "5px",
          fontSize: "12px",
        }}
      >
        <ShowComponent condition={status === "refused"}>
          <div role="alert">
            <Typography typography="m2">Link expired, sign in again.</Typography>
            <p>This SOS link is invalid, already used or expired.</p>
            <p>Ask your support operator for a fresh SOS link.</p>
            <button type="button" onClick={signInAgain}>
              Sign in again
            </button>
          </div>
        </ShowComponent>
        <ShowComponent condition={status === "redeeming"}>
          <Typography typography="s1">Verifying the SOS link...</Typography>
        </ShowComponent>
        <ShowComponent condition={status === "ready"}>
          <Typography typography="s1">
            Page will refresh automatically...
          </Typography>
        </ShowComponent>
      </div>
    </div>
  );
};

export default SOSLoginPage;
