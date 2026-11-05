import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import {
  Box,
  Button,
  ListSubheader,
  MenuItem,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  TextField,
  Typography,
} from "@mui/material";
import { ROW_HEIGHT, TABLE_HEADER_HEIGHT } from "src/theme/sizes";
import {
  EVIDENCE,
  evaluationsOf,
  evaluatorLabelOf,
  requiredFieldsOf,
} from "../../_mock/evalEvidence";
import { aboutCredits, estimateOf } from "../../_mock/trialQuestions";
import ScopePicker, { ScopeReview } from "./ScopePicker";
import { useEvalScope } from "./useEvalScope";

/*
  Use an evaluation — pick one of the environment's evaluations (or one from
  the platform), say which part of each trial's evidence every field it needs
  reads, and review the exact trials. Nothing runs until Evaluate; the
  result is saved as a new one and the run's own results stay as they are.
*/
const FieldLabel = ({ children }) => (
  <Typography
    sx={{ typography: "s1", fontWeight: 500, color: "text.primary", mb: 0.75 }}
  >
    {children}
  </Typography>
);
FieldLabel.propTypes = { children: PropTypes.node };

const cellSx = { typography: "s1", color: "text.secondary", py: 0, px: 1.5 };
const headSx = {
  typography: "s2",
  fontWeight: 500,
  color: "text.tertiary",
  bgcolor: "transparent",
  height: TABLE_HEADER_HEIGHT.default,
  py: 0,
  px: 1.5,
};
const groupSx = {
  typography: "s3",
  color: "text.tertiary",
  lineHeight: "28px",
};

