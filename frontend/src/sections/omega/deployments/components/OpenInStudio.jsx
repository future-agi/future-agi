import React, { useId } from "react";
import PropTypes from "prop-types";
import { useNavigate } from "react-router-dom";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Link from "@mui/material/Link";
import Typography from "@mui/material/Typography";

import {
  OMEGA_PART,
  OMEGA_PRODUCT,
  PRODUCT_STATUS,
} from "src/sections/omega/shell/rollout";
import { useOmegaPart } from "src/sections/omega/shell/useOmegaPart";
import { useOmegaProductStatus } from "src/sections/omega/shell/useOmegaProducts";

import { openInStudio } from "../lib/links";
import { studioPageHref } from "../lib/reach";
import { useAccessToken } from "../lib/useAccessToken";
import DeployIcon from "./DeployIcon";

/**
 * "Open in Studio": the Studio tab, connected to this deployment's server
 * with its access token, handed over in memory (lib/links.js openInStudio),
 * never in a URL. Without a token in this tab there is nothing to hand over,
 * so the button says why and the Studio page itself stays one click away.
 *
 * The button shows only when it works: the Studio tab is open to this
 * workspace AND a deployed server opens inside it (OMEGA_PART
 * DEPLOYMENT_STUDIO, DS:W4-FA-STUDIO-ATTACH). Today a deployed server refuses
 * to be framed, so only the "Studio page" link shows: the page the deployment
 * serves itself, where its entries run.
 *
 * That page is on the deployment's own origin and reads its access token only
 * from its own address, and no token goes into a URL
 * (OD-21). So the link opens the page's key gate, and the line beside it says
 * so: copy the token from Access token above, or rotate it under Settings
 * when this tab no longer holds it (DEMO-FIX-2 item 1).
 */
/**
 * The line beside the Studio page link: that page asks for the access token,
 * and where to get it. Without a token in this tab, Open in Studio (when it
 * shows) waits for the same token, so one line says so for both.
 */
function gateLine({ token, openToo, page }) {
  if (!page) {
    // No Studio page link (a host-only deployment): only a disabled Open in
    // Studio needs a word, and only while this tab holds no token.
    return token || !openToo
      ? null
      : "Open in Studio needs the access token shown when this deployment was created. Rotate it under Settings to get a new one.";
  }
  if (token) {
    return "The Studio page asks for this deployment's access token: copy it from Access token above.";
  }
  const asks = openToo
    ? "Open in Studio and the Studio page need"
    : "The Studio page asks for";
  return `${asks} the access token shown when this deployment was created. Rotate it under Settings to get a new one.`;
}

export default function OpenInStudio({
  deployment,
  variant = "outlined",
  withHint = true,
  tokenOnScreen = false,
}) {
  const token = useAccessToken(deployment.id);
  const navigate = useNavigate();
  const gateId = useId();
  const attachable = useOmegaPart(OMEGA_PART.DEPLOYMENT_STUDIO);
  const studioTab = useOmegaProductStatus(OMEGA_PRODUCT.STUDIO_TAB);
  const canOpen = attachable && studioTab === PRODUCT_STATUS.ADMITTED;
  if (!deployment.studioUrl || !deployment.url) return null;
  // A host-only deployment's Studio page is linked only where its token is on
  // screen beside the link and held by this tab (the New deployment result).
  const studioPage = studioPageHref(deployment, {
    tokenOnScreen: tokenOnScreen && Boolean(token),
  });
  if (!canOpen && !studioPage) return null;
  const line = gateLine({
    token,
    openToo: canOpen && withHint,
    page: Boolean(studioPage),
  });
  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        gap: 0.5,
        alignItems: "flex-start",
      }}
    >
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 1.5,
          flexWrap: "wrap",
        }}
      >
        {canOpen && (
          <Button
            size="small"
            variant={variant}
            color={variant === "contained" ? "primary" : "inherit"}
            disabled={!token}
            onClick={() => openInStudio(deployment.url, token, navigate)}
            startIcon={<DeployIcon name="studio" size={16} />}
            data-testid="open-in-studio"
          >
            Open in Studio
          </Button>
        )}
        {studioPage && (
          <Link
            href={studioPage}
            target="_blank"
            rel="noopener noreferrer"
            underline="hover"
            aria-describedby={gateId}
            data-gate="access-token"
            sx={{
              typography: "s2",
              display: "inline-flex",
              alignItems: "center",
              gap: 0.5,
            }}
            data-testid="open-studio-page"
          >
            Studio page
            <DeployIcon name="external" size={13} />
          </Link>
        )}
      </Box>
      {line && (
        <Typography
          id={gateId}
          typography="s2"
          color="text.secondary"
          data-testid="studio-page-gate"
          data-token-held={token ? "true" : "false"}
        >
          {line}
        </Typography>
      )}
    </Box>
  );
}

OpenInStudio.propTypes = {
  deployment: PropTypes.shape({
    id: PropTypes.string.isRequired,
    url: PropTypes.string,
    studioUrl: PropTypes.string,
    urlReach: PropTypes.string,
  }).isRequired,
  variant: PropTypes.string,
  withHint: PropTypes.bool,
  tokenOnScreen: PropTypes.bool,
};
