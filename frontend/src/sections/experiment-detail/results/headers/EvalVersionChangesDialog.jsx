import React from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Skeleton,
  Stack,
  Typography,
} from "@mui/material";
import { useQueries } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import TextDiff from "src/sections/common/ValueViewer/TextDiff";
import { judgeCallUsd } from "src/sections/develop-detail/Experiment/estimateInputs";
import { rescoreCostLine } from "src/sections/common/Runs/rescoreCopyModel";
import { changeLines, flipLine } from "./evalVersionText";

const ROW_CHIPS = 24;

/**
 * W1-3 (EV-03): See changes. What the template's newest version changes
 * (rubric / prompt as a diff, pass bar, judge, decision question) and, per
 * run in view on an older version, the flip preview the server computed
 * with the newer definition (GET …/versions/{v}/preview/?run_id=, the
 * re-score's own dry pass): the rows that would flip, each opening, and
 * what it costs (the one estimator). "Use v4 on #2A" re-scores that run
 * as a child through the one re-score door; "Use v4 in new runs" pins the
 * experiment's card to it (`pinTargets`: one per experiment in view).
 */
const EvalVersionChangesDialog = ({
  open,
  onClose,
  def,
  runs,
  onUse,
  onOpenRow,
  pinTargets = [],
  onPin,
}) => {
  const latest = def?.latest_version;
  const previews = useQueries({
    queries: (open ? runs ?? [] : []).map((r) => ({
      queryKey: ["eval-version-preview", def?.template_id, latest, r.runId],
      queryFn: () =>
        axios
          .get(
            endpoints.develop.datasetRuns.evalVersionPreview(
              def.template_id,
              latest,
            ),
            { params: { run_id: r.runId } },
          )
          .then((res) => res.data?.result),
      enabled: Boolean(open && def?.template_id && latest),
    })),
  });
  const first = previews.find((q) => q.data)?.data;
  const rubric = first?.changes?.find((c) => c.field === "instructions");
  const lines = changeLines(first?.changes ?? []);
  return (
    <Dialog open={open} onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle sx={{ typography: "m3", pb: 1 }}>
        {`${def?.name ?? "Eval"} ${latest}: what changes`}
      </DialogTitle>
      <DialogContent dividers data-testid="eval-version-changes">
        {!first ? (
          <Skeleton variant="rounded" height={64} />
        ) : (
          <Stack spacing={0.5} sx={{ mb: 2 }} data-testid="eval-version-diff">
            <Typography typography="s2" color="text.secondary">
              {`From ${first.run_version} (what ${first.run_label} was scored with) to ${latest}`}
            </Typography>
            {!lines.length && !rubric ? (
              <Typography typography="s1">
                The definition did not change (rubric, pass bar, judge and
                decision question are the same).
              </Typography>
            ) : null}
            {lines.map((l) => (
              <Typography key={l} typography="s1">
                {l}
              </Typography>
            ))}
            {rubric ? (
              <Box>
                <Typography typography="s2" fontWeight="fontWeightMedium">
                  Rubric / prompt
                </Typography>
                <TextDiff from={rubric.from ?? ""} to={rubric.to ?? ""} />
              </Box>
            ) : null}
          </Stack>
        )}
        <Stack spacing={1.5}>
          {(runs ?? []).map((r, i) => {
            const q = previews[i];
            const p = q?.data;
            return (
              <Box
                key={r.runId}
                data-testid="eval-version-preview"
                sx={{
                  border: "1px solid",
                  borderColor: "divider",
                  borderRadius: 0.5,
                  p: 1.25,
                }}
              >
                <Typography typography="s1" fontWeight="fontWeightSemiBold">
                  {`${r.label ?? "Run"} · scored with ${r.version}`}
                </Typography>
                {q?.isLoading ? (
                  <Skeleton variant="text" width={320} />
                ) : q?.isError || !p ? (
                  <Typography typography="s2" color="error.main">
                    Could not compute what would change
                  </Typography>
                ) : (
                  <>
                    <Typography
                      typography="s1"
                      data-testid="eval-version-flips"
                    >
                      {flipLine(p)}
                    </Typography>
                    <Typography
                      typography="s2"
                      color="text.secondary"
                      data-testid="eval-version-cost"
                    >
                      {rescoreCostLine(p, judgeCallUsd).text}
                    </Typography>
                    {p.would_flip?.length ? (
                      <Stack
                        direction="row"
                        flexWrap="wrap"
                        gap={0.5}
                        sx={{ mt: 0.75 }}
                      >
                        {p.would_flip.slice(0, ROW_CHIPS).map((id, k) => (
                          <Chip
                            key={id}
                            size="small"
                            variant="outlined"
                            label={`Row ${p.would_flip_rows?.[k] ?? k + 1}`}
                            onClick={() => onOpenRow?.(r.runId, id)}
                          />
                        ))}
                        {p.would_flip.length > ROW_CHIPS ? (
                          <Typography typography="s3" color="text.secondary">
                            {`+${p.would_flip.length - ROW_CHIPS} more`}
                          </Typography>
                        ) : null}
                      </Stack>
                    ) : null}
                    <Box sx={{ mt: 1 }}>
                      <Button
                        size="small"
                        variant="outlined"
                        data-testid="eval-version-use"
                        onClick={() => onUse?.(r, latest)}
                      >
                        {`Use ${latest} on ${r.label ?? "this run"}`}
                      </Button>
                    </Box>
                  </>
                )}
              </Box>
            );
          })}
        </Stack>
      </DialogContent>
      <DialogActions>
        {onPin
          ? pinTargets.map((t) => (
              <Button
                key={t.experimentId}
                variant="outlined"
                data-testid="eval-version-pin"
                onClick={() => onPin(t)}
              >
                {pinTargets.length > 1 && t.label
                  ? `Use ${latest} in new runs of ${t.label}`
                  : `Use ${latest} in new runs`}
              </Button>
            ))
          : null}
        <Button onClick={onClose}>Close</Button>
      </DialogActions>
    </Dialog>
  );
};

EvalVersionChangesDialog.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func.isRequired,
  def: PropTypes.object,
  runs: PropTypes.array,
  onUse: PropTypes.func,
  onOpenRow: PropTypes.func,
  pinTargets: PropTypes.array,
  onPin: PropTypes.func,
};

export default EvalVersionChangesDialog;
