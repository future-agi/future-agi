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

export function ModelCell({ data }) {
  if (!data) return null;
  const retire = retireText(data.retirement);
  const path = modelPath(data.id);
  const nameProps = {
    typography: "s1",
    fontWeight: "fontWeightMedium",
    noWrap: true,
    sx: { minWidth: 0 },
  };
  return (
    <Box sx={{ minWidth: 0, lineHeight: 1.3, py: 0.75 }}>
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
        {retire && (
          <Typography
            component="span"
            typography="s3"
            color="warning.main"
            noWrap
            sx={{ flexShrink: 0 }}
          >
            {retire}
          </Typography>
        )}
        {data.offered && !data.onCredits && (
          <Typography
            component="span"
            typography="s3"
            color="text.secondary"
            noWrap
            sx={{ flexShrink: 0 }}
          >
            Not available on credits
          </Typography>
        )}
      </Box>
      <Typography
        typography="s2"
        color="text.secondary"
        noWrap
        sx={{ fontFamily: GATEWAY_MONO }}
      >
        {data.id}
      </Typography>
    </Box>
  );
}

ModelCell.propTypes = { data: PropTypes.object };

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
        <Typography
          typography="s2"
          noWrap
          color={
            data.access === ACCESS.NONE ? "text.secondary" : "text.primary"
          }
        >
          {data.accessLabel}
          {off ? " · off" : ""}
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
      <Typography typography="s2" noWrap>
        {data.vendorName}
      </Typography>
    </Box>
  );
}

VendorCell.propTypes = { data: PropTypes.object };
