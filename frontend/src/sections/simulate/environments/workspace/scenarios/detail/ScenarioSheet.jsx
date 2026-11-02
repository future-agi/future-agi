import PropTypes from "prop-types";
import { Box, Chip, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import SoonChip from "../../../components/SoonChip";
import { normaliseSubTasks } from "../scenarioEditor.constants";
import { personaSubline } from "../personaLabels";
import { SCENARIO_SHAPE } from "../scenarios.shapes";
import { Figure, InlineLink, Part, Row } from "./sheetParts";
import { MONO, VAL_SX } from "./sheet.constants";
import { InternalsRow, ProofRow } from "./ProofSections";

// Vel's scenario drawer sheet, read like a record: four figures across the
// top, then the scenario, its status and its history, each a ruled part.
// Real fields: # = `number`, Passes when = `tests`, Caller = `persona`,
// Brief = `instruction`, Sub-goals = `sub_goals`, Path = `branch`, Latest run
// = `status` and `call_execution_id`. What Vel shows with no field behind it
// is a SoonChip; Proof and Internals fill only with `?uiSample=proof`.

const initialsOf = (name = "") =>
  name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0].toUpperCase())
    .join("");

// "authored" → "Authored", "in_progress" → "In progress".
const statusLabel = (status) => {
  const words = String(status).replace(/_/g, " ").toLowerCase();
  return words.charAt(0).toUpperCase() + words.slice(1);
};

function Caller({ persona }) {
  const subline = personaSubline(persona);
  return (
    <Stack direction="row" alignItems="center" spacing={1}>
      <Box
        sx={{
          width: 21,
          height: 21,
          borderRadius: "50%",
          flexShrink: 0,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          bgcolor: "action.hover",
          border: "1px solid",
          borderColor: "divider",
        }}
      >
        <Typography
          sx={{
            fontSize: 12,
            fontWeight: 600,
            color: "text.secondary",
            letterSpacing: 0.2,
          }}
        >
          {initialsOf(persona.name)}
        </Typography>
      </Box>
      <Typography className="sd-val">
        <Box component="span" sx={{ color: "text.primary", fontWeight: 600 }}>
          {persona.name}
        </Box>
        {subline ? ` · ${subline}` : ""}
      </Typography>
    </Stack>
  );
}
Caller.propTypes = { persona: PropTypes.object };

