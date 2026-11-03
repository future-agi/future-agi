import PropTypes from "prop-types";
import { Box, Button, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import SideDrawer from "../../components/SideDrawer";
import { normaliseSubTasks } from "./scenarioEditor.constants";
import { personaSubline } from "./personaLabels";
import { SCENARIO_SHAPE } from "./scenarios.shapes";

// One scenario, opened from its table row, laid out as the designer's
// ScenarioDrawer: the name (the table's Scenario column) and Edit in the
// header, then one ruled "Scenario" sheet with a row for each of the table's
// other columns, in the table's order and read from the same fields:
// # = `number`, Persona = `persona`, Situation = `instruction`,
// Sub-goals = `sub_goals`, Ideal outcome = `tests`, each shown as sent. The
// designer's figures strip, state chip, must-pass flag, source badge, Path,
// Graded on, Tools it needs and Status sheet have no field behind them, so
// they are left out.

const VAL_SX = { typography: "s2", color: "text.secondary", lineHeight: 1.6, maxWidth: 760 };
const SECTION_HEAD = {
  typography: "s3", fontWeight: 700, color: "text.subtitle", textTransform: "uppercase", letterSpacing: 0.5, mb: 1,
};

export default function ScenarioDrawer({ row, onClose, onEdit, locked = false }) {
  return (
    <SideDrawer open={!!row} onClose={onClose} width={680}>
      {row && (
        <Stack sx={{ height: "100%" }}>
          <Stack
            direction="row" alignItems="center" spacing={1.5}
            sx={{ px: 2.5, py: 2, pr: 6, borderBottom: "1px solid", borderColor: "divider", flexShrink: 0 }}
          >
            <Box flex={1} minWidth={0}>
              <Typography noWrap sx={{ typography: "s1", fontWeight: 600 }}>
                {row.name || row.title}
              </Typography>
            </Box>
            {!locked && onEdit && (
              <Button
                size="small" variant="outlined"
                onClick={() => onEdit(row)}
                startIcon={<Iconify icon="solar:pen-new-square-linear" width={14} />}
                sx={{ typography: "s2", fontWeight: 700, color: "text.primary", borderColor: "divider", flexShrink: 0 }}
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
  // The same fields the table's Situation and Ideal outcome cells read.
  const situation = row.situation || row.task;
  const idealOutcome = row.outcome || row.expected;
  const rows = [
    row.number != null && (
      <Row key="number" label="#">
        <Typography className="sd-val" sx={{ fontVariantNumeric: "tabular-nums" }}>{row.number}</Typography>
      </Row>
    ),
    persona && (
      <Row key="persona" label="Persona">
        <Typography className="sd-val">
          <Box component="span" sx={{ color: "text.primary", fontWeight: 600 }}>{persona.name}</Box>
          {personaSubline(persona) ? ` · ${personaSubline(persona)}` : ""}
        </Typography>
      </Row>
    ),
    situation && (
      <Row key="situation" label="Situation">
        <Typography className="sd-val" sx={{ whiteSpace: "pre-line" }}>{situation}</Typography>
      </Row>
    ),
    goals.length > 0 && (
      <Row key="goals" label="Sub-goals">
        <Stack spacing={1} sx={{ maxWidth: 760 }}>
          {goals.map((g, i) => (
            <Stack key={g.id} direction="row" spacing={1} alignItems="flex-start">
              <Typography
                sx={{
                  typography: "s2", color: "text.disabled", lineHeight: 1.6, width: 14, flexShrink: 0,
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                {`${i + 1}.`}
              </Typography>
              <Typography className="sd-val" sx={{ flex: 1, minWidth: 0 }}>{g.label}</Typography>
            </Stack>
          ))}
        </Stack>
      </Row>
    ),
    idealOutcome && (
      <Row key="outcome" label="Ideal outcome">
        <Typography className="sd-val" sx={{ whiteSpace: "pre-line" }}>{idealOutcome}</Typography>
      </Row>
    ),
  ].filter(Boolean);

  if (rows.length === 0) return null;
  return (
    <Stack spacing={2.5} sx={{ px: 2.5, py: 2.25, "& .sd-val": VAL_SX }}>
      <Box>
        <Typography sx={SECTION_HEAD}>Scenario</Typography>
        <Box
          sx={{
            border: "1px solid", borderColor: "divider", borderRadius: 1,
            "& > .sr + .sr": { borderTop: "1px solid", borderColor: "divider" },
          }}
        >
          {rows}
        </Box>
      </Box>
    </Stack>
  );
}

ScenarioSheet.propTypes = { row: SCENARIO_SHAPE };

function Row({ label, children }) {
  return (
    <Box
      className="sr"
      sx={{
        display: "grid", gridTemplateColumns: { xs: "1fr", sm: "124px minmax(0, 1fr)" },
        columnGap: 2, rowGap: 0.25, px: 1.75, py: 1.125,
      }}
    >
      <Typography sx={{ typography: "s2", color: "text.subtitle", lineHeight: 1.6 }}>{label}</Typography>
      <Box minWidth={0}>{children}</Box>
    </Box>
  );
}

Row.propTypes = { label: PropTypes.string, children: PropTypes.node };
