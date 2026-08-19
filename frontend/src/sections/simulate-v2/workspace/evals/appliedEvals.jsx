import PropTypes from "prop-types";
import { useMemo } from "react";
import { Box, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { resolveEval } from "../../_mock/evals";

/**
 * Applied evals — shared between the Evals step and the Overview card.
 *
 * Both surfaces add, list and remove the same evals, so the reading, the
 * add/remove rules and the row markup live here once. Without this the two
 * would drift the moment either changed.
 */
export function useAppliedEvals(envState, patch) {
  const applied = envState.evals;

  const appliedEvals = useMemo(
    () => applied.map(resolveEval).filter(Boolean),
    [applied],
  );

  const appliedIds = useMemo(
    () => new Set(appliedEvals.map((e) => e.id)),
    [appliedEvals],
  );

  const add = (entries) => {
    const fresh = entries.filter((e) => !appliedIds.has(e.id));
    if (fresh.length) patch({ evals: [...applied, ...fresh] });
  };

  const remove = (id) =>
    patch({ evals: applied.filter((e) => (typeof e === "string" ? e : e.id) !== id) });

  /**
   * The picker returns a configured eval — template, judge model and the
   * variable→column mapping. Only what this prototype needs is kept, in the
   * same shape as a catalogue eval so both render through one row.
   */
  const onEvalAdded = (config) => {
    const id = config.templateId || config.id || `eval-${config.name}`;
    const mapping = config.mapping || {};
    add([{
      id,
      name: config.name || config.evalTemplate?.name || "Eval",
      blurb: Object.keys(mapping).length
        ? Object.entries(mapping).map(([k, v]) => `${k} → ${v}`).join(" · ")
        : "Added from the eval library",
      mapping,
      model: config.model,
      custom: true,
    }]);
  };

  return { applied, appliedEvals, appliedIds, add, remove, onEvalAdded };
}

export function EvalRow({ item, action, dense }) {
  return (
    <Stack direction="row" alignItems="center" spacing={2} sx={{ px: 2.5, py: dense ? 1.25 : 1.5 }}>
      <Box
        sx={{
          width: 30, height: 30, borderRadius: 0.875, display: "grid", placeItems: "center", flexShrink: 0,
          color: "text.secondary", bgcolor: "background.neutral",
        }}
      >
        <Iconify icon={item.icon || "solar:shield-check-linear"} width={16} />
      </Box>
      <Box flex={1} minWidth={0}>
        <Stack direction="row" alignItems="center" spacing={0.75}>
          <Typography noWrap sx={{ typography: "s2", fontWeight: 600 }}>{item.name}</Typography>
          {item.category && (
            <Typography sx={{ typography: "s3", color: "text.subtitle", flexShrink: 0 }}>
              · {item.category}
            </Typography>
          )}
        </Stack>
        <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>{item.blurb}</Typography>
      </Box>
      {item.threshold != null && !dense && (
        <Typography
          sx={{
            typography: "s3", color: "text.subtitle", flexShrink: 0,
            display: { xs: "none", md: "block" }, fontVariantNumeric: "tabular-nums",
          }}
        >
          pass ≥ {(item.threshold * 100).toFixed(0)}%
        </Typography>
      )}
      <Box sx={{ flexShrink: 0 }}>{action}</Box>
    </Stack>
  );
}

EvalRow.propTypes = {
  item: PropTypes.object,
  action: PropTypes.node,
  dense: PropTypes.bool,
};
