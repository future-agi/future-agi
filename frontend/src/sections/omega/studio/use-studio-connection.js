// Which Studio server the tab shows, and where its key comes from.
//
// A connection starts from one of four places:
//   link      the "Open in Future AGI" link, taken out of the address bar by
//             the first script in index.html (window.__omegaConnect)
//   pasted    a link pasted on the connect screen
//   saved     a saved connection (URL only; no key to recover)
//   restored  the server this tab showed before a reload (URL only)
// The key lives in a closure and is handed to the frame once, on its hello.
// It is never put in state that is stored, in a URL or in a request. The child
// also keeps it in memory only: a reload needs a freshly opened/pasted link.
//
// A server is saved, and kept as this tab's active one, only after Studio's
// gate says "ready". A saved URL is a bookmark, not a saved credential: a
// missing or refused key must not remove it.
import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import {
  EMBED_STATUS,
  clearOmegaConnect,
  hasOmegaConnectToken,
  omegaConnectGeneration,
  onOmegaConnect,
  peekOmegaConnect,
  takeOmegaConnectToken,
} from "src/sections/omega/embed";

import { normalizeStudioServer, parseStudioLink } from "./connect-link";
import {
  clearActiveConnection,
  readActiveConnection,
  rememberConnection,
  writeActiveConnection,
} from "./saved-connections";
import { STUDIO_BASE_PATH } from "./studio-copy";

// How long a closing gate stays on screen before the tab goes back to the
// connect screen, where the link can be pasted again. Both gates are final
// answers by then:
// - refused: Studio's server rejected the key.
// - needs-key: Studio has no key, as expected after a child reload or when
//   opening a saved/restored server. The host has no spent key to resend.
// The short wait still lets a freshly handed-over key finish checking.
export const GATE_GRACE_MS = 1500;

export const STORAGE_VERDICT = Object.freeze({
  remember: "remember",
  keep: "keep",
});

// Saved connections contain URLs/labels only. Gate failures describe this
// attempt's key, not the bookmark; only the user's Remove action deletes it.
export function storageVerdict(status, connection) {
  if (connection && status === EMBED_STATUS.connected)
    return STORAGE_VERDICT.remember;
  return STORAGE_VERDICT.keep;
}

let sequence = 0;

const oneShot = (token) => () => {
  const value = token || null;
  token = null;
  return value;
};

const noKey = () => null;

function connectionFor(server, getToken, source) {
  sequence += 1;
  return {
    id: sequence,
    url: server.url,
    origin: server.origin,
    getToken,
    source,
  };
}

// The connection for a link from omega dev. Its key is handed out once. A
// spent intake still identifies the server but cannot authorize a new frame;
// the child's needs-key gate will ask for a freshly opened/pasted link.
//
// The key is taken on the frame's hello, never here: this also runs inside a
// useState initializer, which StrictMode calls twice and keeps one result of.
// It is bound to the link's generation, so this connection's frame, still
// saying hello after a newer link arrived, cannot take that link's key.
function linkConnection(link) {
  const server = normalizeStudioServer(link.url);
  if (!server) return null;
  if (!hasOmegaConnectToken()) return connectionFor(server, noKey, "link");
  const generation = omegaConnectGeneration();
  return connectionFor(
    server,
    () => takeOmegaConnectToken(generation),
    "link",
  );
}

// The connection a fresh page starts with.
function fromIntakeOrRestore() {
  const link = peekOmegaConnect();
  if (link) {
    const connection = linkConnection(link);
    if (!connection) {
      clearOmegaConnect();
      return { connection: null, notice: { kind: "not-local" } };
    }
    return { connection, notice: null };
  }
  const active = normalizeStudioServer(readActiveConnection() || "");
  if (active) {
    return {
      connection: connectionFor(active, noKey, "restored"),
      notice: null,
    };
  }
  return { connection: null, notice: null };
}

export function useStudioConnection() {
  const navigate = useNavigate();
  const [state, setState] = useState(fromIntakeOrRestore);
  const [status, setStatus] = useState(EMBED_STATUS.connecting);
  const graceTimer = useRef(null);
  const current = useRef(state.connection);
  current.current = state.connection;

  const clearGrace = useCallback(() => {
    if (graceTimer.current) clearTimeout(graceTimer.current);
    graceTimer.current = null;
  }, []);

  const open = useCallback(
    (connection) => {
      clearGrace();
      setStatus(EMBED_STATUS.connecting);
      setState({ connection, notice: null });
    },
    [clearGrace],
  );

  // Forgets the server this tab showed, and the link that opened it, so
  // coming back to the tab starts from the connect screen.
  const disconnect = useCallback(
    (notice = null) => {
      clearGrace();
      clearActiveConnection();
      clearOmegaConnect();
      setState({ connection: null, notice });
      navigate(STUDIO_BASE_PATH, { replace: true });
    },
    [clearGrace, navigate],
  );

  // A link opened while this page is already showing.
  useEffect(
    () =>
      onOmegaConnect((link) => {
        if (!link) return;
        const connection = linkConnection(link);
        if (!connection) {
          clearOmegaConnect();
          clearGrace();
          setState({ connection: null, notice: { kind: "not-local" } });
          return;
        }
        open(connection);
      }),
    [open, clearGrace],
  );

  useEffect(() => clearGrace, [clearGrace]);

  const connectPasted = useCallback(
    (text) => {
      const parsed = parseStudioLink(text);
      if (!parsed.ok) return parsed;
      open(connectionFor(parsed, oneShot(parsed.token), "pasted"));
      return { ok: true };
    },
    [open],
  );

  const connectSaved = useCallback(
    (url) => {
      const server = normalizeStudioServer(url);
      if (server) open(connectionFor(server, noKey, "saved"));
    },
    [open],
  );

  // EmbeddedSurface reports every status change here. "connected" comes only
  // from Studio's gate (StudioConnectedView passes requireGate), so this is
  // the one place the tab writes a server, after its key was accepted.
  const onStatusChange = useCallback(
    (next) => {
      setStatus(next);
      const connection = current.current;
      if (!connection) return;
      const verdict = storageVerdict(next, connection);
      if (verdict === STORAGE_VERDICT.remember) {
        clearGrace();
        writeActiveConnection(connection.url);
        rememberConnection(connection.url);
        return;
      }
      if (next === EMBED_STATUS.needsKey || next === EMBED_STATUS.refused) {
        clearGrace();
        // Return to the connect screen without losing the saved URL/label.
        // A fresh link can replace this attempt during the grace period.
        graceTimer.current = setTimeout(() => {
          graceTimer.current = null;
          if (current.current?.id !== connection.id) return;
          disconnect({ kind: next, url: connection.url });
        }, GATE_GRACE_MS);
        return;
      }
      clearGrace();
    },
    [clearGrace, disconnect],
  );

  return {
    connection: state.connection,
    notice: state.notice,
    status,
    connectPasted,
    connectSaved,
    disconnect,
    onStatusChange,
  };
}
