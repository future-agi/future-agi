import PropTypes from "prop-types";
import { Box, Stack, Tooltip, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { REQUIRED_EVAL_NOTE } from "../../_mock/evals";
import { evalRailOf } from "../../_mock/metering";

/*
  Applied evals — the row markup shared between the Evals step and the
  Overview card. The reading and add/remove rules live in useAppliedEvals.js.
*/
export function EvalRow({ item, action, dense }) {
  /* Mapping chips — the variables the eval needs, each pointing at the
     column of the run that fills them. Preset evals arrive with defaults
     via `resolveEval` and custom evals carry the user's picker mapping,
     so this reads the same shape in both cases. Skipped when the mapping
     is empty (e.g. an eval that scores only on the transcript). */
  const mappingEntries = Object.entries(item.mapping || {});

  return (
    <Stack
      direction="row"
      alignItems="flex-start"
      spacing={2}
      sx={{ px: 2.5, py: dense ? 1.25 : 1.5 }}
    >
      <Box
        sx={{
          width: 30,
          height: 30,
          borderRadius: "6px",
          display: "grid",
          placeItems: "center",
          flexShrink: 0,
          color: "text.secondary",
          bgcolor: "background.neutral",
          mt: 0.125,
        }}
      >
        <Iconify icon={item.icon || "solar:shield-check-linear"} width={16} />
      </Box>
      <Box flex={1} minWidth={0}>
        <Stack direction="row" alignItems="center" spacing={0.75}>
          <Typography noWrap sx={{ typography: "s2", fontWeight: 600 }}>
            {item.name}
          </Typography>
          {item.category && (
            <Typography
              sx={{ typography: "s3", color: "text.tertiary", flexShrink: 0 }}
            >
              · {item.category}
            </Typography>
          )}
          {/* Required — a failing score fails the conversation. */}
          {item.required && (
            <Tooltip arrow title={REQUIRED_EVAL_NOTE}>
              <Box
                sx={{
                  display: "inline-flex",
                  alignItems: "center",
                  flexShrink: 0,
                  height: 18,
                  px: 0.625,
                  borderRadius: "4px",
                  border: "1px solid",
                  borderColor: "text.disabled",
                }}
              >
                <Typography
                  sx={{
                    typography: "s3",
                    fontWeight: 600,
                    color: "text.primary",
                  }}
                >
                  Required
                </Typography>
              </Box>
            </Tooltip>
          )}
        </Stack>
        <Typography noWrap sx={{ typography: "s3", color: "text.tertiary" }}>
          {item.blurb}
        </Typography>
        {mappingEntries.length > 0 && !dense && (
          <Stack
            direction="row"
            spacing={0.5}
            sx={{ mt: 0.75, flexWrap: "wrap", rowGap: 0.5 }}
          >
            {mappingEntries.map(([variable, column]) => (
              <MappingChip key={variable} variable={variable} column={column} />
            ))}
          </Stack>
        )}
      </Box>
      {/* The billing rail it runs on — free, or credits per call — with the
          why on hover (AC-19.6). */}
      {!dense && <EvalRail item={item} />}
      {item.threshold != null && !dense && (
        <Typography
          sx={{
            typography: "s3",
            color: "text.tertiary",
            flexShrink: 0,
            display: { xs: "none", md: "block" },
            fontVariantNumeric: "tabular-nums",
            mt: 0.375,
          }}
        >
          pass ≥ {(item.threshold * 100).toFixed(0)}%
        </Typography>
      )}
      <Box sx={{ flexShrink: 0, mt: 0.125 }}>{action}</Box>
    </Stack>
  );
}

/* Free, or "2 credits / call" — quiet, with the reason on hover. */
export function EvalRail({ item, sx }) {
  const rail = evalRailOf(item);
  return (
    <Tooltip arrow title={rail.why}>
      <Typography
        sx={{
          typography: "s3",
          flexShrink: 0,
          whiteSpace: "nowrap",
          mt: 0.375,
          cursor: "help",
          color: rail.metered ? "text.secondary" : "text.tertiary",
          fontWeight: rail.metered ? 600 : 400,
          fontVariantNumeric: "tabular-nums",
          borderBottom: "1px dotted",
          borderColor: "text.disabled",
          ...sx,
        }}
      >
        {rail.label}
      </Typography>
    </Tooltip>
  );
}
EvalRail.propTypes = { item: PropTypes.object, sx: PropTypes.object };

/* One variable → column chip. Small pill so a row can carry three or four
   without dominating the layout. */
function MappingChip({ variable, column }) {
  return (
    <Box
      sx={{
        display: "inline-flex",
        alignItems: "center",
        gap: 0.5,
        px: 0.75,
        py: 0.125,
        borderRadius: "4px",
        border: "1px solid",
        borderColor: "divider",
        bgcolor: "action.hover",
      }}
    >
      <Typography
        sx={{
          typography: "s3",
          color: "text.secondary",
          fontFamily: "ui-monospace, Menlo, monospace",
        }}
      >
        {variable}
      </Typography>
      <Iconify
        icon="solar:arrow-right-linear"
        width={12}
        sx={{ color: "text.disabled" }}
      />
      <Typography
        sx={{
          typography: "s3",
          color: "text.primary",
          fontFamily: "ui-monospace, Menlo, monospace",
        }}
      >
        {column}
      </Typography>
    </Box>
  );
}
MappingChip.propTypes = {
  variable: PropTypes.string,
  column: PropTypes.string,
};

EvalRow.propTypes = {
  item: PropTypes.object,
  action: PropTypes.node,
  dense: PropTypes.bool,
};
