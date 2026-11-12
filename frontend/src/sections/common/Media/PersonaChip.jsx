// A persona value: the initial in a neutral circle + `Ana · calm`, traits
// in the tooltip. Never run colours (a persona is not a run).
import React from "react";
import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import CustomTooltip from "src/components/tooltip";
import { personaOf } from "./mediaModel";

export default function PersonaChip({ value, persona: given, size = "cell" }) {
  const p = given ?? personaOf(value);
  if (!p) return null;
  const tip = (
    <Stack gap={0.25} sx={{ py: 0.25 }}>
      <Typography typography="s2" fontWeight="fontWeightSemiBold">
        {p.label}
      </Typography>
      {p.description ? (
        <Typography typography="s3">{p.description}</Typography>
      ) : null}
      {p.traits.map(([k, v]) => (
        <Typography key={k} typography="s3">
          {`${k.replace(/_/g, " ")}: ${String(v)}`}
        </Typography>
      ))}
    </Stack>
  );
  const d = size === "cell" ? 20 : 28;
  return (
    <CustomTooltip show size="small" arrow title={tip}>
      <Stack
        direction="row"
        alignItems="center"
        gap={0.75}
        data-persona-chip={p.name}
        sx={{ minWidth: 0, maxWidth: "100%" }}
      >
        <Box
          aria-hidden
          sx={{
            width: d,
            height: d,
            borderRadius: "50%",
            flexShrink: 0,
            display: "grid",
            placeItems: "center",
            bgcolor: "background.neutral",
            border: "1px solid",
            borderColor: "divider",
            typography: size === "cell" ? "s3" : "s2",
            fontWeight: 600,
            color: "text.secondary",
          }}
        >
          {p.initial}
        </Box>
        <Typography
          typography={size === "cell" ? "s2" : "s1"}
          noWrap
          sx={{ minWidth: 0 }}
        >
          {p.label}
        </Typography>
      </Stack>
    </CustomTooltip>
  );
}

PersonaChip.propTypes = {
  value: PropTypes.any,
  persona: PropTypes.object,
  size: PropTypes.oneOf(["cell", "row", "card"]),
};
