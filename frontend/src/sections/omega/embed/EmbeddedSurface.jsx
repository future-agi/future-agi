import React, { useContext, useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import { useTheme } from "@mui/material/styles";
import { useLocation } from "react-router-dom";
import { QueryClientContext } from "@tanstack/react-query";

import { OMEGA_PRODUCT, PRODUCT_STATUS } from "../shell/rollout";
import { useOmegaProducts } from "../shell/useOmegaProducts";

import EmbedGateOverlay from "./EmbedGateOverlay";
import { FRAME_ALLOW, FRAME_CLASS } from "./frame-attributes";
import { buildFrameSrc, childPathFromParent, originOf } from "./route-sync";
import { EMBED_STATUS, useEmbedSession } from "./use-embed-session";

// Shows a private Omega surface (Studio, the EE Console) inside Future AGI in a
// frame, and speaks omega-embed/1 with it: the token goes over postMessage
// after an origin-pinned hello, never in the frame's URL.
//
// Frame attributes:
// - no `sandbox` by default: an opaque sandbox sends `Origin: null`, which
//   Studio refuses. A surface on its own origin (the Console, CP P6) may pass
//   one that keeps its origin (`allow-same-origin`);
// - allow="local-network-access; loopback-network" for local servers, unless
//   the surface names its own permissions;
// - referrerPolicy="no-referrer" so the child never sees the parent URL;
// - className "ph-no-capture rr-block" so no session replay records the frame.
//
// A surface whose child names its document (`requireDoc`, the EE Console)
// also hands the frame's load event to the session, so a document
// that reloads in place is never shown as the one that was connected.

const directHref = (src) => {
  try {
    const url = new URL(src, window.location.origin);
    return `${url.origin}${url.pathname}`;
  } catch {
    return undefined;
  }
};

// The Console opts into document-bound sessions; Studio keeps its existing
// provider-free embed contract. Without a capabilities observation, report
// unknown rather than constructing an answer or inferring hosted withholding.
export default function EmbeddedSurface(props) {
  const queryClient = useContext(QueryClientContext);
  if (props.requireDoc && queryClient) {
    return <ProductAwareSurface {...props} />;
  }
  return (
    <SurfaceFrame
      {...props}
      productAvailability={props.requireDoc ? "unknown" : undefined}
    />
  );
}

function ProductAwareSurface(props) {
  const products = useOmegaProducts();
  const status = products.statusOf(OMEGA_PRODUCT.SANDBOXES);
  let availability = "unknown";
  if (status === PRODUCT_STATUS.ADMITTED || status === PRODUCT_STATUS.PAUSED) {
    availability = status;
  } else if (products.hostedWithheld(OMEGA_PRODUCT.SANDBOXES)) {
    availability = "withheld";
  }
  return <SurfaceFrame {...props} productAvailability={availability} />;
}

function SurfaceFrame({
  src,
  expectedOrigin,
  getToken,
  getTokens,
  basePath,
  initialRoute,
  routeMode = "hash",
  theme: themeOverride,
  productAvailability,
  title,
  label = "the page",
  probePath,
  openHref,
  copy,
  onOpen,
  onStatusChange,
  onTelemetry,
  onBadge,
  helloTimeoutMs,
  noKeyTimeoutMs,
  requireGate = false,
  requireDoc = false,
  allow = FRAME_ALLOW,
  sandbox,
  sx,
}) {
  const muiTheme = useTheme();
  const location = useLocation();
  const frameRef = useRef(null);
  const origin = expectedOrigin || originOf(src);

  // Fixed for the life of the frame: later route changes travel as messages,
  // so the frame never reloads when the parent URL changes.
  const [frameSrc] = useState(() => {
    const route =
      initialRoute ??
      (basePath
        ? childPathFromParent(location.pathname, location.search, basePath)
        : "/");
    return buildFrameSrc(src, route, routeMode);
  });

  const themeMode = themeOverride || muiTheme.palette.mode;
  const { status, diagnosis, attempt, retry, onFrameLoad } = useEmbedSession({
    frameRef,
    frameSrc,
    expectedOrigin: origin,
    getToken,
    getTokens,
    basePath,
    themeMode,
    productAvailability,
    probePath,
    onOpen,
    onStatusChange,
    onTelemetry,
    onBadge,
    helloTimeoutMs,
    noKeyTimeoutMs,
    requireGate,
    requireDoc,
  });

  const direct = useMemo(() => openHref ?? directHref(src), [openHref, src]);
  const connected = status === EMBED_STATUS.connected;

  return (
    <Box
      data-testid="omega-embedded-surface"
      data-state={status}
      sx={{
        position: "relative",
        flex: "1 1 auto",
        width: 1,
        height: 1,
        minHeight: 0,
        overflow: "hidden",
        bgcolor: "background.default",
        ...sx,
      }}
    >
      <Box
        component="iframe"
        key={attempt}
        ref={frameRef}
        src={frameSrc}
        title={title || label}
        allow={allow}
        sandbox={sandbox || undefined}
        referrerPolicy="no-referrer"
        className={FRAME_CLASS}
        onLoad={onFrameLoad}
        data-testid="omega-embed-frame"
        sx={{
          position: "absolute",
          inset: 0,
          width: 1,
          height: 1,
          border: 0,
          display: "block",
          colorScheme: themeMode,
          visibility: connected ? "visible" : "hidden",
        }}
      />
      <EmbedGateOverlay
        status={status}
        diagnosis={diagnosis}
        label={label}
        copy={copy}
        openHref={direct}
        onRetry={retry}
      />
    </Box>
  );
}

EmbeddedSurface.propTypes = {
  /** The child's base URL, e.g. "http://127.0.0.1:4190/?embed=1". */
  src: PropTypes.string.isRequired,
  /** The only origin the frame may speak from; defaults to src's origin. */
  expectedOrigin: PropTypes.string,
  /** Called once per hello; returns the token, null, or a promise of either. */
  getToken: PropTypes.func.isRequired,
  /** Called once per hello with how many keys it asks for (CP P7); returns up to that many. */
  getTokens: PropTypes.func,
  /** Parent path that owns the frame; enables route sync both ways. */
  basePath: PropTypes.string,
  /** The child's first route; defaults to the parent URL under basePath. */
  initialRoute: PropTypes.string,
  /** "hash" (Studio) or "path" (Console) child routing. */
  routeMode: PropTypes.oneOf(["hash", "path"]),
  /** "light" or "dark"; defaults to the Future AGI theme. */
  theme: PropTypes.oneOf(["light", "dark"]),
  title: PropTypes.string,
  /** How the gate copy names the surface, e.g. "local Studio". */
  label: PropTypes.string,
  /** A path on the child's server for the no-hello probe. */
  probePath: PropTypes.string,
  /** "Open directly" link; defaults to src without query or fragment. */
  openHref: PropTypes.string,
  copy: PropTypes.object,
  /** Receives a same-origin URL from embed.open; default navigates in-app. */
  onOpen: PropTypes.func,
  onStatusChange: PropTypes.func,
  /** Receives each validated embed.telemetry message (CP P8). */
  onTelemetry: PropTypes.func,
  /** Receives each embed.badge Inbox count (CP P15). */
  onBadge: PropTypes.func,
  helloTimeoutMs: PropTypes.number,
  /** How long the child may take to report its gate when its word decides. */
  noKeyTimeoutMs: PropTypes.number,
  /**
   * Connected only once the child's gate says "ready"; a key handed over is
   * "checking" until then, and "needs-key" if no gate comes in time. Studio
   * and the EE Console both pass it: handing a key over proves nothing until
   * the child has checked it. Without it, a key handed over counts as
   * connected.
   */
  requireGate: PropTypes.bool,
  /**
   * The child names its document in every hello and gate (the EE
   * Console): a hello or gate without a doc is ignored, each key echoes the
   * hello's doc, a gate counts only for the current document, and a reload
   * of the frame's document goes back to connecting. Studio sends no doc yet
   * and leaves it off.
   */
  requireDoc: PropTypes.bool,
  /** The frame's permissions policy; defaults to FRAME_ALLOW (local servers). */
  allow: PropTypes.string,
  /** A sandbox for a surface on its own origin; none by default (Studio). */
  sandbox: PropTypes.string,
  sx: PropTypes.object,
};

SurfaceFrame.propTypes = {
  ...EmbeddedSurface.propTypes,
  productAvailability: PropTypes.oneOf([
    "admitted",
    "paused",
    "withheld",
    "unknown",
  ]),
};
