import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Button, Stack, TextField, Typography } from "@mui/material";
import { useSnackbar } from "notistack";
import { ConfirmDialog } from "src/components/custom-dialog";
import { useArchiveScenario } from "src/api/harness/scenarios";
import { LifecycleSections } from "./ScenarioDetail";
import Iconify from "src/components/iconify";
import SideDrawer from "../../components/SideDrawer";
import { normaliseSubTasks } from "./scenarioEditor.constants";
import { personaSubline } from "./personaLabels";
import {
  callActorTimeout,
  callActorWhen,
  effectiveTrigger,
} from "./callActorLabels";
import { SCENARIO_SHAPE } from "./scenarios.shapes";
import { SCENARIOS_COPY } from "./scenarios.constants";

// One scenario, opened from its table row, laid out as the designer's
// ScenarioDrawer: the name (the table's Scenario column) and Edit in the
// header, then one ruled "Scenario" sheet with a row for each of the table's
// other columns, in the table's order and read from the same fields:
// # = `number`, Persona = `persona`, Situation = `instruction`,
// Sub-goals = `sub_goals`, Ideal outcome = `tests`, each shown as sent. The
// designer's figures strip, state chip, must-pass flag, source badge, Path,
// Graded on, Tools it needs and Status sheet have no field behind them, so
// they are left out.

const VAL_SX = { typography: "s1", color: "text.secondary", maxWidth: 760 };
const SECTION_HEAD = {
  typography: "m3",
  fontWeight: "fontWeightMedium",
  color: "text.primary",
  mb: 1,
};

// Archive is offered on a saved case; an unsaved addition has no identity yet, so "remove" applies.
function ArchiveAction({ row, jobId, onDone }) {
  const [confirming, setConfirming] = useState(false);
  const [reason, setReason] = useState("");
  const { enqueueSnackbar } = useSnackbar();
  const archive = useArchiveScenario(jobId);
  const lifecycle = row.lifecycle ?? row._raw?.lifecycle;
  if (!jobId || !lifecycle || row.draftChange === "added") return null;
  const archived = lifecycle.state === "archived";
  const key = row.scenarioKey ?? row._raw?.scenario_key;

  const run = async () => {
    try {
      const done = await archive.mutateAsync({ key, reason, restore: archived });
      const restoredCopy = done?.live ? SCENARIOS_COPY.restoredLive : SCENARIOS_COPY.restored;
      enqueueSnackbar(archived ? restoredCopy : SCENARIOS_COPY.archived, { variant: "success" });
      setConfirming(false);
      onDone?.();
    } catch (error) {
      const code = error?.response?.data?.error;
      enqueueSnackbar(
        code === "draft_changed" ? SCENARIOS_COPY.draftChanged : error?.response?.data?.message || error?.message,
        { variant: "error" },
      );
    }
  };

  return (
    <>
      <Button
        size="small"
        variant="outlined"
        color="inherit"
        onClick={() => (archived ? run() : setConfirming(true))}
        disabled={archive.isPending}
        startIcon={<Iconify icon={archived ? "solar:restart-linear" : "solar:archive-linear"} width={14} />}
        sx={{ flexShrink: 0 }}
      >
        {archived ? SCENARIOS_COPY.restore : SCENARIOS_COPY.archive}
      </Button>
      <ConfirmDialog
        open={confirming}
        onClose={() => setConfirming(false)}
        title={SCENARIOS_COPY.archiveTitle}
        content={
          <TextField
            fullWidth
            size="small"
            label={SCENARIOS_COPY.archiveReason}
            value={reason}
            inputProps={{ maxLength: 255 }}
            onChange={(e) => setReason(e.target.value)}
          />
        }
        action={
          <Button variant="contained" color="primary" onClick={run} disabled={archive.isPending}>
            {SCENARIOS_COPY.archive}
          </Button>
        }
      />
    </>
  );
}

ArchiveAction.propTypes = {
  row: PropTypes.object.isRequired,
  jobId: PropTypes.string,
  onDone: PropTypes.func,
};

export default function ScenarioDrawer({
  row,
  env,
  onClose,
  onEdit,
  locked = false,
}) {
  return (
    <SideDrawer open={!!row} onClose={onClose} width={680}>
      {row && (
        <Stack sx={{ height: "100%" }}>
          <Stack
            direction="row"
            alignItems="center"
            spacing={1.5}
            sx={{
              px: 3,
              py: 2,
              pr: 6,
              borderBottom: "1px solid",
              borderColor: "divider",
              flexShrink: 0,
            }}
          >
            <Box flex={1} minWidth={0}>
              <Typography
                noWrap
                sx={{ typography: "m1", fontWeight: "fontWeightMedium" }}
              >
                {row.name || row.title}
              </Typography>
            </Box>
            {!locked && onEdit && (
              <Button
                size="small"
                variant="outlined"
                color="inherit"
                onClick={() => onEdit(row)}
                startIcon={
                  <Iconify icon="solar:pen-new-square-linear" width={14} />
                }
                sx={{ flexShrink: 0 }}
              >
                Edit
              </Button>
            )}
            {!locked && <ArchiveAction row={row} jobId={env?.id} onDone={onClose} />}
          </Stack>
          <Box sx={{ flex: 1, overflow: "auto" }}>
            <ScenarioSheet row={row} env={env} />
            <Stack spacing={3} sx={{ px: 3, pb: 3 }}>
              <LifecycleSections row={row} jobId={env?.id} />
            </Stack>
          </Box>
        </Stack>
      )}
    </SideDrawer>
  );
}

