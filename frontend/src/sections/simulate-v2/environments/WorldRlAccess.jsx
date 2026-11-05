import PropTypes from "prop-types";
import { useState } from "react";
import {
  Box,
  Button,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { REWARD_VERSIONS, TOOL_FORMATS, rlCodeOf } from "../_mock/release";

/*
  RL access from the catalogue (rl.md step 1: Catalogue → environment → RL
  access). A launch world ships as a locked release with train and test
  splits, so a trainer can start from here without adopting it first. Test
  tasks are listed for evaluation without their answers.
*/
const Row = ({ label, children }) => (
  <Stack
    direction="row"
    spacing={2}
    sx={{
      px: 1.5,
      py: 0.875,
      borderTop: "1px solid",
      borderColor: "divider",
      "&:first-of-type": { borderTop: 0 },
    }}
  >
    <Typography
      sx={{
        typography: "s2",
        color: "text.tertiary",
        width: 150,
        flexShrink: 0,
      }}
    >
      {label}
    </Typography>
    <Box sx={{ typography: "s2", color: "text.primary", minWidth: 0 }}>
      {children}
    </Box>
  </Stack>
);
Row.propTypes = { label: PropTypes.node, children: PropTypes.node };

export default function WorldRlAccess({ world }) {
  const entry = world.entry;
  const ready = entry?.release?.status === "ready";
  const [split, setSplit] = useState("train");
  const [format, setFormat] = useState("openai");
  const [reward, setReward] = useState("v1");
  const [key, setKey] = useState(null);

  if (!ready)
    return (
      <Typography sx={{ typography: "s2", color: "text.secondary" }}>
        RL access opens once this world passes its release checks and ships as a
        locked release with train and test splits.
      </Typography>
    );

  const n = world.scenarios || 40;
  const test = Math.max(4, Math.round(n * 0.2));
  const train = n - test;
  const code = rlCodeOf(
    { id: entry.id, tools: world.template?.tools || [] },
    {
      lockedRelease: { version: entry.release.version, rewardVersion: reward },
      rlReward: reward,
    },
    split,
    format,
  );

  return (
    <Stack spacing={2}>
      <Typography sx={{ typography: "s2", color: "text.secondary" }}>
        Train your own model on this world. We run the world, the simulated
        people and the reward; sessions use the same credits as simulations.
      </Typography>

      <Box
        sx={{
          border: "1px solid",
          borderColor: "divider",
          borderRadius: "8px",
        }}
      >
        <Row label="Release">{`${entry.release.version} · locked · scorer v1`}</Row>
        <Row label="Splits">
          {`Train ${train} tasks · Test ${test} tasks (answers and hidden checks stay with us)`}
        </Row>
        <Row label="Reward">
          From the same sub-goal checks and judged criteria that decide a
          run&apos;s pass or fail. A scoring failure is a grading error, never
          0.
        </Row>
        <Row label="Reward checks">
          <Box component="span" sx={{ color: "status.success.fg" }}>
            Passed
          </Box>
          {" — repeating an action earns nothing (negative control)"}
        </Row>
      </Box>

      <Stack direction="row" spacing={1.5} useFlexGap sx={{ flexWrap: "wrap" }}>
        <TextField
          select
          size="small"
          label="Split"
          value={split}
          onChange={(e) => setSplit(e.target.value)}
          sx={{ width: 180 }}
        >
          <MenuItem value="train">{`Train · ${train} tasks`}</MenuItem>
          <MenuItem value="test">{`Test · ${test} tasks`}</MenuItem>
        </TextField>
        <TextField
          select
          size="small"
          label="Tool format"
          value={format}
          onChange={(e) => setFormat(e.target.value)}
          sx={{ width: 180 }}
        >
          {TOOL_FORMATS.map((f) => (
            <MenuItem key={f.id} value={f.id}>
              {f.label}
            </MenuItem>
          ))}
        </TextField>
        <TextField
          select
          size="small"
          label="Reward version"
          value={reward}
          onChange={(e) => setReward(e.target.value)}
          sx={{ width: 150 }}
        >
          {REWARD_VERSIONS.map((v) => (
            <MenuItem key={v} value={v}>
              {v}
            </MenuItem>
          ))}
        </TextField>
      </Stack>

      <Stack direction="row" alignItems="center" spacing={1.5}>
        <Typography
          sx={{ typography: "s2", color: "text.tertiary", width: 70 }}
        >
          API key
        </Typography>
        <Typography
          sx={{
            typography: "s2",
            flex: 1,
            fontFamily: "ui-monospace, Menlo, monospace",
          }}
        >
          {key || "None yet"}
        </Typography>
        <Button
          size="small"
          variant="outlined"
          onClick={() =>
            setKey(`fi_rl_••••••••${Math.random().toString(36).slice(2, 6)}`)
          }
          startIcon={<Iconify icon="solar:key-linear" width={14} />}
          sx={{ color: "text.primary", borderColor: "divider" }}
        >
          {key ? "Create another" : "Create API key"}
        </Button>
      </Stack>

      <Box
        component="pre"
        sx={{
          m: 0,
          p: 1.5,
          borderRadius: "8px",
          bgcolor: "background.neutral",
          typography: "s3",
          fontFamily: "ui-monospace, Menlo, monospace",
          overflow: "auto",
          whiteSpace: "pre",
        }}
      >
        {code}
      </Box>
    </Stack>
  );
}
WorldRlAccess.propTypes = { world: PropTypes.object.isRequired };
