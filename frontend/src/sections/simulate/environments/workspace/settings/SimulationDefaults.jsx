import PropTypes from "prop-types";
import { useState } from "react";
import { enqueueSnackbar } from "notistack";
import {
  Box,
  Button,
  MenuItem,
  Select,
  Slider,
  Stack,
  Typography,
} from "@mui/material";
import {
  SAMPLE_SIMULATOR_MODEL,
  SAMPLE_SIMULATOR_MODELS,
  SAMPLE_STUCK_AFTER_MIN,
} from "src/api/simulate-environments/_fixtures/detail/settings";
import SoonChip from "../../components/SoonChip";
import UiOnlyFlag from "../../components/UiOnlyFlag";
import { MAX_PARALLELISM, PARALLELISM_COPY } from "../../parallelism.constants";
import { UI_ONLY_SAVED } from "../gaps/NeedsInputButton";
import useRunRepeats from "../run/useRunRepeats";
import SettingsCard from "./SettingsCard";
import { durationWords } from "./settings.constants";

const REPEATS_MAX = 20;
const TURN_OPTIONS = [4, 6, 8, 10, 12, 15, 20, 25, 30, 40];
const TIME_OPTIONS = [300, 900, 1800, 3600, 7200, 14400];
const AT_ONCE_OPTIONS = Array.from(
  { length: MAX_PARALLELISM },
  (_, i) => i + 1,
);

const withCurrent = (opts, v) =>
  (v == null || opts.includes(v) ? opts : [...opts, v]).sort((x, y) => x - y);

const turnsWords = (n) => `${n} turn${n === 1 ? "" : "s"}`;
const atOnceWords = (n) => (n === 1 ? "1 · one at a time" : `${n} at once`);
const atOnceSentence = (n) => {
  if (n == null) return null;
  return n === 1
    ? "Every run takes one conversation at a time."
    : `Every run takes up to ${n} at once.`;
};
const minutesWords = (m) => durationWords(m * 60);

const LABEL_SX = { typography: "s3", color: "text.secondary", mb: 0.5 };
const HELP_SX = { typography: "s3", color: "text.subtitle" };

// A picker over a real value. `value` null reads "-" (the API sent none).
function Pick({ label, value, options, format, onChange, disabled }) {
  return (
    <Box sx={{ minWidth: 0 }}>
      <Typography sx={LABEL_SX}>{label}</Typography>
      <Select
        size="small"
        fullWidth
        displayEmpty
        value={value ?? ""}
        disabled={disabled}
        onChange={(e) => onChange(Number(e.target.value))}
        renderValue={(v) => (v === "" ? "-" : format(v))}
        inputProps={{ "aria-label": label }}
        sx={{ typography: "s2" }}
      >
        {withCurrent(options, value).map((o) => (
          <MenuItem key={o} value={o} sx={{ typography: "s2" }}>
            {format(o)}
          </MenuItem>
        ))}
      </Select>
    </Box>
  );
}
Pick.propTypes = {
  label: PropTypes.string.isRequired,
  value: PropTypes.number,
  options: PropTypes.arrayOf(PropTypes.number).isRequired,
  format: PropTypes.func.isRequired,
  onChange: PropTypes.func.isRequired,
  disabled: PropTypes.bool,
};