ScenarioDrawer.propTypes = {
  row: SCENARIO_SHAPE,
  env: PropTypes.shape({ id: PropTypes.string, callActors: PropTypes.array }),
  onClose: PropTypes.func.isRequired,
  onEdit: PropTypes.func,
  locked: PropTypes.bool,
};

function ScenarioSheet({ row, env }) {
  const persona = row.persona?.name ? row.persona : null;
  const listed = Object.fromEntries(
    (env?.callActors || []).map((one) => [one.id, one]),
  );
  const names = Object.fromEntries(
    Object.values(listed).map((one) => [one.id, one.name || one.id]),
  );
  const goals = normaliseSubTasks(row.subTasks);
  // The same fields the table's Situation and Ideal outcome cells read.
  const situation = row.situation || row.task;
  const idealOutcome = row.outcome || row.expected;
  const rows = [
    row.number != null && (
      <Row key="number" label="#">
        <Typography
          className="sd-val"
          sx={{ fontVariantNumeric: "tabular-nums" }}
        >
          {row.number}
        </Typography>
      </Row>
    ),
    persona && (
      <Row key="persona" label="Persona">
        <Typography className="sd-val">
          <Box
            component="span"
            sx={{ color: "text.primary", fontWeight: "fontWeightMedium" }}
          >
            {persona.name}
          </Box>
          {personaSubline(persona) ? ` · ${personaSubline(persona)}` : ""}
        </Typography>
      </Row>
    ),
    situation && (
      <Row key="situation" label="Situation">
        <Typography className="sd-val" sx={{ whiteSpace: "pre-line" }}>
          {situation}
        </Typography>
      </Row>
    ),
    goals.length > 0 && (
      <Row key="goals" label="Sub-goals">
        <Stack spacing={1} sx={{ maxWidth: 760 }}>
          {goals.map((g, i) => (
            <Stack
              key={g.id}
              direction="row"
              spacing={1}
              alignItems="flex-start"
            >
              <Typography
                sx={{
                  typography: "s1",
                  color: "text.disabled",
                  width: 14,
                  flexShrink: 0,
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                {`${i + 1}.`}
              </Typography>
              <Typography className="sd-val" sx={{ flex: 1, minWidth: 0 }}>
                {g.label}
              </Typography>
            </Stack>
          ))}
        </Stack>
      </Row>
    ),
    row.callActors?.length > 0 && (
      <Row key="actors" label="Actors">
        <Stack spacing={1.25} sx={{ maxWidth: 760 }}>
          {row.callActors.map((one) => (
            <Box key={one.id}>
              <Typography className="sd-val">
                <Box
                  component="span"
                  sx={{ color: "text.primary", fontWeight: 600 }}
                >
                  {listed[one.id]?.name || one.id}
                </Box>
                {listed[one.id]?.role ? ` · ${listed[one.id].role}` : ""}
                {` · ${callActorWhen(effectiveTrigger(one, listed[one.id]), listed[one.id]?.channel)}`}
              </Typography>
              {callActorTimeout(listed[one.id], names) && (
                <Typography className="sd-val" sx={{ color: "text.secondary" }}>
                  {callActorTimeout(listed[one.id], names)}
                </Typography>
              )}
              {(one.instruction || listed[one.id]?.goal) && (
                <Typography
                  className="sd-val"
                  sx={{ color: "text.secondary", whiteSpace: "pre-line" }}
                >
                  {one.instruction || listed[one.id]?.goal}
                </Typography>
              )}
            </Box>
          ))}
        </Stack>
      </Row>
    ),
    idealOutcome && (
      <Row key="outcome" label="Ideal outcome">
        <Typography className="sd-val" sx={{ whiteSpace: "pre-line" }}>
          {idealOutcome}
        </Typography>
      </Row>
    ),
  ].filter(Boolean);

  if (rows.length === 0) return null;
  return (
    <Stack spacing={3} sx={{ px: 3, py: 3, "& .sd-val": VAL_SX }}>
      <Box>
        <Typography sx={SECTION_HEAD}>Scenario</Typography>
        <Box
          sx={{
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "8px",
            "& > .sr + .sr": { borderTop: "1px solid", borderColor: "divider" },
          }}
        >
          {rows}
        </Box>
      </Box>
    </Stack>
  );
}

ScenarioSheet.propTypes = {
  row: SCENARIO_SHAPE,
  env: PropTypes.shape({ callActors: PropTypes.array }),
};

function Row({ label, children }) {
  return (
    <Box
      className="sr"
      sx={{
        display: "grid",
        gridTemplateColumns: { xs: "1fr", sm: "124px minmax(0, 1fr)" },
        columnGap: 2,
        rowGap: 0.25,
        px: 1.5,
        py: 1,
      }}
    >
      <Typography sx={{ typography: "s1", color: "text.subtitle" }}>
        {label}
      </Typography>
      <Box minWidth={0}>{children}</Box>
    </Box>
  );
}

Row.propTypes = { label: PropTypes.string, children: PropTypes.node };
