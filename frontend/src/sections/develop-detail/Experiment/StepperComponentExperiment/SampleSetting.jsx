// W1-6 (notify D40): run a seeded random sample of the rows in scope. The
// create body sends `sample: {n, seed}`; the server picks
// `prng.shuffled(rowIds, seed)`, so the same seed on the same scope picks
// the same rows again (a kept run plays them too). Review and the run's
// details read `25 random rows (seed 7) of 248` (runLedger.sampleLine).
import React from "react";
import PropTypes from "prop-types";
import {
  Checkbox,
  FormControlLabel,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { useController } from "react-hook-form";
import { sampleLine } from "src/sections/common/Runs/runLedger";
import { wizardSettingShown } from "src/launch/wizardSettings";

const toInt = (v) => {
  const n = Math.round(Number(v));
  return Number.isFinite(n) ? n : null;
};

const SampleSetting = ({
  control,
  inScope = null,
  disabled = false,
  noun = "row",
  fixedText = null,
}) => {
  const { field } = useController({ control, name: "sample" });
  const sample = field.value ?? null;
  const on = Boolean(sample?.on);
  const of = Number(inScope) > 0 ? Number(inScope) : null;
  const set = (patch) =>
    field.onChange({
      on: true,
      n: sample?.n ?? null,
      seed: sample?.seed ?? 0,
      ...patch,
    });
  const n = toInt(sample?.n);
  // L6 launch switch (knob:sample): dev runs every row; a launch build shows
  // the sample only while it is on (src/launch/wizardSettings.js). A fixed
  // sample (Edit: the rows were chosen at create) shows in every build,
  // read-only, with `fixedText` saying why.
  const locked = disabled || Boolean(fixedText);
  if (!fixedText && !wizardSettingShown("sample", on)) return null;
  return (
    <Stack spacing={0.5} data-testid="sample-setting">
      <Stack direction="row" alignItems="center" gap={1} flexWrap="wrap">
        <FormControlLabel
          sx={{ mr: 0 }}
          control={
            <Checkbox
              size="small"
              checked={on}
              disabled={locked}
              onChange={(e) =>
                field.onChange(
                  e.target.checked
                    ? {
                        on: true,
                        n: sample?.n ?? null,
                        seed: sample?.seed ?? 0,
                      }
                    : null,
                )
              }
              inputProps={{ "aria-label": "Run a random sample of the rows" }}
            />
          }
          label={<Typography typography="s2">A random</Typography>}
        />
        <TextField
          size="small"
          type="number"
          value={on && n != null ? n : ""}
          disabled={locked || !on}
          onChange={(e) => {
            const v = toInt(e.target.value);
            set({ n: v != null && v > 0 ? v : null });
          }}
          inputProps={{
            min: 1,
            max: of ?? undefined,
            "aria-label": `${noun.charAt(0).toUpperCase()}${noun.slice(1)}s in the sample`,
          }}
          sx={{ width: 84 }}
        />
        <Typography typography="s2">{`${noun}s · seed`}</Typography>
        <TextField
          size="small"
          type="number"
          value={on ? sample.seed ?? 0 : ""}
          disabled={locked || !on}
          onChange={(e) => set({ seed: toInt(e.target.value) ?? 0 })}
          inputProps={{ "aria-label": "Sample seed" }}
          sx={{ width: 76 }}
        />
      </Stack>
      <Typography typography="s3" color="text.secondary">
        {!on
          ? `Runs every ${noun} in scope.`
          : n
            ? `${sampleLine({ n, seed: sample.seed ?? 0, of }, { noun })}. The same seed picks the same ${noun}s again.`
            : `Enter how many ${noun}s to sample; Continue waits for it.`}
      </Typography>
      {fixedText ? (
        <Typography
          typography="s3"
          color="text.secondary"
          data-testid="sample-fixed"
        >
          {fixedText}
        </Typography>
      ) : null}
    </Stack>
  );
};

SampleSetting.propTypes = {
  control: PropTypes.object,
  inScope: PropTypes.number,
  disabled: PropTypes.bool,
  noun: PropTypes.oneOf(["row", "task"]),
  fixedText: PropTypes.string,
};

export default SampleSetting;
