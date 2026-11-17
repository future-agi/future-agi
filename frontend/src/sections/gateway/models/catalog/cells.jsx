import React from "react";
import PropTypes from "prop-types";
import { Box, Link, Tooltip, Typography } from "@mui/material";

import { RouterLink } from "src/routes/components";

import { LogoMark } from "../../components/frame";
import { ACCESS } from "../catalogRows";
import { modelPath } from "../modelLinks";
import { GATEWAY_MONO } from "../../utils/fonts";

const retireText = (retirement) => {
  if (!retirement) return "";
  if (retirement.date) return `Retires ${retirement.date}`;
  return "Deprecated";
};

// A status note under the id (s3, 11 px): set on 14 px lines so a
// three-line cell (name 18 + id 14 + note 14) fits the 52 px row.
const NOTE_SX = { minWidth: 0, lineHeight: "14px" };
const NOTE_ROW_SX = {
  display: "flex",
  alignItems: "center",
  gap: 0.75,
  minWidth: 0,
};

export function ModelCell({ data }) {
  if (!data) return null;
  const retire = retireText(data.retirement);
  const unsold = data.offered && !data.onCredits;
  const noted = Boolean(retire || unsold);
  const path = modelPath(data.id);
  // The name has its own line, so a status note never squeezes it; a name
  // or id the column still cuts says itself in full on hover.
  const nameProps = {
    typography: "s1",
    fontWeight: "fontWeightMedium",
    noWrap: true,
    title: data.name,
    sx: { minWidth: 0 },
  };
  return (
    <Box sx={{ minWidth: 0, lineHeight: 1.3, py: noted ? 0 : 0.75 }}>
      <Box
        sx={{ display: "flex", alignItems: "center", gap: 0.75, minWidth: 0 }}
      >
        {/* A link, so the model page opens from the keyboard or in a new
            tab; a click elsewhere on the row opens it too. */}
        {path ? (
          <Link
            component={RouterLink}
            href={path}
            color="inherit"
            underline="hover"
            {...nameProps}
          >
            {data.name}
          </Link>
        ) : (
          <Typography {...nameProps}>{data.name}</Typography>
        )}
      </Box>
      <Typography
        typography="s2"
        color="text.secondary"
        noWrap
        title={data.id}
        sx={{
          fontFamily: GATEWAY_MONO,
          ...(noted && { lineHeight: "14px" }),
        }}
      >
        {data.id}
      </Typography>
      {noted && (
        <Box sx={NOTE_ROW_SX}>
          {retire && (
            <Typography
              component="span"
              typography="s3"
              color="warning.main"
              noWrap
              title={retire}
              sx={NOTE_SX}
            >
              {retire}
            </Typography>
          )}
          {unsold && (
            <Typography
              component="span"
              typography="s3"
              color="text.secondary"
              noWrap
              title="Not available on credits"
              sx={NOTE_SX}
            >
              Not available on credits
            </Typography>
          )}
        </Box>
      )}
    </Box>
  );
}

ModelCell.propTypes = { data: PropTypes.object };

const ACCESS_LABEL_SX = {
  whiteSpace: "normal",
  overflow: "hidden",
  display: "-webkit-box",
  WebkitBoxOrient: "vertical",
  WebkitLineClamp: 2,
};

export function AccessCell({ data }) {
  if (!data) return null;
  const off =
    (data.access === ACCESS.MANAGED || data.access === ACCESS.BOTH) &&
    !data.managedOn;
  const tip = off
    ? "Future AGI credits are off for your organization."
    : data.accessReason ||
      (data.ownRoutes.length
        ? `Your ${data.ownRoutes.map((r) => r.providerName).join(", ")} key serves it as ${data.ownRoutes[0].sendAs}.`
        : "Billed from your Future AGI credits.");
  return (
    <Tooltip title={tip} arrow placement="top-start">
      <Box
        sx={{
          height: "100%",
          display: "flex",
          alignItems: "center",
          minWidth: 0,
        }}
      >
        {/* Two lines at most, so a narrow column wraps the label instead of
            cutting "· off" (kept together by a no-break space). */}
        <Typography
          typography="s2"
          color={
            data.access === ACCESS.NONE ? "text.secondary" : "text.primary"
          }
          sx={ACCESS_LABEL_SX}
        >
          {data.accessLabel}
          {off ? " ·\u00a0off" : ""}
        </Typography>
      </Box>
    </Tooltip>
  );
}

AccessCell.propTypes = { data: PropTypes.object };

// The provider: its logo (Future AGI's, where it has one) or its initial,
// then its name.
export function VendorCell({ data }) {
  if (!data) return null;
  return (
    <Box
      sx={{
        height: "100%",
        display: "flex",
        alignItems: "center",
        gap: 1,
        minWidth: 0,
      }}
    >
      <LogoMark name={data.vendorName} src={data.vendorLogo} size={16} />
      <Typography typography="s2" noWrap title={data.vendorName}>
        {data.vendorName}
      </Typography>
    </Box>
  );
}

VendorCell.propTypes = { data: PropTypes.object };
