import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Chip from "@mui/material/Chip";
import Typography from "@mui/material/Typography";

import { formatDate, shortDigest } from "../lib/format";
import ToneChip from "./ToneChip";

// The Versions tab (SPEC §7.2): version, published, reviewed, notes, short
// digest, rules version, and a yanked badge with its reason.
export default function VersionsTable({ versions, current }) {
  if (!versions?.length) {
    return (
      <Typography typography="s2" color="text.secondary">
        No versions to show.
      </Typography>
    );
  }
  return (
    <Box
      component="ol"
      data-testid="hub-versions"
      sx={{
        m: 0,
        p: 0,
        listStyle: "none",
        display: "flex",
        flexDirection: "column",
      }}
    >
      {versions.map((row) => (
        <Box
          component="li"
          key={row.version}
          data-testid="hub-version-row"
          sx={{
            py: 1.5,
            borderBottom: "1px solid",
            borderColor: "divider",
            display: "flex",
            flexDirection: "column",
            gap: 0.5,
          }}
        >
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              gap: 1,
              flexWrap: "wrap",
            }}
          >
            <Typography
              typography="s1"
              fontWeight={600}
              sx={{ fontFamily: "monospace" }}
            >
              {row.version}
            </Typography>
            {row.version === current && (
              <Chip size="small" label="Showing" variant="outlined" />
            )}
            {row.status === "yanked" && (
              <ToneChip
                tone="attention"
                label={`Yanked: ${row.yankedReason || "no reason given"}`}
              />
            )}
            <Typography
              typography="s2"
              color="text.secondary"
              sx={{ ml: "auto" }}
            >
              {`Published and reviewed ${formatDate(row.publishedAt)}`}
            </Typography>
          </Box>
          {row.notes && (
            <Typography typography="s2" color="text.primary">
              {row.notes}
            </Typography>
          )}
          <Typography
            typography="s3"
            color="text.tertiary"
            sx={{ fontFamily: "monospace" }}
          >
            {`${shortDigest(row.digest, 12)}${row.rulesVersion ? ` · rules v${row.rulesVersion}` : ""}`}
          </Typography>
        </Box>
      ))}
    </Box>
  );
}

VersionsTable.propTypes = {
  versions: PropTypes.array,
  current: PropTypes.string,
};
