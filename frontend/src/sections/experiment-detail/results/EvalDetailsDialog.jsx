import React from "react";
import PropTypes from "prop-types";
import {
  Box,
  Dialog,
  DialogContent,
  DialogTitle,
  IconButton,
  Stack,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import DirectionIcon from "src/sections/common/Metrics/DirectionIcon";
import OpenInEvalsLink from "src/sections/common/EvalHandoffs/OpenInEvalsLink";
import RunChip from "src/sections/common/Runs/RunChip";

const Line = ({ label, children }) => (
  <Stack direction="row" gap={1.5} alignItems="baseline">
    <Typography
      typography="s2"
      color="text.secondary"
      sx={{ width: 110, flexShrink: 0 }}
    >
      {label}
    </Typography>
    <Box sx={{ typography: "s1", minWidth: 0 }}>{children}</Box>
  </Stack>
);
Line.propTypes = { label: PropTypes.string, children: PropTypes.node };

/**
 * `Eval details` from a results group ⋮ menu (PRODUCT-SPEC §3.10.7): what
 * the eval measures, its direction and pass mark, and the version each run
 * in view was scored with.
 */
const EvalDetailsDialog = ({ def, runsById, colors, onClose }) => {
  const open = !!def;
  const versions = Object.entries(def?.versions ?? {});
  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="xs">
      <DialogTitle sx={{ pr: 6 }}>
        <Stack direction="row" alignItems="center" gap={1}>
          <Iconify
            icon="material-symbols:check-circle-outline"
            sx={{ color: "info.success" }}
          />
          <Typography typography="m3" fontWeight="fontWeightSemiBold">
            {def?.name}
          </Typography>
          <DirectionIcon higherIsBetter={def?.higher_is_better ?? null} />
        </Stack>
        <IconButton
          aria-label="Close"
          onClick={onClose}
          sx={{ position: "absolute", top: 12, right: 12 }}
        >
          <Iconify icon="mingcute:close-line" />
        </IconButton>
      </DialogTitle>
      <DialogContent>
        <Stack spacing={1.25} sx={{ pb: 1 }}>
          {def?.description && (
            <Typography typography="s1" color="text.secondary">
              {def.description}
            </Typography>
          )}
          <Line label="Output">
            {def?.output_type === "pass_fail" ? "Pass / Fail" : "Score 0–1"}
          </Line>
          <Line label="Direction">
            {def?.higher_is_better === false
              ? "Lower is better"
              : "Higher is better"}
          </Line>
          <Line label="Pass mark">
            {def?.output_type === "pass_fail"
              ? "Pass"
              : def?.pass_threshold != null
                ? `≥ ${def.pass_threshold}`
                : "None set"}
          </Line>
          <Line label="Latest version">{def?.version ?? "—"}</Line>
          {/* L7c g7 (colmenu:eval-details): the judge each run in view was
              scored with, and the integration to the eval's page. */}
          {Object.keys(def?.scored_with ?? {}).length > 0 && (
            <Line label="Judge">
              {[
                ...new Set(
                  Object.values(def.scored_with)
                    .map((s) => s?.judge_model)
                    .filter(Boolean),
                ),
              ].join(", ") || "—"}
            </Line>
          )}
          <OpenInEvalsLink
            metricKey={def?.key}
            def={def}
            name={def?.name}
            version={def?.version}
          />
          {versions.length > 0 && (
            <Box>
              <Typography
                typography="s2"
                color="text.secondary"
                sx={{ mb: 0.75 }}
              >
                {def?.versions_differ
                  ? "Scored with different versions (deltas mix versions)"
                  : "Version per run"}
              </Typography>
              <Stack spacing={0.75}>
                {versions.map(([runId, v]) => (
                  <Stack
                    key={runId}
                    direction="row"
                    alignItems="center"
                    gap={1}
                  >
                    {runsById[runId] && (
                      <RunChip
                        run={runsById[runId]}
                        color={colors[runId]}
                        showStatus={false}
                      />
                    )}
                    <Typography
                      typography="s2"
                      fontWeight={500}
                      color={
                        def?.versions_differ && v !== def?.version
                          ? "warning.dark"
                          : "text.primary"
                      }
                    >
                      {v ?? "—"}
                    </Typography>
                  </Stack>
                ))}
              </Stack>
            </Box>
          )}
        </Stack>
      </DialogContent>
    </Dialog>
  );
};

EvalDetailsDialog.propTypes = {
  def: PropTypes.object,
  runsById: PropTypes.object.isRequired,
  colors: PropTypes.object.isRequired,
  onClose: PropTypes.func.isRequired,
};

export default EvalDetailsDialog;
