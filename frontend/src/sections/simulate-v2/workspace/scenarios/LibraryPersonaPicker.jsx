import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Button, Stack, Tooltip, Typography } from "@mui/material";
import PersonaListView from "src/sections/persona/PersonaListView";
import PersonaTaskPlan from "../../components/PersonaTaskPlan";
import {
  DEFAULT_TASK_PLAN,
  personaTypeFor,
  planSummary,
  scenariosForPersonas,
} from "../../_mock/personaScenarios";

/**
 * Add scenarios → From persona library. Pick personas the team keeps in
 * Environments → Personas, choose which of this environment's tasks each one
 * gets, and each scenario is written in that persona's voice. The list opens
 * on the environment's own type (voice or chat).
 */
export default function LibraryPersonaPicker({ env, envState, onAdd }) {
  const [picked, setPicked] = useState([]);
  const [plan, setPlan] = useState(DEFAULT_TASK_PLAN);
  const type = personaTypeFor(env);
  const summary = planSummary(env, envState, picked, plan);
  const total = summary?.total || 0;

  const toggle = (persona, on) =>
    setPicked((xs) =>
      on
        ? xs.some((p) => p.id === persona.id)
          ? xs
          : [...xs, persona]
        : xs.filter((p) => p.id !== persona.id),
    );

  return (
    <Box
      sx={{
        flex: 1,
        minHeight: 0,
        display: "flex",
        flexDirection: "column",
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "8px",
        overflow: "hidden",
      }}
    >
      <Box
        sx={{
          flex: 1,
          minHeight: 360,
          display: "flex",
          flexDirection: "column",
          p: 2,
        }}
      >
        <PersonaListView
          isSelectable
          selectedPersonas={picked}
          onToggleSelect={toggle}
          personaCreateEditType={type}
        />
      </Box>
      <Stack
        direction="row"
        alignItems="center"
        spacing={1.5}
        sx={{
          px: 2,
          py: 1.5,
          borderTop: "1px solid",
          borderColor: "divider",
          flexShrink: 0,
        }}
      >
        {/* The sentence names the first couple of tasks; hover for all of them. */}
        <Tooltip
          placement="top-start"
          title={
            summary?.tasks?.length > 2
              ? summary.tasks.map((t) => <div key={t}>{t}</div>)
              : ""
          }
        >
          <Typography
            sx={{
              typography: "s2",
              flex: 1,
              minWidth: 0,
              color: picked.length ? "text.primary" : "text.tertiary",
            }}
          >
            {summary
              ? summary.text
              : "Pick personas from the library — Future AGI built or your team's own."}
          </Typography>
        </Tooltip>
        <PersonaTaskPlan
          env={env}
          envState={envState}
          plan={plan}
          onChange={setPlan}
        />
        <Button
          size="small"
          variant="contained"
          color="primary"
          disabled={!total}
          onClick={() =>
            onAdd(scenariosForPersonas(env, envState, picked, plan).rows)
          }
          sx={{ whiteSpace: "nowrap" }}
        >
          {total
            ? `Add ${total} scenario${total === 1 ? "" : "s"}`
            : "Add scenarios"}
        </Button>
      </Stack>
    </Box>
  );
}

LibraryPersonaPicker.propTypes = {
  env: PropTypes.object,
  envState: PropTypes.object,
  onAdd: PropTypes.func.isRequired,
};