function NumberedList({ items }) {
  return (
    <Stack spacing={1} sx={{ maxWidth: 760 }}>
      {items.map((g, i) => (
        <Stack key={g.id} direction="row" spacing={1} alignItems="flex-start">
          <Typography
            sx={{
              typography: "s2",
              color: "text.disabled",
              lineHeight: 1.6,
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
  );
}
NumberedList.propTypes = { items: PropTypes.array };

// The conversation's path: a list of handlers reads as chips, a sentence as text.
function PathSteps({ branch }) {
  if (!Array.isArray(branch)) {
    return <Typography className="sd-val">{branch}</Typography>;
  }
  return (
    <Stack direction="row" alignItems="center" flexWrap="wrap" gap={0.5}>
      {branch.map((st, i) => (
        <Stack key={`${st}-${i}`} direction="row" alignItems="center" gap={0.5}>
          {i > 0 && (
            <Iconify
              icon="solar:arrow-right-linear"
              width={12}
              sx={{ color: "text.disabled" }}
            />
          )}
          <Chip
            size="small"
            variant="outlined"
            label={st}
            sx={{ fontFamily: MONO }}
          />
        </Stack>
      ))}
    </Stack>
  );
}
PathSteps.propTypes = {
  branch: PropTypes.oneOfType([
    PropTypes.string,
    PropTypes.arrayOf(PropTypes.string),
  ]),
};

function LatestRun({ status, callExecutionId, onOpenConversation, opening }) {
  return (
    <Stack direction="row" alignItems="center" spacing={1}>
      {status && (
        <Typography
          className="sd-val"
          sx={{ color: "text.primary !important", fontWeight: 600 }}
        >
          {statusLabel(status)}
        </Typography>
      )}
      {status && !callExecutionId && (
        <Box component="span" sx={{ color: "text.disabled", typography: "s2" }}>
          ·
        </Box>
      )}
      {!callExecutionId && (
        <Typography className="sd-val">No run yet</Typography>
      )}
      <Box sx={{ flex: 1 }} />
      {callExecutionId && onOpenConversation && (
        <InlineLink
          onClick={onOpenConversation}
          disabled={opening}
          aria-busy={opening}
        >
          Open conversation
          <Iconify icon="solar:arrow-right-linear" width={12} />
        </InlineLink>
      )}
    </Stack>
  );
}
LatestRun.propTypes = {
  status: PropTypes.string,
  callExecutionId: PropTypes.string,
  onOpenConversation: PropTypes.func,
  opening: PropTypes.bool,
};

export default function ScenarioSheet({
  row,
  onOpenConversation,
  opening = false,
}) {
  const persona = row.persona?.name ? row.persona : null;
  const goals = normaliseSubTasks(row.subTasks);
  // The same fields the table's Situation and Ideal outcome cells read.
  const brief = row.situation || row.task;
  const passesWhen = row.outcome || row.expected;
  const branch = row.conversationBranch;

  const facts = [
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
    passesWhen && (
      <Row key="passes" label="Passes when">
        <Typography
          className="sd-val"
          sx={{
            color: "text.primary !important",
            fontWeight: 500,
            whiteSpace: "pre-line",
          }}
        >
          {passesWhen}
        </Typography>
      </Row>
    ),
    persona && (
      <Row key="caller" label="Caller">
        <Caller persona={persona} />
      </Row>
    ),
    brief && (
      <Row key="brief" label="Brief">
        <Typography className="sd-val" sx={{ whiteSpace: "pre-line" }}>
          {brief}
        </Typography>
      </Row>
    ),
    goals.length > 0 && (
      <Row key="goals" label="Sub-goals">
        <NumberedList items={goals} />
      </Row>
    ),
    (Array.isArray(branch) ? branch.length > 0 : !!branch) && (
      <Row key="path" label="Path">
        <PathSteps branch={branch} />
      </Row>
    ),
  ].filter(Boolean);

  return (
    <Stack spacing={2.5} sx={{ px: 2.5, py: 2.25, "& .sd-val": VAL_SX }}>
      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: "repeat(4, minmax(0, 1fr))",
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 1,
          "& > * + *": { borderLeft: "1px solid", borderColor: "divider" },
        }}
      >
        <Figure
          label="Runs"
          value={
            <SoonChip
              label="Runs"
              what="A scenario's results across its recent runs"
              plural
              size="tiny"
            />
          }
        />
        <Figure
          testId="figure-sub-goals"
          label="Sub-goals"
          value={goals.length}
          sub={goals.length ? null : "None set"}
        />
        <Figure
          label="Checks"
          value={
            <SoonChip
              label="Checks"
              what="The checks that prove a scenario"
              plural
              size="tiny"
            />
          }
        />
        <Figure
          label="Changes"
          value={
            <SoonChip
              label="Changes"
              what="A scenario's edit history"
              size="tiny"
            />
          }
        />
      </Box>

      {facts.length > 0 && (
        <Part title="Scenario">
          {facts}
          <Row label="Graded on">
            <SoonChip
              label="Grading"
              what="What a run of this scenario is graded on"
              size="tiny"
            />
          </Row>
          <Row label="Tools it needs">
            <SoonChip
              label="Tools"
              what="The tools a scenario needs the world to answer"
              plural
              size="tiny"
            />
          </Row>
        </Part>
      )}

      <Part title="Status">
        <Row label="Latest run">
          <LatestRun
            status={row.status}
            callExecutionId={row.callExecutionId}
            onOpenConversation={onOpenConversation}
            opening={opening}
          />
        </Row>
        <Row label="Health">
          <SoonChip
            label="Health" detail="Whether the scenario can still run, and whether it is still correct for the current agent."
            what="Whether a scenario can still run and is still correct"
            size="tiny"
          />
        </Row>
        <Row label="Protection">
          <SoonChip
            label="Protection" detail="Whether an improvement may break this scenario: must-pass, remembered as a past regression, or passing."
            what="Whether an improvement may break a scenario (must-pass, regression memory or passing)"
            size="tiny"
          />
        </Row>
        <ProofRow />
        <InternalsRow />
      </Part>

      <Part title="History">
        <Row label="From">
          <SoonChip
            label="Origin"
            what="Where a production or generated scenario came from"
            size="tiny"
          />
        </Row>
      </Part>
    </Stack>
  );
}

ScenarioSheet.propTypes = {
  row: SCENARIO_SHAPE.isRequired,
  onOpenConversation: PropTypes.func,
  opening: PropTypes.bool,
};
