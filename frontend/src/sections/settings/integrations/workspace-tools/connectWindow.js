import { useCallback, useEffect, useRef, useState } from "react";

/**
 * The provider's page (GitHub's install page, Slack's consent page) opens in
 * its own window, as it does for real, and reports back with postMessage —
 * standing in for the redirect to our setup / callback URL.
 */
const MSG = "fagi-proto-connect";

export const CONNECT_PATHS = {
  github: "/integrations-connect/github-install",
};

const features = () => {
  const w = 720;
  const h = 780;
  const left = Math.max(0, window.screenX + (window.outerWidth - w) / 2);
  const top = Math.max(0, window.screenY + (window.outerHeight - h) / 2);
  return `popup,width=${w},height=${h},left=${left},top=${top}`;
};

/** In the provider window: tell Future AGI what happened, then close. */
export const replyToOpener = (payload) => {
  if (!window.opener) return false;
  window.opener.postMessage({ type: MSG, ...payload }, window.location.origin);
  setTimeout(() => window.close(), 60);
  return true;
};

/**
 * In Future AGI: open the provider window and hear back from it.
 * `onResult` gets the window's message, or { outcome: "closed" } when the
 * person closes the window without finishing (no redirect ever comes).
 */
export function useConnectWindow(onResult) {
  const win = useRef(null);
  const settled = useRef(false);
  /* Several screens mount this; only the one that opened the window listens. */
  const mine = useRef(false);
  const handler = useRef(onResult);
  handler.current = onResult;
  const [status, setStatus] = useState("idle"); // idle | open | blocked

  useEffect(() => {
    const onMessage = (e) => {
      if (
        e.origin !== window.location.origin ||
        e.data?.type !== MSG ||
        !mine.current
      )
        return;
      mine.current = false;
      settled.current = true;
      setStatus("idle");
      handler.current(e.data);
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, []);

  useEffect(() => {
    if (status !== "open") return undefined;
    const t = setInterval(() => {
      if (!win.current?.closed) return;
      clearInterval(t);
      setStatus("idle");
      /* Give a just-sent message the chance to land first. */
      setTimeout(() => {
        if (settled.current) return;
        mine.current = false;
        handler.current({ outcome: "closed" });
      }, 150);
    }, 400);
    return () => clearInterval(t);
  }, [status]);

  const open = useCallback((url) => {
    settled.current = false;
    mine.current = true;
    const w = window.open(url, "fagi-connect", features());
    if (!w) {
      setStatus("blocked");
      return;
    }
    win.current = w;
    w.focus();
    setStatus("open");
  }, []);
  const focus = useCallback(() => win.current?.focus(), []);
  const close = useCallback(() => {
    settled.current = true; // closing it ourselves isn't the person cancelling on the provider
    mine.current = false;
    win.current?.close();
    setStatus("idle");
  }, []);

  return { open, focus, close, status };
}
