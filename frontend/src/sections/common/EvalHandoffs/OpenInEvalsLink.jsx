// KH integration (H-1 · EV-07, EV-NA1): "Open in Evals" at the run's version.
//   text  `Open in Evals ↗` (hover card, results drawer score card)
//   chip  wraps the header's version chip; tooltip `Open Correctness v2 in Evals`
// No template id = no link (the chip renders as it was; the text renders
// nothing). The href is checked against the template's versions.
import React from "react";
import PropTypes from "prop-types";
import { Box, Link, Typography } from "@mui/material";
import { Link as RouterLink } from "react-router-dom";
import CustomTooltip from "src/components/tooltip";
import { useEvalLink } from "./useEvalTemplateRef";

const stop = (e) => e.stopPropagation();

export default function OpenInEvalsLink({
  datasetId,
  metricKey,
  def,
  name,
  version,
  variant = "text",
  showVersion = false,
  children,
}) {
  const link = useEvalLink({
    datasetId,
    metricKey: metricKey ?? def?.key,
    def,
    name,
    version,
  });
  if (!link) {
    if (variant === "chip") return children ?? null;
    return showVersion && version ? (
      <Typography component="span" typography="s3" color="text.secondary">
        {version}
      </Typography>
    ) : null;
  }
  const common = {
    component: RouterLink,
    to: link.href,
    onClick: stop,
    onMouseDown: stop,
    "data-testid": `open-in-evals-${variant}`,
    "data-href": link.href,
    "data-version-missing": link.missing ? "true" : "false",
    "aria-label": link.tooltip,
  };
  if (variant === "chip")
    return (
      <CustomTooltip
        show
        title={link.tooltip}
        size="small"
        placement="top"
        arrow
      >
        <Link
          {...common}
          // The header's own tooltip stays shut while the chip's is open.
          onMouseOver={stop}
          underline="none"
          sx={{ display: "inline-flex", flexShrink: 0 }}
        >
          {children}
        </Link>
      </CustomTooltip>
    );
  return (
    <Box
      component="span"
      sx={{ display: "inline-flex", alignItems: "center", gap: 0.5 }}
    >
      {showVersion && version && (
        <Typography component="span" typography="s3" color="text.secondary">
          {link.edited ? `${version} (edited in this experiment)` : version} ·
        </Typography>
      )}
      <CustomTooltip
        show
        title={link.tooltip}
        size="small"
        placement="top"
        arrow
      >
        <Link
          {...common}
          underline="hover"
          typography="s3"
          fontWeight="fontWeightMedium"
        >
          Open in Evals ↗
        </Link>
      </CustomTooltip>
    </Box>
  );
}

OpenInEvalsLink.propTypes = {
  datasetId: PropTypes.string,
  metricKey: PropTypes.string,
  def: PropTypes.object,
  name: PropTypes.string,
  version: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
  variant: PropTypes.oneOf(["text", "chip"]),
  showVersion: PropTypes.bool,
  children: PropTypes.node,
};
