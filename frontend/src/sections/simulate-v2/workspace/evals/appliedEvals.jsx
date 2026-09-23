import PropTypes from "prop-types";
import { useMemo } from "react";
import { alpha } from "@mui/material/styles";
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

  /* Edit in place — keeps the eval's slot in the list. Bare-id records are
     promoted to objects so the edited mapping / threshold survive. */
  const update = (id, changes) =>
    patch({
      evals: applied.map((e) => {
        const entryId = typeof e === "string" ? e : e.id;
        if (entryId !== id) return e;
        return { ...(typeof e === "string" ? { id: e } : e), ...changes };
      }),
    });

  /**
   * The picker returns a configured eval — template, judge model and the
   * variable→column mapping. Only what this prototype needs is kept, in the
   * same shape as a catalogue eval so both render through one row.
   */
  const onEvalAdded = (config) => {
    const id = config.templateId || config.id || `eval-${config.name}`;
    const mapping = config.mapping || {};
    /* Mapping is rendered as chips on the row now — don't duplicate it in
       the blurb. Blurb stays for the qualitative description. */
    add([{
      id,
      name: config.name || config.evalTemplate?.name || "Eval",
      blurb: config.evalTemplate?.description || "Added from the eval library",
      mapping,
      model: config.model,
      custom: true,
    }]);
  };

  return { applied, appliedEvals, appliedIds, add, remove, update, onEvalAdded };
}

export function EvalRow({ item, action, dense }) {
  /* Mapping chips — the variables the eval needs, each pointing at the
     column of the run that fills them. Preset evals arrive with defaults
     via `resolveEval` and custom evals carry the user's picker mapping,
     so this reads the same shape in both cases. Skipped when the mapping
     is empty (e.g. an eval that scores only on the transcript). */
  const mappingEntries = Object.entries(item.mapping || {});

  return (
    <Stack direction="row" alignItems="flex-start" spacing={2} sx={{ px: 2.5, py: dense ? 1.25 : 1.5 }}>
      <Box
        sx={{
          width: 30, height: 30, borderRadius: 0.875, display: "grid", placeItems: "center", flexShrink: 0,
          color: "text.secondary", bgcolor: "background.neutral",
          mt: 0.125,
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
        {mappingEntries.length > 0 && !dense && (
          <Stack direction="row" spacing={0.5} sx={{ mt: 0.75, flexWrap: "wrap", rowGap: 0.5 }}>
            {mappingEntries.map(([variable, column]) => (
              <MappingChip key={variable} variable={variable} column={column} />
            ))}
          </Stack>
        )}
      </Box>
      {item.threshold != null && !dense && (
        <Typography
          sx={{
            typography: "s3", color: "text.subtitle", flexShrink: 0,
            display: { xs: "none", md: "block" }, fontVariantNumeric: "tabular-nums",
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

/* One variable → column chip. Small pill so a row can carry three or four
   without dominating the layout. */
function MappingChip({ variable, column }) {
  return (
    <Box
      sx={{
        display: "inline-flex", alignItems: "center", gap: 0.5,
        px: 0.75, py: 0.125, borderRadius: 0.75,
        border: "1px solid",
        borderColor: "divider",
        bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.04 : 0.025),
      }}
    >
      <Typography sx={{ typography: "s3", color: "text.secondary", fontFamily: "ui-monospace, Menlo, monospace" }}>
        {variable}
      </Typography>
      <Iconify icon="solar:arrow-right-linear" width={10} sx={{ color: "text.disabled" }} />
      <Typography sx={{ typography: "s3", color: "text.primary", fontFamily: "ui-monospace, Menlo, monospace" }}>
        {column}
      </Typography>
    </Box>
  );
}
MappingChip.propTypes = { variable: PropTypes.string, column: PropTypes.string };

EvalRow.propTypes = {
  item: PropTypes.object,
  action: PropTypes.node,
  dense: PropTypes.bool,
};
