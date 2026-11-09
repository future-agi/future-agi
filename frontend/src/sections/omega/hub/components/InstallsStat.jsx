import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";

import { countOrDash } from "../lib/format";
import { showsInstalls, showsOpens, useInstallPath } from "../lib/installs";
import { useHandoffAdmission } from "../lib/useHandoffAdmission";

import CountDefinition from "./CountDefinition";

function Sparkline({ series }) {
  const values = (series || []).map((v) => Number(v) || 0);
  const max = Math.max(...values, 0);
  if (!values.length || max === 0) return null;
  const width = 120;
  const height = 28;
  const step = values.length > 1 ? width / (values.length - 1) : width;
  const points = values
    .map(
      (v, i) =>
        `${(i * step).toFixed(1)},${(height - 2 - (v / max) * (height - 4)).toFixed(1)}`,
    )
    .join(" ");
  return (
    <Box
      component="svg"
      viewBox={`0 0 ${width} ${height}`}
      sx={{ width, height, color: "primary.main" }}
      aria-hidden="true"
    >
      <polyline
        points={points}
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinejoin="round"
      />
    </Box>
  );
}

Sparkline.propTypes = { series: PropTypes.array };

// "Installs, last 30 days" and, on its own line, "Opened in Future AGI"
// (SPEC §6.2, §7.2). A count the server has not seen is "—". Each line shows
// only when it can count something (lib/installs.js): installs once the CLI
// has `omega hub`, opens once a "Use this" handoff ships, or when the server
// did count one. With neither the panel is left out: it could only ever read
// "No installs yet".
export default function InstallsStat({ installs30d, opens30d, series }) {
  const installPath = useInstallPath();
  const installsShown = showsInstalls(installPath, installs30d);
  const admission = useHandoffAdmission();
  const opensShown = showsOpens(opens30d, undefined, admission);
  if (!installsShown && !opensShown) return null;
  const none = !installs30d;
  return (
    <Box
      data-testid="hub-installs"
      sx={{
        display: "flex",
        flexDirection: "column",
        gap: 1,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        p: 2,
        bgcolor: "background.paper",
      }}
    >
      {installsShown && (
        <Box>
          <Box sx={{ display: "flex", alignItems: "center", gap: 0.25 }}>
            <Typography typography="s2" color="text.secondary">
              Installs, last 30 days
            </Typography>
            <CountDefinition kind="installs" />
          </Box>
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              gap: 1,
            }}
          >
            <Typography
              typography="m2"
              fontWeight="fontWeightSemiBold"
              data-testid="hub-installs-value"
            >
              {countOrDash(installs30d)}
            </Typography>
            <Sparkline series={series} />
          </Box>
          {none && (
            <Typography typography="s3" color="text.tertiary">
              No installs yet
            </Typography>
          )}
        </Box>
      )}
      {opensShown && (
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
          }}
        >
          <Box sx={{ display: "flex", alignItems: "center", gap: 0.25 }}>
            <Typography typography="s2" color="text.secondary">
              Opened in Future AGI, last 30 days
            </Typography>
            <CountDefinition kind="opens" />
          </Box>
          <Typography
            typography="s1"
            fontWeight="fontWeightMedium"
            data-testid="hub-opens-value"
          >
            {countOrDash(opens30d)}
          </Typography>
        </Box>
      )}
    </Box>
  );
}

InstallsStat.propTypes = {
  installs30d: PropTypes.number,
  opens30d: PropTypes.number,
  series: PropTypes.array,
};
