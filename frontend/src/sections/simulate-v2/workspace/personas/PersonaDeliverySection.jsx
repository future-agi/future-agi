import PropTypes from "prop-types";
import { useFormContext } from "react-hook-form";
import { alpha } from "@mui/material/styles";
import {
  Box, Stack, Typography, Slider, Switch, Chip, Tooltip,
} from "@mui/material";
import { deliveryControls, fidelitySample } from "../../_mock/fidelity";
import { effectiveModality } from "../../_mock/rlContract";

/**
 * How this persona comes through.
 *
 * The old Fidelity page owned this — noise, barge-in, typos, DOM churn —
 * "applied to every scenario in this environment." Which meant one person's
 * knob turned quietly changed every scenario's numbers, and any two personas
 * necessarily had the same delivery. That was a description of the *line*, and
 * the line is a property of the individual caller, not the environment. So it
 * moves here.
 *
 * Two consequences worth noting inline. First, the identity fields (accent,
 * demographics, tone) that Fidelity duplicated are already above on the
 * persona; they do not appear again. Second, everything below stays as the
 * modality dictates — a voice persona gets line conditions and barge-in, a
 * chat persona gets typos and drift — so we do not offer a knob that could
 * never apply.
 */

export default function PersonaDeliverySection({ env, envState }) {
  const { watch, setValue } = useFormContext();
  const modality = effectiveModality(env, envState);
  const groups = deliveryControls(env);

  if (!groups.length) return null;

  const delivery = watch("delivery") || {};
  const set = (id, v) => setValue("delivery", { ...delivery, [id]: v }, { shouldDirty: true });

  return (
    <Box
      sx={{
        p: 2, borderRadius: 1, border: "1px solid", borderColor: "divider",
        bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.02 : 0.01),
      }}
    >
      <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 0.5 }}>
        <Typography sx={{ typography: "s1", fontWeight: 700 }}>How they come through</Typography>
      </Stack>
      <Typography sx={{ typography: "s3", color: "text.subtitle", mb: 2 }}>
        The line, not the person. Recorded with every run, so a score that moves is the agent — not
        someone quietly turning a knob.
      </Typography>

      <Stack spacing={3}>
        {groups.map((g) => (
          <Box key={g.title}>
            <Typography sx={{ typography: "s2", fontWeight: 700, color: "text.secondary", mb: 1.25 }}>
              {g.title}
            </Typography>
            <Stack spacing={2}>
              {g.controls.map((c) => (
                <Control key={c.id} control={c} value={delivery[c.id] ?? c.def} onChange={(v) => set(c.id, v)} />
              ))}
            </Stack>
          </Box>
        ))}

        {/* One line of sample output — a knob that only shows a percentage
            reads like a mystery until you see what it produces. */}
        <Box
          sx={{
            px: 1.75, py: 1.25, borderRadius: 1,
            bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.04 : 0.02),
          }}
        >
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>What this produces</Typography>
          <Typography sx={{ typography: "s2", color: "text.secondary", fontStyle: "italic", mt: 0.375 }}>
            {fidelitySample(modality, delivery)}
          </Typography>
        </Box>
      </Stack>
    </Box>
  );
}

PersonaDeliverySection.propTypes = { env: PropTypes.object, envState: PropTypes.object };

function Control({ control, value, onChange }) {
  if (control.kind === "slider") {
    return (
      <Box>
        <Stack direction="row" alignItems="baseline" justifyContent="space-between" sx={{ mb: 0.5 }}>
          <Box>
            <Typography sx={{ typography: "s2", fontWeight: 600 }}>{control.label}</Typography>
            {control.help && (
              <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{control.help}</Typography>
            )}
          </Box>
          <Typography sx={{ typography: "s2", fontVariantNumeric: "tabular-nums", color: "text.secondary" }}>
            {value}%
          </Typography>
        </Stack>
        <Slider
          value={value}
          onChange={(_, v) => onChange(v)}
          size="small"
          sx={{ mt: 0.5 }}
        />
      </Box>
    );
  }

  if (control.kind === "toggle") {
    return (
      <Stack direction="row" alignItems="center" spacing={1.25}>
        <Box flex={1}>
          <Typography sx={{ typography: "s2", fontWeight: 600 }}>{control.label}</Typography>
          {control.help && (
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{control.help}</Typography>
          )}
        </Box>
        <Switch checked={!!value} onChange={(e) => onChange(e.target.checked)} />
      </Stack>
    );
  }

  if (control.kind === "chips") {
    const selected = Array.isArray(value) ? value : [];
    const toggle = (opt) => {
      const on = selected.includes(opt);
      onChange(on ? selected.filter((x) => x !== opt) : [...selected, opt]);
    };
    return (
      <Box>
        <Typography sx={{ typography: "s2", fontWeight: 600 }}>{control.label}</Typography>
        {control.help && (
          <Typography sx={{ typography: "s3", color: "text.subtitle", mb: 0.75 }}>{control.help}</Typography>
        )}
        <Stack direction="row" spacing={0.75} flexWrap="wrap" rowGap={0.75}>
          {control.options.map((opt) => {
            const on = selected.includes(opt);
            return (
              <Tooltip key={opt} arrow title={on ? "Included in the pool" : "Add to the pool"}>
                <Chip
                  label={opt}
                  onClick={() => toggle(opt)}
                  sx={{
                    height: 26, borderRadius: 0.75,
                    border: "1px solid",
                    borderColor: on ? "text.primary" : "divider",
                    bgcolor: (t) => (on ? alpha(t.palette.text.primary, 0.08) : "transparent"),
                    color: on ? "text.primary" : "text.secondary",
                    "&:hover": { bgcolor: (t) => alpha(t.palette.text.primary, 0.06) },
                    "& .MuiChip-label": { px: 1, typography: "s2", fontWeight: 600 },
                  }}
                />
              </Tooltip>
            );
          })}
        </Stack>
      </Box>
    );
  }

  return null;
}

Control.propTypes = { control: PropTypes.object, value: PropTypes.any, onChange: PropTypes.func };
