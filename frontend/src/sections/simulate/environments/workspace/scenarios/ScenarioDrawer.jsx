import PropTypes from "prop-types";
import { Box, Button, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import SideDrawer from "../../components/SideDrawer";
import { normaliseSubTasks } from "./scenarioEditor.constants";
import { personaSubline } from "./personaLabels";
import { SCENARIO_SHAPE } from "./scenarios.shapes";
import { ACTORS_COPY } from "../actors/actors.constants";
import {
  TEST_CONDITIONS_COPY,
  testConditionRoute,
} from "./testConditions.constants";

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

export default function ScenarioDrawer({
  row,
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
          </Stack>
          <Box sx={{ flex: 1, overflow: "auto" }}>
            <ScenarioSheet row={row} />
          </Box>
        </Stack>
      )}
    </SideDrawer>
  );
}

ScenarioDrawer.propTypes = {
  row: SCENARIO_SHAPE,
  onClose: PropTypes.func.isRequired,
  onEdit: PropTypes.func,
  locked: PropTypes.bool,
};

function ScenarioSheet({ row }) {
  const persona = row.persona?.name ? row.persona : null;
  const goals = normaliseSubTasks(row.subTasks);
  const problem = row.doorProblem;
  const problemLine = (code) =>
    problem?.code === code && (
      <Typography className="sd-val" sx={{ color: "warning.main" }}>
        {problem.message}
      </Typography>
    );
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
                  {one.name}
                </Box>
                {one.role ? ` · ${one.role}` : ""}
                {` · ${one.when_label}`}
              </Typography>
              {one.timeout_label && (
                <Typography className="sd-val" sx={{ color: "text.secondary" }}>
                  {one.timeout_label}
                </Typography>
              )}
              {one.instruction && (
                <Typography
                  className="sd-val"
                  sx={{ color: "text.secondary", whiteSpace: "pre-line" }}
                >
                  {one.instruction}
                </Typography>
              )}
              {(one.actions || []).map((step, index) => (
                <Typography
                  key={`${step.action}-${index}`}
                  className="sd-val"
                  sx={{ color: "text.secondary" }}
                >
                  {ACTORS_COPY.scenarioActions.takes(
                    step.action,
                    step.when_label,
                  )}
                </Typography>
              ))}
            </Box>
          ))}
          {problemLine("actor_no_door")}
        </Stack>
      </Row>
    ),
    <Row key="conditions" label={TEST_CONDITIONS_COPY.heading}>
      <Stack spacing={0.5} sx={{ maxWidth: 760 }}>
        {row.faults?.length > 0 ? (
          row.faults.map((fault, index) => (
            <Box key={`${fault.action}-${index}`}>
              <Typography className="sd-val">{fault.line}</Typography>
              {testConditionRoute(fault) && (
                <Typography
                  className="sd-val"
                  sx={{ typography: "s2", color: "text.subtitle" }}
                >
                  {testConditionRoute(fault)}
                </Typography>
              )}
            </Box>
          ))
        ) : (
          <Typography className="sd-val">
            {TEST_CONDITIONS_COPY.empty}
          </Typography>
        )}
        {problemLine("fault_unreachable")}
      </Stack>
    </Row>,
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
