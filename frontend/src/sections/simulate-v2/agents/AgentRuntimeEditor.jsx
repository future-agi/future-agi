import PropTypes from "prop-types";
import { useState } from "react";
import { useSnackbar } from "notistack";
import { Box, Button, Stack, TextField, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { useConnectedAgents } from "../store";

const MONO = "ui-monospace, Menlo, monospace";
const valid = (u) => /^(https?|wss?):\/\/\S+$/i.test(u.trim());

/**
 * Where our adapter's serve() runs the agent — the URL a simulation calls on
 * every turn. A framework agent can be connected before serve() is running,
 * so this can be missing; until it's set (or its repository is linked, so we
 * build it ourselves), it can be versioned and read but not simulated.
 *
 * variant "prompt": the agent page's call-out when it's missing.
 * variant "embedded": the same, inside an environment's test-subject card.
 * variant "inline": the editor inside the Connection tab.
 */
export default function AgentRuntimeEditor({
  agent,
  current,
  variant = "prompt",
  onDone,
}) {
  const { setAgentRuntime } = useConnectedAgents();
  const { enqueueSnackbar } = useSnackbar();
  const [value, setValue] = useState(current || "");
  const ok = valid(value);

  const save = () => {
    setAgentRuntime(agent.key, value.trim());
    enqueueSnackbar(
      `${agent.name} runs at ${value.trim()} — ready to simulate`,
      { variant: "success" },
    );
    onDone?.();
  };

  const field = (
    <Stack
      direction="row"
      spacing={1}
      alignItems="flex-start"
      sx={{ width: "100%" }}
    >
      <TextField
        size="small"
        fullWidth
        value={value}
        onChange={(e) => setValue(e.target.value)}
        autoFocus={variant === "inline"}
        placeholder="https://your-agent.example.com/chat"
        error={!!value && !ok}
        helperText={
          value && !ok
            ? "Enter a full URL starting with https://, http:// or wss://"
            : undefined
        }
        InputProps={{ sx: { typography: "s2", fontFamily: MONO } }}
        onKeyDown={(e) => {
          if (e.key === "Enter" && ok) save();
        }}
      />
      <Button
        size="small"
        variant="contained"
        color="primary"
        disabled={!ok}
        onClick={save}
        sx={{ flexShrink: 0 }}
      >
        Save
      </Button>
      {variant === "inline" && (
        <Button
          size="small"
          onClick={onDone}
          sx={{ color: "text.secondary", flexShrink: 0 }}
        >
          Cancel
        </Button>
      )}
    </Stack>
  );

  if (variant === "inline") return field;

  const frame =
    variant === "embedded"
      ? { mt: 1.5, pt: 1.5, borderTop: "1px solid", borderColor: "divider" }
      : {
          mb: 2,
          p: 1.75,
          borderRadius: "8px",
          border: "1px solid",
          borderColor: "divider",
        };
  return (
    <Box sx={frame}>
      <Stack
        direction="row"
        spacing={1.5}
        alignItems="flex-start"
        sx={{ mb: 1.25 }}
      >
        <Iconify
          icon="solar:danger-triangle-linear"
          width={20}
          sx={{ color: "status.warning.fg", mt: 0.125, flexShrink: 0 }}
        />
        <Box>
          <Typography sx={{ typography: "s2", fontWeight: 600 }}>
            Add where serve() runs this agent
          </Typography>
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
            {variant === "embedded"
              ? "A simulation calls the address our adapter's serve() answers on — runs stay blocked until it has one. It applies wherever this agent is tested."
              : "Simulations call the address our adapter's serve() answers on. Until then it can be versioned and inspected, not run."}
          </Typography>
        </Box>
      </Stack>
      {field}
    </Box>
  );
}

AgentRuntimeEditor.propTypes = {
  agent: PropTypes.object.isRequired,
  current: PropTypes.string,
  variant: PropTypes.oneOf(["prompt", "embedded", "inline"]),
  onDone: PropTypes.func,
};
