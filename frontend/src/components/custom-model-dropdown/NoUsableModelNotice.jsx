import React from "react";
import PropTypes from "prop-types";
import { Link, Typography } from "@mui/material";
import { Link as RouterLink, useInRouterContext } from "react-router-dom";
import { NO_USABLE_MODEL_COPY as COPY } from "./constants";
import {
  AI_PROVIDERS_HREF,
  CREDITS_ROUTE,
  creditsCopy,
  creditsLinkHref,
} from "./noUsableModel";

// One line that says what works when no model in a menu can run here: the
// user's own provider key, and, when the organization's Future AGI credits
// are on, the gateway path this deployment serves (Try it, or an API key).
const NoUsableModelNotice = ({
  creditsRoute = CREDITS_ROUTE.NONE,
  model,
  onLinkClick,
  sx,
  ...rest
}) => {
  const inRouter = useInRouterContext();
  const linkProps = (href) => ({
    ...(inRouter ? { component: RouterLink, to: href } : { href }),
    underline: "hover",
    onClick: onLinkClick,
  });
  const credits = creditsCopy(creditsRoute);
  const creditsHref = credits ? creditsLinkHref(creditsRoute, model) : null;
  return (
    <Typography
      component="p"
      data-testid="no-usable-model-notice"
      sx={{
        fontSize: "12px",
        lineHeight: "16px",
        color: "text.secondary",
        ...sx,
      }}
      {...rest}
    >
      {COPY.START}
      <Link {...linkProps(AI_PROVIDERS_HREF)}>{COPY.PROVIDERS_LINK}</Link>
      {COPY.PROVIDERS_END}
      {credits && creditsHref ? (
        <>
          {credits.start}
          <Link {...linkProps(creditsHref)}>{credits.link}</Link>
          {credits.end}
        </>
      ) : (
        COPY.END
      )}
    </Typography>
  );
};

NoUsableModelNotice.propTypes = {
  creditsRoute: PropTypes.oneOf(Object.values(CREDITS_ROUTE)),
  // The one model the line is about, for its Try it link.
  model: PropTypes.shape({
    provider: PropTypes.string,
    model: PropTypes.string,
  }),
  onLinkClick: PropTypes.func,
  sx: PropTypes.object,
};

export default NoUsableModelNotice;
