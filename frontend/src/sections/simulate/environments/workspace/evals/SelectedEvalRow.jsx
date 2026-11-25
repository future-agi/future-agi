import PropTypes from "prop-types";
import { Box, Stack, Tooltip, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { EVALS_COPY } from "./evals.constants";
import { SELECTED_EVAL_SHAPE, inputRowsOf, sourceLabel } from "./evalEntry";

const MONO = "ui-monospace, Menlo, monospace";

function RequiredBadge() {
  return (
    <Tooltip arrow title={EVALS_COPY.requiredTip}>
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
          sx={{ typography: "s3", fontWeight: 600, color: "text.primary" }}
        >
          {EVALS_COPY.required}
        </Typography>
      </Box>
    </Tooltip>
  );
}

function MappingChip({ variable, label }) {
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
        minWidth: 0,
      }}
    >
      <Typography
        sx={{ typography: "s3", color: "text.secondary", fontFamily: MONO }}
      >
        {variable}
      </Typography>
      <Iconify
        icon="solar:arrow-right-linear"
        width={12}
        sx={{ color: "text.disabled", flexShrink: 0 }}
      />
      <Typography noWrap sx={{ typography: "s3", color: "text.primary" }}>
        {label}
      </Typography>
    </Box>
  );
}

MappingChip.propTypes = {
  variable: PropTypes.string,
  label: PropTypes.string,
};

export function EvalCost({ item }) {
  const credits = item?.credits_per_run;
  if (!Number.isFinite(credits)) return null;
  const metered = credits > 0 || Boolean(item.charges_judge_tokens);
  return (
    <Tooltip arrow title={EVALS_COPY.costTip(item)}>
      <Typography
        sx={{
          typography: "s3",
          flexShrink: 0,
          whiteSpace: "nowrap",
          mt: 0.375,
          cursor: "help",
          color: metered ? "text.secondary" : "text.tertiary",
          fontWeight: metered ? 600 : 400,
          fontVariantNumeric: "tabular-nums",
          borderBottom: "1px dotted",
          borderColor: "text.disabled",
        }}
      >
        {EVALS_COPY.costLabel(item)}
      </Typography>
    </Tooltip>
  );
}

EvalCost.propTypes = { item: SELECTED_EVAL_SHAPE };

export default function SelectedEvalRow({ item, threshold, action }) {
  const source = sourceLabel(item);
  const inputs = inputRowsOf(item);
  return (
    <Stack
      direction="row"
      alignItems="flex-start"
      spacing={2}
      sx={{ px: 2.5, py: 1.5 }}
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
        <Iconify icon="solar:shield-check-linear" width={16} />
      </Box>
      <Box flex={1} minWidth={0}>
        <Stack direction="row" alignItems="center" spacing={0.75} minWidth={0}>
          <Typography noWrap sx={{ typography: "s2", fontWeight: 600 }}>
            {item.name}
          </Typography>
          {source && (
            <Typography
              sx={{ typography: "s3", color: "text.tertiary", flexShrink: 0 }}
            >
              · {source}
            </Typography>
          )}
          {item.required && <RequiredBadge />}
        </Stack>
        {item.description && (
          <Tooltip arrow title={item.description} enterDelay={400}>
            <Typography
              noWrap
              sx={{ typography: "s3", color: "text.tertiary" }}
            >
              {item.description}
            </Typography>
          </Tooltip>
        )}
        {inputs.length > 0 && (
          <Stack
            direction="row"
            spacing={0.5}
            sx={{ mt: 0.75, flexWrap: "wrap", rowGap: 0.5 }}
          >
            {inputs.map((row) => (
              <MappingChip key={row.key} variable={row.key} label={row.label} />
            ))}
          </Stack>
        )}
      </Box>
      <EvalCost item={item} />
      {threshold}
      <Box sx={{ flexShrink: 0, mt: 0.125 }}>{action}</Box>
    </Stack>
  );
}

SelectedEvalRow.propTypes = {
  item: SELECTED_EVAL_SHAPE,
  threshold: PropTypes.node,
  action: PropTypes.node,
};
