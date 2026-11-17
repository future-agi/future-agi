import React, { useMemo } from "react";
import PropTypes from "prop-types";
import { Box, MenuItem, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { isSurfaceShown } from "src/launch/launchSwitch";
import { useDecisionModels } from "./useDecisionModels";

/**
 * The evaluator picker's "Decision models" section (W1, PR #3289 parity):
 * one row per preset decision model, with its format and per-token price.
 * A model whose provider has no key in the workspace stays listed, says
 * where to add the key, and opens the keys drawer on that provider.
 */
export default function DecisionModelsSection({
  model,
  search = "",
  onPick,
  onAddKey,
}) {
  const { rows, isLoading, isError, notSupported, refetch } =
    useDecisionModels();
  const shown = useMemo(() => {
    const s = search.trim().toLowerCase();
    if (!s) return rows;
    return rows.filter((r) =>
      `${r.id} ${r.providerName} ${r.formatLabel} decision`
        .toLowerCase()
        .includes(s),
    );
  }, [rows, search]);
  if (!isSurfaceShown("models.decision")) return null;
  if (!isLoading && !isError && !shown.length) return null;
  return (
    <Box data-testid="decision-models-section">
      <Typography
        variant="caption"
        sx={{
          px: 1.5,
          pt: 1.5,
          pb: 0.25,
          display: "block",
          fontSize: "10px",
          fontWeight: 700,
          textTransform: "uppercase",
          letterSpacing: "0.5px",
          color: "primary.main",
        }}
      >
        Decision models · your key
      </Typography>
      <Typography
        variant="caption"
        color="text.secondary"
        sx={{ px: 1.5, pb: 0.5, display: "block", fontSize: "11px" }}
      >
        Answer a typed question (yes/no, a choice, a score). Billed to your
        provider key; no Future AGI credits.
      </Typography>
      {isLoading && (
        <Typography
          variant="caption"
          color="text.secondary"
          sx={{ px: 1.5, py: 0.5, display: "block" }}
        >
          Loading decision models…
        </Typography>
      )}
      {/* An older gateway cannot list them: one plain line, no retry. */}
      {isError && !isLoading && notSupported ? (
        <Typography
          data-testid="decision-models-not-supported"
          variant="caption"
          color="text.secondary"
          sx={{ px: 1.5, py: 0.75, display: "block" }}
        >
          This gateway version doesn&apos;t list decision models. Update the
          gateway to use them.
        </Typography>
      ) : null}
      {/* W7 B4: a failed listing is not a missing key. */}
      {isError && !isLoading && !notSupported ? (
        <MenuItem
          data-testid="decision-models-error"
          onClick={() => refetch?.()}
          sx={{ mx: 0.5, borderRadius: "6px", py: 0.75 }}
        >
          <Typography variant="caption" color="error.main">
            Couldn&apos;t list decision models · Try again
          </Typography>
        </MenuItem>
      ) : null}
      {!isLoading &&
        !isError &&
        shown.map((r) => {
          const selected = r.id === model;
          return (
            <MenuItem
              key={r.id}
              selected={selected}
              data-testid={`decision-model-${r.id}`}
              onClick={() => (r.ready ? onPick(r.id) : onAddKey(r))}
              sx={{
                mx: 0.5,
                borderRadius: "6px",
                py: 0.75,
                gap: 1,
                width: "auto",
                alignItems: "flex-start",
              }}
            >
              <Iconify
                icon={r.ready ? "mdi:scale-balance" : "mdi:key-alert-outline"}
                width={18}
                sx={{
                  mt: 0.25,
                  color: selected
                    ? "primary.main"
                    : r.ready
                      ? "text.secondary"
                      : "warning.main",
                  flexShrink: 0,
                }}
              />
              <Box sx={{ flex: 1, minWidth: 0 }}>
                <Stack direction="row" alignItems="center" gap={0.75}>
                  <Typography
                    variant="body2"
                    sx={{ fontSize: "13px", fontWeight: 500 }}
                    noWrap
                  >
                    {r.id}
                  </Typography>
                  <Typography
                    component="span"
                    sx={{
                      fontSize: "10px",
                      px: 0.5,
                      borderRadius: 0.5,
                      bgcolor: "action.hover",
                      color: "text.secondary",
                      flexShrink: 0,
                    }}
                  >
                    {r.formatLabel}
                  </Typography>
                </Stack>
                <Typography
                  variant="caption"
                  color="text.secondary"
                  sx={{ fontSize: "11px", display: "block" }}
                >
                  {r.priceText}
                </Typography>
                <Typography
                  variant="caption"
                  sx={{
                    fontSize: "11px",
                    display: "block",
                    color: r.ready ? "text.secondary" : "warning.dark",
                    textDecoration: r.ready ? "none" : "underline",
                  }}
                >
                  {r.keyHint}
                </Typography>
              </Box>
              {selected && (
                <Iconify
                  icon="mdi:check"
                  width={16}
                  sx={{ color: "primary.main", flexShrink: 0, mt: 0.25 }}
                />
              )}
            </MenuItem>
          );
        })}
    </Box>
  );
}

DecisionModelsSection.propTypes = {
  model: PropTypes.string,
  search: PropTypes.string,
  onPick: PropTypes.func.isRequired,
  onAddKey: PropTypes.func.isRequired,
};
