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
// `hasKey` says whether this connection had a key to hand over at all, so the
// page can say from the start that a keyless one will ask for the link.
//
// A server is saved, and kept as this tab's active one, only after Studio's
// gate says "ready". A saved URL is a bookmark, not a saved credential: a
// missing or refused key must not remove it.
//
// When Studio asks for the link again (needs-key, refused), the tab goes back
// to the connect screen but keeps the address of the Studio page the person
// was on, so pasting the link reopens it. Only Disconnect goes to the base
// path.
import { useCallback, useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import {
  EMBED_STATUS,
  clearOmegaConnect,
  hasOmegaConnectToken,
  omegaConnectGeneration,
  onOmegaConnect,
  peekOmegaConnect,
  takeOmegaConnectToken,
} from "src/sections/omega/embed";

import {
  normalizeStudioServer,
  parseStudioLink,
  serverDisplayName,
} from "./connect-link";
import {
  clearActiveConnection,
  readActiveConnection,
  readSavedConnections,
  rememberConnection,
  writeActiveConnection,
} from "./saved-connections";
import { STUDIO_BASE_PATH } from "./studio-copy";

// How long a closing gate stays on screen before the tab goes back to the
// connect screen, where the link can be pasted again and the same words stay
// as the notice. Both gates are final answers by then:
// - refused: Studio's server rejected the key.
// - needs-key: Studio has no key, as expected after a child reload or when
//   opening a saved/restored server. The host has no spent key to resend.
// Long enough to read the panel's one sentence and use its button; a freshly
// handed-over key can still replace the attempt meanwhile.
export const GATE_GRACE_MS = 3000;

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

// The Studio page this tab last showed while connected (an address under
// /dashboard/studio, never a key), so a person who left for another Future AGI
// page comes back to it once they paste the link again. Per tab.
const LAST_PAGE_KEY = "omega.studio.page";

const sessionStore = () => {
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
};

function rememberPage(url, path) {
  try {
    sessionStore()?.setItem(LAST_PAGE_KEY, JSON.stringify({ url, path }));
  } catch {
    /* not remembered */
  }
}

function readLastPage() {
  try {
    const value = JSON.parse(sessionStore()?.getItem(LAST_PAGE_KEY));
    if (typeof value?.url !== "string" || typeof value?.path !== "string")
      return null;
    return value;
  } catch {
    return null;
  }
}

function forgetPage() {
  try {
    sessionStore()?.removeItem(LAST_PAGE_KEY);
  } catch {
    /* nothing to forget */
  }
}

// A Studio page deeper than the tab's home: /dashboard/studio/t/th_1, or the
// base path with a query. Null for the home page or anything outside Studio.
function studioPage(pathname, search = "") {
  if (typeof pathname !== "string") return null;
  const underBase =
    pathname === STUDIO_BASE_PATH ||
    pathname.startsWith(`${STUDIO_BASE_PATH}/`);
  if (!underBase) return null;
  const home = pathname.replace(/\/+$/, "") === STUDIO_BASE_PATH;
  const query = search && search !== "?" ? search : "";
  if (home && !query) return null;
  return `${pathname}${query}`;
}

// What the connect screen says after a gate closed this connection. A refused
// key stays the reason even if the child then says hello again and reports
// needs-key: that would hide why it failed.
const gateKind = (status, connection, refusedConnectionId) =>
  status === EMBED_STATUS.refused || refusedConnectionId === connection.id
    ? EMBED_STATUS.refused
    : EMBED_STATUS.needsKey;

let sequence = 0;

const oneShot = (token) => () => {
  const value = token || null;
  token = null;
  return value;
};

const noKey = () => null;

// The name a person gave this server in Saved connections, when they renamed
// it; the panel and notice use it next to the address.
function savedLabel(url) {
  const saved = readSavedConnections().find((item) => item.url === url);
  return saved && saved.label !== serverDisplayName(url) ? saved.label : null;
}

function connectionFor(server, getToken, source, hasKey = false) {
  sequence += 1;
  return {
    id: sequence,
    url: server.url,
    origin: server.origin,
    getToken,
    source,
    hasKey,
    label: savedLabel(server.url),
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
    true,
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
  const location = useLocation();
  const [state, setState] = useState(fromIntakeOrRestore);
  const [status, setStatus] = useState(EMBED_STATUS.connecting);
  const graceTimer = useRef(null);
  const current = useRef(state.connection);
  current.current = state.connection;
  const here = useRef(location);
  here.current = location;
  const statusNow = useRef(status);
  statusNow.current = status;
  // The connection whose key Studio refused: a needs-key that follows (the
  // child said hello again) must not hide why it failed.
  const refusedId = useRef(null);

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

  // The Disconnect button: forgets the server this tab showed, the link that
  // opened it and the page it was on, so coming back to the tab starts from
  // the connect screen.
  const disconnect = useCallback(
    (notice = null) => {
      clearGrace();
      clearActiveConnection();
      clearOmegaConnect();
      forgetPage();
      setState({ connection: null, notice });
      navigate(STUDIO_BASE_PATH, { replace: true });
    },
    [clearGrace, navigate],
  );

  // The page to come back to once the link is pasted again: the one the
  // address bar names, or, after leaving Studio and opening its home again,
  // the one this tab last showed for the same server.
  const keepPage = useCallback(
    (connection) => {
      const { pathname, search } = here.current;
      const named = studioPage(pathname, search);
      if (named) return named;
      const last = readLastPage();
      if (last?.url !== connection.url) return null;
      const cut = last.path.indexOf("?");
      const page =
        cut === -1
          ? studioPage(last.path)
          : studioPage(last.path.slice(0, cut), last.path.slice(cut));
      if (page) navigate(page, { replace: true });
      return page;
    },
    [navigate],
  );

  // Studio needs the link again (needs-key, refused, or the person chose to
  // paste it now): back to the connect screen, keeping the saved URL/label
  // and the address of the page they were on.
  const fallBack = useCallback(
    (connection, kind) => {
      clearGrace();
      clearActiveConnection();
      clearOmegaConnect();
      const returnTo = keepPage(connection);
      setState({
        connection: null,
        notice: {
          kind,
          url: connection.url,
          label: connection.label,
          source: connection.source,
          hasKey: connection.hasKey,
          returnTo,
        },
      });
    },
    [clearGrace, keepPage],
  );

  // The gate panel's "Paste the link": go to the paste field now.
  const pasteLink = useCallback(() => {
    const connection = current.current;
    if (!connection) return;
    fallBack(
      connection,
      gateKind(statusNow.current, connection, refusedId.current),
    );
  }, [fallBack]);

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

  // While Studio is connected, the page it shows is the one to come back to.
  useEffect(() => {
    if (status !== EMBED_STATUS.connected || !state.connection) return;
    rememberPage(
      state.connection.url,
      `${location.pathname}${location.search}`,
    );
  }, [status, state.connection, location.pathname, location.search]);

  const connectPasted = useCallback(
    (text) => {
      const parsed = parseStudioLink(text);
      if (!parsed.ok) return parsed;
      open(
        connectionFor(
          parsed,
          oneShot(parsed.token),
          "pasted",
          Boolean(parsed.token),
        ),
      );
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
      if (next === EMBED_STATUS.refused) refusedId.current = connection.id;
      if (next === EMBED_STATUS.needsKey || next === EMBED_STATUS.refused) {
        clearGrace();
        const kind = gateKind(next, connection, refusedId.current);
        // Return to the connect screen without losing the saved URL/label.
        // A fresh link can replace this attempt during the grace period.
        graceTimer.current = setTimeout(() => {
          graceTimer.current = null;
          if (current.current?.id !== connection.id) return;
          fallBack(connection, kind);
        }, GATE_GRACE_MS);
        return;
      }
      clearGrace();
    },
    [clearGrace, fallBack],
  );

  return {
    connection: state.connection,
    notice: state.notice,
    status,
    connectPasted,
    connectSaved,
    disconnect,
    pasteLink,
    onStatusChange,
  };
}
