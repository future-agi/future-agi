import { HOST_API } from 'src/config-global';

const isLoopback = (hostname) => hostname === 'localhost' || hostname === '127.0.0.1';

/** Resolve the API address, never the frontend origin. Fail closed if the
 * highest-precedence configured address is invalid instead of falling back.
 */
export function publicApiOrigin() {
  const runtime = typeof window !== 'undefined' ? window.__FUTURE_AGI_CONFIG__ : undefined;
  const configured = runtime?.VITE_PUBLIC_API_ORIGIN || import.meta.env.VITE_PUBLIC_API_ORIGIN || HOST_API;
  try {
    const url = new URL(configured);
    const localDevelopment = url.protocol === 'http:' && isLoopback(url.hostname) && typeof window !== 'undefined' && isLoopback(window.location.hostname);
    if ((url.protocol !== 'https:' && !localDevelopment) || url.username || url.password || url.search || url.hash) {
      return { origin: null, error: 'not_https' };
    }
    return { origin: url.origin, error: null };
  } catch {
    return { origin: null, error: 'not_https' };
  }
}
