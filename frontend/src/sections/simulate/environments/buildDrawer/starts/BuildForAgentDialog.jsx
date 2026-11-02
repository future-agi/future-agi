import PropTypes from "prop-types";
import { useState } from "react";
import {
  Box,
  Button,
  ButtonBase,
  Dialog,
  MenuItem,
  Select,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { MONO, PreflightRow, useUiOnlyBuild } from "../forms/formBits";
import UiOnlyFlag from "../../components/UiOnlyFlag";
import { BTN_SX, INPUT_SX, T12, T13, T14, focusRing } from "../drawerStyles";
import { envNameFor } from "./startUtils";

// Quick picks; any count from 1 to 1,000 can be typed.
const SIZES = [10, 25, 50, 100];
const MIN_SCENARIOS = 1;
const MAX_SCENARIOS = 1000;

// What preflight would check before an agent's build, as sample results.
const SAMPLE_CHECKS = [
  { label: "Agent", detail: "Reached at its connection. It answered." },
  { label: "Sandbox", detail: "A sandbox is free. The world builds there." },
  { label: "Parallel worlds", detail: "1 at once admitted." },
];

function Label({ children }) {
  return (
    <Typography
      component="label"
      sx={{
        ...T12,
        fontWeight: 600,
        letterSpacing: 0.5,
        color: "text.secondary",
        display: "block",
        mb: 0.75,
      }}
    >
      {children}
    </Typography>
  );
}
Label.propTypes = { children: PropTypes.node };

function Pick({ on, onClick, children }) {
  return (
    <ButtonBase
      aria-pressed={on}
      onClick={onClick}
      sx={{
        px: 1,
        minWidth: 28,
        height: 22,
        borderRadius: "4px",
        border: "1px solid",
        borderColor: on ? "text.disabled" : "divider",
        bgcolor: on ? "action.selected" : "transparent",
        ...T12,
        fontWeight: on ? 600 : 500,
        fontVariantNumeric: "tabular-nums",
        color: on ? "text.primary" : "text.secondary",
        "&:hover": { color: "text.primary" },
        "&.Mui-focusVisible": focusRing,
      }}
    >
      {children}
    </ButtonBase>
  );
}
Pick.propTypes = {
  on: PropTypes.bool,
  onClick: PropTypes.func,
  children: PropTypes.node,
};

function DialogBody({ agent, onClose, subtitle }) {
  const build = useUiOnlyBuild(SAMPLE_CHECKS);
  const [name, setName] = useState(() => envNameFor(agent));
  const version = agent.latestLabel || "Latest";
  // Held as typed, so a half-typed number isn't snapped mid-edit.
  const [countText, setCountText] = useState("10");
  const typed = Math.floor(Number(countText));
  const countOk =
    Number.isFinite(typed) && typed >= MIN_SCENARIOS && typed <= MAX_SCENARIOS;
  const count = countOk
    ? typed
    : Math.min(
        MAX_SCENARIOS,
        Math.max(MIN_SCENARIOS, Number.isFinite(typed) ? typed : MIN_SCENARIOS),
      );

  const blocked = !name.trim()
    ? "Name it first"
    : !countOk
      ? `Scenarios: ${MIN_SCENARIOS} to ${MAX_SCENARIOS.toLocaleString()}`
      : !build.readyToSubmit
        ? "Run preflight first"
        : null;

  return (
    <>
      <Box sx={{ px: 3, pt: 2.5, pb: 1 }}>
        <Stack direction="row" alignItems="center" spacing={1}>
          <Typography sx={{ ...T14, fontWeight: 600 }}>
            Build environment
          </Typography>
          <UiOnlyFlag />
        </Stack>
        <Typography sx={{ ...T12, color: "text.secondary", mt: 0.25 }}>
          {subtitle ||
            `We read ${agent.name} and build its environment: simulated tools, seeded data, rules and scenarios.`}
        </Typography>
      </Box>
      <Stack spacing={2} sx={{ px: 3, py: 2 }}>
        <Box>
          <Label>Name</Label>
          <TextField
            size="small"
            fullWidth
            value={name}
            onChange={(e) => setName(e.target.value)}
            inputProps={{ "aria-label": "Name" }}
            sx={{
              ...INPUT_SX,
              "& .MuiInputBase-input": {
                py: 0.75,
                px: 1,
                ...T13,
              },
            }}
          />
        </Box>
        <Stack direction="row" spacing={1.5}>
          <Box flex={1} minWidth={0}>
            <Label>Built from version</Label>
            <Select
              size="small"
              fullWidth
              value={version}
              inputProps={{ "aria-label": "Built from version" }}
              sx={{ ...T13, "& .MuiSelect-select": { py: 0.75 } }}
            >
              <MenuItem value={version} sx={T13}>
                <Box
                  component="span"
                  sx={{ fontFamily: MONO, fontWeight: 600, mr: 1 }}
                >
                  {version}
                </Box>
                <Box component="span" sx={{ color: "text.secondary" }}>
                  latest
                </Box>
              </MenuItem>
            </Select>
          </Box>
          <Box flex={1} minWidth={0}>
            <Label>Scenarios to start with</Label>
            <TextField
              size="small"
              fullWidth
              type="number"
              value={countText}
              placeholder="10"
              onChange={(e) => setCountText(e.target.value)}
              onBlur={() => setCountText(String(count))}
              error={!countOk}
              inputProps={{
                min: MIN_SCENARIOS,
                max: MAX_SCENARIOS,
                step: 1,
                "aria-label": "Scenarios to start with",
              }}
              sx={{
                ...INPUT_SX,
                "& .MuiInputBase-input": {
                  py: 0.75,
                  px: 1,
                  ...T13,
                  fontVariantNumeric: "tabular-nums",
                },
              }}
            />
          </Box>
        </Stack>
        <Stack
          direction="row"
          alignItems="center"
          spacing={0.75}
          sx={{ mt: -1 }}
        >
          {SIZES.map((n) => (
            <Pick
              key={n}
              on={countOk && typed === n}
              onClick={() => setCountText(String(n))}
            >
              {n}
            </Pick>
          ))}
          <Typography
            sx={{
              ...T12,
              color: countOk ? "text.secondary" : "error.main",
              ml: 0.5,
            }}
          >
            {countOk
              ? `Any number from ${MIN_SCENARIOS} to ${MAX_SCENARIOS.toLocaleString()}`
              : `Pick ${MIN_SCENARIOS} to ${MAX_SCENARIOS.toLocaleString()}`}
          </Typography>
        </Stack>
        <Typography sx={{ ...T12, color: "text.secondary" }}>
          The environment tests this version first. You can add scenarios and
          switch versions afterwards.
        </Typography>
        <PreflightRow
          build={build}
          canRun={!!name.trim() && countOk}
          blockedReason="Name it and pick how many scenarios, then preflight checks the agent."
          onRun={build.runPreflight}
        />
        <Stack
          direction="row"
          spacing={0.75}
          alignItems="flex-start"
          sx={{
            px: 1.25,
            py: 1,
            borderRadius: "6px",
            bgcolor: "action.hover",
          }}
        >
          <Iconify
            icon="solar:info-circle-linear"
            width={14}
            sx={{ color: "text.secondary", flexShrink: 0, mt: "2px" }}
          />
          <Typography sx={{ ...T12, color: "text.secondary" }}>
            One environment per materially different setup: data or systems,
            channel, market, language or product line. Not one per version.
          </Typography>
        </Stack>
      </Stack>
      <Stack
        direction="row"
        alignItems="center"
        justifyContent="flex-end"
        spacing={1}
        sx={{ px: 3, py: 1.75, borderTop: "1px solid", borderColor: "divider" }}
      >
        {blocked && (
          <Typography
            sx={{ ...T12, color: "text.secondary", flex: 1, minWidth: 0 }}
          >
            {blocked}
          </Typography>
        )}
        <Button onClick={onClose} sx={{ ...BTN_SX, color: "text.secondary" }}>
          Cancel
        </Button>
        <Button
          variant="contained"
          color="primary"
          disabled={!!blocked}
          onClick={build.commitBuild}
          sx={BTN_SX}
        >
          Build environment
        </Button>
      </Stack>
    </>
  );
}
DialogBody.propTypes = {
  agent: PropTypes.object.isRequired,
  onClose: PropTypes.func.isRequired,
  subtitle: PropTypes.string,
};

/**
 * "Build environment" for one connected agent: its name, the version it's
 * built from and how many scenarios to start with, then preflight. UI only:
 * preflight shows sample checks and building only says it isn't wired.
 */
export default function BuildForAgentDialog({ agent, onClose, subtitle }) {
  return (
    <Dialog
      open={!!agent}
      onClose={onClose}
      aria-label="Build environment"
      PaperProps={{
        sx: {
          borderRadius: "8px",
          maxWidth: 560,
          width: "100%",
          bgcolor: "background.paper",
          backgroundImage: "none",
          border: "1px solid",
          borderColor: "divider",
        },
      }}
    >
      {agent && (
        <DialogBody
          key={agent.id}
          agent={agent}
          onClose={onClose}
          subtitle={subtitle}
        />
      )}
    </Dialog>
  );
}
BuildForAgentDialog.propTypes = {
  agent: PropTypes.object,
  onClose: PropTypes.func.isRequired,
  subtitle: PropTypes.string,
};
