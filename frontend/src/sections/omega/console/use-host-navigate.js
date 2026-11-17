// The Console's own product menu has a "Future AGI" entry. Picking it posts
// {type: 'embed.navigate', path} from the frame to this page, which then moves
// Future AGI to that path (by default /dashboard).
//
// The embed bridge only speaks the parent-to-child direction of
// embed.navigate, so this page listens for the child-to-parent one itself,
// with the same rules the bridge applies to every child message: the message
// must come from the frame's own window AND from the frame's expected origin,
// only after the handshake has given the frame its key, and the path must be
// a plain same-site path (normalizeEmbedPath). Anything else is ignored.
import { useEffect, useRef } from "react";
import { useNavigate } from "react-router-dom";

import { EMBED_MESSAGE, normalizeEmbedPath } from "src/sections/omega/embed";

const FRAME_SELECTOR = '[data-testid="omega-embed-frame"]';

/**
 * @param {object} options
 * @param {{ current: HTMLElement | null }} options.containerRef  wraps the EmbeddedSurface
 * @param {string | null} options.expectedOrigin  the only origin the frame may speak from
 * @param {boolean} options.enabled  true once the frame holds its key
 * @param {Window} [options.hostWindow]
 */
export function useHostNavigateMessages({
  containerRef,
  expectedOrigin,
  enabled,
  hostWindow = window,
}) {
  const navigate = useNavigate();
  const latest = useRef({ navigate, enabled });
  latest.current = { navigate, enabled };

  useEffect(() => {
    if (!expectedOrigin) return undefined;
    const onMessage = (event) => {
      if (!latest.current.enabled) return;
      const data = event.data;
      if (!data || typeof data !== "object") return;
      if (data.type !== EMBED_MESSAGE.navigate) return;
      if (event.origin !== expectedOrigin) return;
      const frame = containerRef.current?.querySelector(FRAME_SELECTOR);
      if (!frame?.contentWindow || event.source !== frame.contentWindow) return;
      const path = normalizeEmbedPath(data.path);
      if (!path) return;
      latest.current.navigate(path);
    };
    hostWindow.addEventListener("message", onMessage);
    return () => hostWindow.removeEventListener("message", onMessage);
  }, [containerRef, expectedOrigin, hostWindow]);
}
