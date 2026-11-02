import PropTypes from "prop-types";
import { Box, Button, IconButton, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { OUTLINED_BTN_SX, ROW_BOX_SX, T12 } from "../../drawerStyles";
import { Field } from "../formBits";

export const INITIAL_STEPS = [
  { id: "s1", name: "Greet and identify", instructions: "", next: "" },
  { id: "s2", name: "", instructions: "", next: "" },
];

/** Every step named and saying what it does, two at least. */
export const stepsComplete = (steps) =>
  steps.length >= 2 &&
  steps.every((st) => st.name.trim() && st.instructions.trim());

/**
 * Instructions written as steps, the way Bland, Retell and Vapi workflows
 * are: each its own name, instructions and where it goes next. Two at least.
 */
export default function PhoneSteps({ steps, onChange }) {
  const edit = (id, patch) =>
    onChange(steps.map((st) => (st.id === id ? { ...st, ...patch } : st)));
  const add = () => {
    const last = Math.max(0, ...steps.map((st) => Number(st.id.slice(1))));
    onChange([
      ...steps,
      { id: `s${last + 1}`, name: "", instructions: "", next: "" },
    ]);
  };

  return (
    <Stack spacing={1}>
      {steps.map((st, i) => {
        const n = i + 1;
        return (
          <Box key={st.id} sx={{ ...ROW_BOX_SX, p: 1.25 }}>
            <Stack
              direction="row"
              alignItems="center"
              spacing={1}
              sx={{ mb: 1 }}
            >
              <Typography
                sx={{
                  ...T12,
                  fontWeight: 600,
                  color: "text.secondary",
                  flexShrink: 0,
                }}
              >{`Step ${n}`}</Typography>
              <Field
                value={st.name}
                onChange={(v) => edit(st.id, { name: v })}
                placeholder={
                  i === 0 ? "Greet and identify" : "e.g. Book the appointment"
                }
                inputProps={{ "aria-label": `Step ${n} name` }}
                sx={{ flex: 1 }}
              />
              {steps.length > 2 && (
                <IconButton
                  size="small"
                  aria-label={`Remove step ${n}`}
                  onClick={() => onChange(steps.filter((x) => x.id !== st.id))}
                  sx={{ p: 0.5 }}
                >
                  <Iconify
                    icon="solar:trash-bin-minimalistic-linear"
                    width={16}
                  />
                </IconButton>
              )}
            </Stack>
            <Field
              multiline
              minRows={2}
              value={st.instructions}
              onChange={(v) => edit(st.id, { instructions: v })}
              placeholder="What the agent says and does in this step."
              inputProps={{ "aria-label": `Step ${n} instructions` }}
            />
            <Field
              value={st.next}
              onChange={(v) => edit(st.id, { next: v })}
              placeholder={
                i === steps.length - 1
                  ? "Then, e.g. end the call"
                  : `Then, e.g. go to step ${n + 1} once the caller is verified; otherwise transfer`
              }
              inputProps={{ "aria-label": `Step ${n} then` }}
              sx={{ mt: 1 }}
            />
          </Box>
        );
      })}
      <Box>
        <Button
          variant="outlined"
          startIcon={<Iconify icon="solar:add-circle-linear" width={14} />}
          onClick={add}
          sx={OUTLINED_BTN_SX}
        >
          Add step
        </Button>
      </Box>
      <Typography sx={{ ...T12, color: "text.secondary" }}>
        Each step becomes one of the agent&apos;s members, so a suggested fix
        names the step it changes.
      </Typography>
    </Stack>
  );
}
PhoneSteps.propTypes = {
  steps: PropTypes.arrayOf(
    PropTypes.shape({
      id: PropTypes.string,
      name: PropTypes.string,
      instructions: PropTypes.string,
      next: PropTypes.string,
    }),
  ).isRequired,
  onChange: PropTypes.func.isRequired,
};
