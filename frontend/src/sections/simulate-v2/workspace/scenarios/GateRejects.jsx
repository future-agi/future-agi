import PropTypes from "prop-types";
import { useState } from "react";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography, Collapse } from "@mui/material";
import Iconify from "src/components/iconify";
import { generationTally, rejectsFor, GATES } from "../../_mock/gates";

/**
 * What generation threw away.
 *
 * Sits under the scenario list because it is a property of that list: the rows
 * above are what survived three gates, and until the rejects were visible the
 * gates were a badge on the survivors rather than a filter with a yield.
 *
 * Collapsed by default. The count is the argument — "41 drafted, 9 rejected"
 * says the generator has judgement — and the reasons are there for whoever
 * doubts it.
 */
export default function GateRejects({ env, kept, sx }) {
  const [open, setOpen] = useState(false);
  const rejects = rejectsFor(env);
  const tally = generationTally(env, kept);

  if (!rejects.length) return null;

  return (
    <Box
      sx={{
        border: "1px solid", borderColor: "divider", borderRadius: 1.5,
        bgcolor: "background.paper", overflow: "hidden", ...sx,
      }}
    >
      <Stack
        direction="row" alignItems="center" spacing={1.5}
        onClick={() => setOpen((o) => !o)}
        sx={{ px: 2.5, py: 1.75, cursor: "pointer", "&:hover": { bgcolor: "action.hover" } }}
      >
        <Iconify icon="solar:filter-linear" width={15} sx={{ color: "text.subtitle", flexShrink: 0 }} />
        <Box flex={1} minWidth={0}>
          <Typography sx={{ typography: "s2", fontWeight: 700 }}>
            {tally.drafted} drafted · {tally.kept} kept ·{" "}
            <Box component="span" sx={{ color: "#DC2626" }}>{tally.rejected} rejected</Box>
          </Typography>
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
            The three gates are code, not a model. A draft that fails one is discarded, not kept with a warning.
          </Typography>
        </Box>
        <Stack
          direction="row" spacing={0.75} flexShrink={0}
          sx={{ display: { xs: "none", md: "flex" } }}
        >
          {tally.byGate.map((g) => (
            <Typography
              key={g.id}
              sx={{
                px: 0.875, py: 0.375, borderRadius: 0.75,
                typography: "s3", fontWeight: 700, color: "text.secondary",
                bgcolor: "background.neutral",
              }}
            >
              {g.count} {g.failed}
            </Typography>
          ))}
        </Stack>
        <Iconify
          icon={open ? "solar:alt-arrow-up-linear" : "solar:alt-arrow-down-linear"}
          width={14}
          sx={{ color: "text.subtitle", flexShrink: 0 }}
        />
      </Stack>

      <Collapse in={open} unmountOnExit>
        <Stack
          divider={<Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />}
          sx={{ borderTop: "1px solid", borderColor: "divider" }}
        >
          {rejects.map((r) => (
            <Stack key={r.id} direction="row" spacing={1.5} alignItems="flex-start" sx={{ px: 2.5, py: 1.5 }}>
              <GateBadge gate={r.gate} />
              <Box flex={1} minWidth={0}>
                <Typography sx={{ typography: "s2", fontWeight: 600, color: "text.secondary" }}>
                  {r.title}
                </Typography>
                <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{r.reason}</Typography>
              </Box>
            </Stack>
          ))}
        </Stack>
      </Collapse>
    </Box>
  );
}

GateRejects.propTypes = {
  env: PropTypes.object.isRequired,
  kept: PropTypes.number,
  sx: PropTypes.object,
};

/** Which gate said no. Red because the row is a discard, not a caveat. */
function GateBadge({ gate }) {
  const meta = GATES.find((g) => g.id === gate);
  return (
    <Typography
      sx={{
        width: 92, flexShrink: 0, textAlign: "center", mt: "1px",
        px: 0.75, py: 0.25, borderRadius: 0.5,
        typography: "s3", fontWeight: 700, color: "#DC2626",
        bgcolor: (t) => alpha("#DC2626", t.palette.mode === "dark" ? 0.16 : 0.08),
      }}
    >
      {(meta?.failed || gate).toUpperCase()}
    </Typography>
  );
}
GateBadge.propTypes = { gate: PropTypes.string };
