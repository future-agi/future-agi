// A media URL a player can seek in exactly.
//
// A server that answers a media request with `200` and no `Accept-Ranges`
// makes the element unseekable (`seekable` = 0–0): setting `currentTime`
// snaps back to 0. The prototype's mock WAV endpoint is one; so are some
// signed-URL proxies. Lenses and call views need exact seeks (turns, Align
// on turns, drift correction), so they play a Blob URL of the file instead:
// one fetch per URL per session, shared by every player that asks.
import { useEffect, useMemo, useState } from "react";

const cache = new Map(); // url -> Promise<string>

const isLocal = (url) => /^(blob:|data:)/i.test(String(url ?? ""));

/** Resolve a URL to a Blob URL (cached); local URLs pass through. */
export function seekableUrl(url) {
  if (!url || isLocal(url)) return Promise.resolve(url ?? null);
  if (!cache.has(url)) {
    const p = fetch(url)
      .then((res) => {
        if (!res.ok) throw new Error(`${res.status}`);
        return res.blob();
      })
      .then((blob) => URL.createObjectURL(blob))
      .catch((err) => {
        cache.delete(url);
        throw err;
      });
    cache.set(url, p);
  }
  return cache.get(url);
}

/**
 * @param {(string|null|undefined)[]} urls
 * @returns {{urls: (string|null)[], ready: boolean, error: boolean}}
 *   `urls` holds the seekable URL per input once resolved (null until then,
 *   and null for a missing input)
 */
export function useSeekableUrls(urls) {
  const key = (urls ?? []).map((u) => u ?? "").join("|");
  const list = useMemo(() => (urls ?? []).map((u) => u ?? null), [key]); // eslint-disable-line react-hooks/exhaustive-deps
  const [state, setState] = useState({ key: null, urls: [], error: false });
  useEffect(() => {
    let alive = true;
    Promise.all(list.map((u) => (u ? seekableUrl(u) : null)))
      .then((resolved) => {
        if (alive) setState({ key, urls: resolved, error: false });
      })
      .catch(() => {
        // Fall back to the original URLs: they still play from the start.
        if (alive) setState({ key, urls: list, error: true });
      });
    return () => {
      alive = false;
    };
  }, [key, list]);
  const ready = state.key === key;
  return {
    urls: ready ? state.urls : list.map(() => null),
    ready,
    error: ready && state.error,
  };
}

export default useSeekableUrls;
