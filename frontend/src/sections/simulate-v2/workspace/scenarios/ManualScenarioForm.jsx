import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Button, Stack, TextField, Typography } from "@mui/material";
import CopyCommandButton from "../../components/CopyCommandButton";
import { personaFor } from "../../_mock/scenarios";
import { scenarioCli } from "../../_mock/scenarioOrg";

/**
 * A scenario written by hand (roadmap scenarios.md "Create from manual
 * entry"): the situation, the starting state, what passes and what must
 * not happen — kept apart, because the same words with a different
 * starting state are a different test. It lands as a draft and is checked
 * like any other; one that fails is kept, blocked, with the reason.
 */
export default function ManualScenarioForm({ env, onAdd }) {
  const [f, setF] = useState({
    title: "",
    situation: "",
    state: "",
    expected: "",
    forbidden: "",
  });
  const set = (k) => (e) => setF((x) => ({ ...x, [k]: e.target.value }));
  const ok = f.title.trim() && f.situation.trim() && f.expected.trim();
  const add = () =>
    onAdd([
      {
        id: `${env.id}-manual-${Date.now().toString(36)}`,
        title: f.title.trim(),
        summary: f.title.trim(),
        task: f.situation.trim(),
        situation: f.situation.trim(),
        setupNote: f.state.trim() || null,
        expected: f.expected.trim(),
        outcome: f.expected.trim(),
        forbidden: f.forbidden
          .split("\n")
          .map((x) => x.trim())
          .filter(Boolean),
        persona: personaFor(env, 0),
        turns: 6,
        proposed: { at: new Date().toISOString(), source: "manual" },
      },
    ]);
  return (
    <Stack spacing={1.5}>
      <TextField
        size="small"
        label="Name"
        placeholder="Duplicate refund retry"
        value={f.title}
        onChange={set("title")}
      />
      <TextField
        size="small"
        label="Situation — what the caller brings"
        multiline
        minRows={2}
        placeholder="The caller says the refund failed and asks to try again."
        value={f.situation}
        onChange={set("situation")}
      />
      <TextField
        size="small"
        label="Starting state"
        multiline
        minRows={2}
        placeholder="Order O-104 already has refund RF-7 committed."
        value={f.state}
        onChange={set("state")}
        helperText="Without it, the same words describe a different test."
      />
      <TextField
        size="small"
        label="Passes when"
        multiline
        minRows={2}
        placeholder="Exactly one refund exists, RF-7 is reused, and the caller is told it already went through."
        value={f.expected}
        onChange={set("expected")}
      />
      <TextField
        size="small"
        label="Must not happen"
        multiline
        placeholder="One per line — a second refund; telling the caller it failed"
        value={f.forbidden}
        onChange={set("forbidden")}
      />
      <Stack direction="row" alignItems="center" spacing={1}>
        <Box flex={1}>
          <CopyCommandButton command={scenarioCli.create(env)} />
        </Box>
        <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
          Lands as a draft · checked before it can run
        </Typography>
        <Button
          size="small"
          variant="contained"
          color="primary"
          disabled={!ok}
          onClick={add}
        >
          Add draft
        </Button>
      </Stack>
    </Stack>
  );
}

ManualScenarioForm.propTypes = {
  env: PropTypes.object.isRequired,
  onAdd: PropTypes.func.isRequired,
};