export default function UseEvaluationComposer({
  env,
  envState,
  ctx,
  filter,
  selectedIds,
  runLabel,
  initial,
  onCancel,
  onRun,
}) {
  const { applied, platform } = useMemo(
    () => evaluationsOf(env, envState),
    [env, envState],
  );
  const all = useMemo(() => [...applied, ...platform], [applied, platform]);
  const [evalId, setEvalId] = useState(
    () =>
      all.find((e) => e.id === initial?.evaluator?.id)?.id || all[0]?.id || "",
  );
  const evaluation = all.find((e) => e.id === evalId) || null;
  const fieldsOfEval = useMemo(
    () => (evaluation ? requiredFieldsOf(evaluation) : []),
    [evaluation],
  );
  /* What each field reads — the evidence it defaults to, until changed. */
  const [picked, setPicked] = useState({});
  const mappingOf = (f) => picked[`${evalId}:${f.name}`] || f.evidence;
  const mapping = useMemo(
    () =>
      Object.fromEntries(
        fieldsOfEval.map((f) => [
          f.name,
          picked[`${evalId}:${f.name}`] || f.evidence || "env",
        ]),
      ),
    [fieldsOfEval, picked, evalId],
  );
  const fields = useMemo(
    () => [...new Set(Object.values(mapping).filter((v) => v !== "env"))],
    [mapping],
  );
  const state = useEvalScope({ ctx, filter, selectedIds, fields, initial });
  const { scope, scopeLabel, trials, readable } = state;

  if (!all.length)
    return (
      <Typography sx={{ typography: "s1", color: "text.secondary" }}>
        No evaluations apply to this environment yet.
      </Typography>
    );

  const row = (e, group) => (
    <MenuItem key={`${group}:${e.id}`} value={e.id}>
      <Box sx={{ minWidth: 0 }}>
        <Typography sx={{ typography: "s1", color: "text.primary" }}>
          {evaluatorLabelOf(e)}
        </Typography>
        <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
          {[e.type, `pass at ${Math.round(e.threshold * 100)}`]
            .filter(Boolean)
            .join(" · ")}
        </Typography>
      </Box>
    </MenuItem>
  );

  return (
    <Box sx={{ maxWidth: 760 }}>
      <Typography sx={{ typography: "s1", color: "text.secondary" }}>
        Scores saved trials. Your agent isn&apos;t run again.
      </Typography>

      <Stack spacing={2.5} sx={{ mt: 3 }}>
        <Box>
          <FieldLabel>Evaluation</FieldLabel>
          <TextField
            select
            size="small"
            value={evalId}
            onChange={(e) => setEvalId(e.target.value)}
            SelectProps={{
              renderValue: (v) => {
                const e = all.find((x) => x.id === v);
                return e ? evaluatorLabelOf(e) : "";
              },
            }}
            sx={{ minWidth: 320, maxWidth: "100%" }}
          >
            {applied.length > 0 && (
              <ListSubheader sx={groupSx}>
                Applied to this environment
              </ListSubheader>
            )}
            {applied.map((e) => row(e, "applied"))}
            {platform.length > 0 && (
              <ListSubheader sx={groupSx}>Platform evaluations</ListSubheader>
            )}
            {platform.map((e) => row(e, "platform"))}
          </TextField>
          {evaluation?.about && (
            <Typography
              sx={{ typography: "s3", color: "text.tertiary", mt: 0.75 }}
            >
              {evaluation.about}
            </Typography>
          )}
        </Box>

        <Box>
          <FieldLabel>Fields it needs</FieldLabel>
          <Table size="small" sx={{ maxWidth: 560 }}>
            <TableHead>
              <TableRow>
                <TableCell sx={{ ...headSx, width: 180 }}>Field</TableCell>
                <TableCell sx={headSx}>Reads</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {fieldsOfEval.map((f) => (
                <TableRow key={f.name} sx={{ height: ROW_HEIGHT.default }}>
                  <TableCell
                    sx={{ ...cellSx, color: "text.primary", fontWeight: 500 }}
                  >
                    {f.label}
                  </TableCell>
                  <TableCell sx={{ ...cellSx, py: 0.5 }}>
                    {f.fromEnvironment ? (
                      "From the environment"
                    ) : (
                      <TextField
                        select
                        size="small"
                        value={mappingOf(f)}
                        aria-label={`${f.label} reads`}
                        onChange={(e) =>
                          setPicked((p) => ({
                            ...p,
                            [`${evalId}:${f.name}`]: e.target.value,
                          }))
                        }
                        sx={{ minWidth: 200 }}
                      >
                        {EVIDENCE.map((ev) => (
                          <MenuItem key={ev.key} value={ev.key}>
                            {ev.label}
                          </MenuItem>
                        ))}
                      </TextField>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Box>

        <ScopePicker
          ctx={ctx}
          filter={filter}
          selectedIds={selectedIds}
          state={state}
          runLabel={runLabel}
        />

        <Box sx={{ pt: 1 }}>
          <ScopeReview ctx={ctx} state={state} />
          <Typography
            sx={{ typography: "s3", color: "text.tertiary", mt: 2, mb: 1.5 }}
          >
            Extra to the run&apos;s task checks ·{" "}
            {aboutCredits(estimateOf(readable, 1))}.
          </Typography>
          <Stack direction="row" alignItems="center" spacing={1.5}>
            <Button
              variant="contained"
              color="primary"
              disabled={!evaluation || !readable}
              onClick={() =>
                onRun({ evaluation, mapping, trials, scope, scopeLabel })
              }
            >
              Evaluate {readable} {readable === 1 ? "trial" : "trials"}
            </Button>
            {onCancel && (
              <Button onClick={onCancel} sx={{ color: "text.secondary" }}>
                Cancel
              </Button>
            )}
          </Stack>
        </Box>
      </Stack>
    </Box>
  );
}
UseEvaluationComposer.propTypes = {
  env: PropTypes.object,
  envState: PropTypes.object.isRequired,
  ctx: PropTypes.object.isRequired,
  filter: PropTypes.object,
  selectedIds: PropTypes.array,
  runLabel: PropTypes.string.isRequired,
  initial: PropTypes.object,
  onCancel: PropTypes.func,
  onRun: PropTypes.func.isRequired,
};