// What every new run starts from. Repeats is the workspace's shared value (the
// header's Repeats pill and Run read it). Turns and time come from the
// contract's end conditions and conversations at once from the build's
// runtime. Changing those has no API yet: picking works and Save sends
// nothing. Stuck after and the simulated user's model aren't in the API.
export default function SimulationDefaults({
  env,
  envState,
  patch,
  disabled = false,
  sample = false,
}) {
  const [repeats, setRepeats] = useRunRepeats(envState, patch);
  const ec = env?.endConditions;
  const parallelism = env?.parallelism;
  const real = {
    turns: ec?.max_turns ?? null,
    time: ec?.max_duration_seconds ?? null,
    atOnce:
      env?.runtime?.parallelism ??
      parallelism?.effective ??
      parallelism?.requested ??
      null,
  };
  const [draft, setDraft] = useState({});
  const [stuckAfter, setStuckAfter] = useState(SAMPLE_STUCK_AFTER_MIN);
  const [model, setModel] = useState(SAMPLE_SIMULATOR_MODEL);
  const value = (key) => (key in draft ? draft[key] : real[key]);
  const dirty = Object.keys(draft).some((key) => draft[key] !== real[key]);
  const set = (key) => (v) => setDraft((d) => ({ ...d, [key]: v }));
  const save = () => {
    enqueueSnackbar(UI_ONLY_SAVED, { variant: "info" });
    setDraft({});
  };
  const voice = env?.surface === "voice";
  const modelNote = SAMPLE_SIMULATOR_MODELS.find((m) => m.id === model)?.note;

  return (
    <SettingsCard
      label="Simulation defaults"
      title="Simulation defaults"
      subtitle="What every new run of this environment starts from. A run can still change them."
      action={<UiOnlyFlag feature="environmentSettings" />}
    >
      <Stack spacing={2.5} sx={{ p: 2.5 }}>
        <Stack direction={{ xs: "column", md: "row" }} spacing={3}>
          <Box sx={{ flex: 1, minWidth: 0 }}>
            <Stack
              direction="row"
              alignItems="baseline"
              justifyContent="space-between"
            >
              <Typography sx={{ typography: "s2", fontWeight: 600 }}>
                Repeats per scenario
              </Typography>
              <Typography
                sx={{
                  typography: "s2",
                  fontWeight: 600,
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                {repeats}
              </Typography>
            </Stack>
            <Slider
              size="small"
              value={repeats}
              min={1}
              max={REPEATS_MAX}
              step={1}
              disabled={disabled || !patch}
              onChange={(_, v) => setRepeats(v)}
              aria-label="Repeats per scenario"
              sx={{ py: 1, "& .MuiSlider-thumb": { width: 12, height: 12 } }}
            />
            <Typography sx={HELP_SX}>
              {`Each scenario runs this many times, 1 to ${REPEATS_MAX}. Three tells a flaky scenario from a failing one. Run uses the same number.`}
            </Typography>
          </Box>
          <Box sx={{ flex: 1, minWidth: 0 }}>
            <Pick
              label="Conversations at once"
              value={value("atOnce")}
              options={AT_ONCE_OPTIONS}
              format={atOnceWords}
              onChange={set("atOnce")}
              disabled={disabled}
            />
            <Typography sx={{ ...HELP_SX, mt: 0.75 }}>
              {[
                atOnceSentence(value("atOnce")),
                // The build's own count, only when it ran fewer than asked.
                parallelism?.requested &&
                  (parallelism.effective ?? parallelism.admitted) <
                    parallelism.requested &&
                  `${PARALLELISM_COPY.fact}: ${PARALLELISM_COPY.factValue(parallelism)}.`,
                voice
                  ? "Your provider's concurrency limit still applies."
                  : value("atOnce") !== 1 &&
                    "Results are the same. A run finishes sooner.",
              ]
                .filter(Boolean)
                .join(" ")}
            </Typography>
          </Box>
        </Stack>

        <Box>
          <Typography sx={{ typography: "s2", fontWeight: 600 }}>
            Limits
          </Typography>
          <Typography sx={{ ...HELP_SX, mb: 1.25 }}>
            A conversation stops at whichever comes first. A scenario&apos;s own
            limit wins.
          </Typography>
          <Box
            sx={{
              display: "grid",
              gridTemplateColumns: {
                xs: "1fr",
                sm: "repeat(3, minmax(0, 1fr))",
              },
              gap: 1.5,
              maxWidth: 560,
            }}
          >
            <Pick
              label="Turns"
              value={value("turns")}
              options={TURN_OPTIONS}
              format={turnsWords}
              onChange={set("turns")}
              disabled={disabled}
            />
            <Pick
              label="Time per conversation"
              value={value("time")}
              options={TIME_OPTIONS}
              format={durationWords}
              onChange={set("time")}
              disabled={disabled}
            />
            {sample ? (
              <Pick
                label="Stuck after"
                value={stuckAfter}
                options={[1, 2, 5, 10, 30]}
                format={minutesWords}
                onChange={setStuckAfter}
                disabled={disabled}
              />
            ) : (
              <Box sx={{ minWidth: 0 }}>
                <Typography sx={LABEL_SX}>Stuck after</Typography>
                <SoonChip
                  label="Stuck after"
                  what="A no-activity limit per conversation"
                  sx={{ mt: 0.5 }}
                />
              </Box>
            )}
          </Box>
        </Box>

        <Stack
          direction="row"
          alignItems="center"
          justifyContent="space-between"
          spacing={2}
        >
          <Box minWidth={0}>
            <Typography sx={{ typography: "s2", fontWeight: 600 }}>
              Simulated user&apos;s model
            </Typography>
            <Typography sx={HELP_SX}>
              {sample && modelNote
                ? `${modelNote} Changing it makes the next environment version.`
                : "The model that plays the caller in every scenario."}
            </Typography>
          </Box>
          {sample ? (
            <Select
              size="small"
              value={model}
              disabled={disabled}
              onChange={(e) => setModel(e.target.value)}
              inputProps={{ "aria-label": "Simulated user's model" }}
              sx={{ minWidth: 200, typography: "s2", flexShrink: 0 }}
            >
              {SAMPLE_SIMULATOR_MODELS.map((m) => (
                <MenuItem key={m.id} value={m.id} sx={{ typography: "s2" }}>
                  {m.label}
                </MenuItem>
              ))}
            </Select>
          ) : (
            <SoonChip label="Model" what="The simulated user's model" />
          )}
        </Stack>
      </Stack>
      {/* Vel's caller history: each model change, newest first. */}
      <Stack
        direction="row"
        alignItems="center"
        spacing={0.75}
        sx={{
          px: 2.5,
          py: 1.25,
          borderTop: "1px solid",
          borderColor: "divider",
        }}
      >
        <Typography
          sx={{ typography: "s3", fontWeight: 600, color: "text.subtitle" }}
        >
          Simulated caller changes
        </Typography>
        <SoonChip
          label="History"
          detail="Each change to the simulated user's model, newest first, with the one this environment started with."
          what="The simulated caller's change history"
          size="tiny"
        />
      </Stack>
      {dirty && (
        <Stack
          direction="row"
          justifyContent="flex-end"
          spacing={1}
          sx={{
            px: 2.5,
            py: 1.25,
            borderTop: "1px solid",
            borderColor: "divider",
          }}
        >
          <Button
            size="small"
            onClick={() => setDraft({})}
            sx={{ color: "text.secondary" }}
          >
            Discard
          </Button>
          <Button size="small" variant="contained" onClick={save}>
            Save
          </Button>
        </Stack>
      )}
    </SettingsCard>
  );
}
SimulationDefaults.propTypes = {
  env: PropTypes.object,
  envState: PropTypes.object,
  patch: PropTypes.func,
  disabled: PropTypes.bool,
  sample: PropTypes.bool,
};
