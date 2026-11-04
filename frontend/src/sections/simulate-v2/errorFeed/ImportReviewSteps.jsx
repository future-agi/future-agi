import PropTypes from "prop-types";
import {
  Box,
  Button,
  Checkbox,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";

/*
  Error Feed Fix goes through the import review first (clarified plan,
  evidence.md): the issue's traces become reviewed tests before any fix is
  tried — private details removed, what should have happened stated, missing
  world facts filled, then saved and validated in the environment. Only then
  does the run reproduce the failure and the fix start.
*/
const reviewDone = (v) =>
  !!(v?.redacted && v?.expected?.trim() && v?.expectedOk && v?.facts);

export default function ImportReviewSteps({
  drafts,
  traceTotal,
  envName,
  value,
  onChange,
}) {
  const set = (k, x) => onChange({ ...value, [k]: x });
  const steps = [
    {
      id: "redacted",
      title: "Remove private details",
      done: !!value.redacted,
      body: (
        <>
          <Typography sx={{ typography: "s3", color: "text.secondary" }}>
            {`Names, phone numbers, emails and card numbers in the ${traceTotal} traces are replaced before they become scenario text — for example “Priya Shah” → [customer], “+1 415 555 0134” → [phone].`}
          </Typography>
          <Stack direction="row" alignItems="center" sx={{ ml: -0.75 }}>
            <Checkbox
              size="small"
              checked={!!value.redacted}
              onChange={(e) => set("redacted", e.target.checked)}
            />
            <Typography sx={{ typography: "s2" }}>
              I checked what&apos;s replaced
            </Typography>
          </Stack>
        </>
      ),
    },
    {
      id: "expected",
      title: "Say what should have happened",
      done: !!(value.expected?.trim() && value.expectedOk),
      body: (
        <>
          <TextField
            size="small"
            fullWidth
            multiline
            minRows={2}
            value={value.expected || ""}
            onChange={(e) => set("expected", e.target.value)}
            placeholder="Grounded in policy — e.g. check the existing refund before issuing another"
            helperText="An annotator's label is evidence, not the answer — this is the claim the tests check."
          />
          <Button
            size="small"
            variant="outlined"
            disabled={!value.expected?.trim()}
            onClick={() => set("expectedOk", true)}
            sx={{ mt: 0.75, color: "text.primary", borderColor: "divider" }}
          >
            Confirm
          </Button>
        </>
      ),
    },
    {
      id: "facts",
      title: "Fill missing world facts",
      done: !!value.facts,
      body: (
        <>
          <Typography sx={{ typography: "s3", color: "text.secondary" }}>
            The traces don&apos;t record the starting state — for example the
            order&apos;s age and its refund history. They&apos;re filled with
            labelled synthetic values; nothing claims to be an exact replay.
          </Typography>
          <Stack direction="row" alignItems="center" sx={{ ml: -0.75 }}>
            <Checkbox
              size="small"
              checked={!!value.facts}
              onChange={(e) => set("facts", e.target.checked)}
            />
            <Typography sx={{ typography: "s2" }}>
              Use the labelled synthetic facts
            </Typography>
          </Stack>
        </>
      ),
    },
    {
      id: "save",
      title: "Save and validate",
      done: reviewDone(value),
      body: (
        <Typography sx={{ typography: "s3", color: "text.secondary" }}>
          {`Saved into ${envName || "the environment"} as ${drafts.length} ${drafts.length === 1 ? "scenario" : "scenarios"} with a link to the issue, validated before the run. Then the run reproduces the failure, and only then is a fix tried.`}
        </Typography>
      ),
    },
  ];
  const next = steps.findIndex((s) => !s.done);
  return (
    <Box
      sx={{ border: "1px solid", borderColor: "divider", borderRadius: "8px" }}
    >
      <Typography
        sx={{ typography: "s2", fontWeight: 600, px: 1.5, pt: 1.25, pb: 0.5 }}
      >
        Turn the issue into tests first
      </Typography>
      {steps.map((s, i) => (
        <Box
          key={s.id}
          sx={{
            px: 1.5,
            py: 1,
            borderTop: i ? "1px solid" : 0,
            borderColor: "divider",
          }}
        >
          <Stack direction="row" spacing={1} alignItems="center">
            <Iconify
              icon={s.done ? "solar:check-circle-bold" : "solar:circle-linear"}
              width={16}
              sx={{
                color: s.done ? "status.success.fg" : "text.disabled",
                flexShrink: 0,
              }}
            />
            <Typography sx={{ typography: "s2", fontWeight: 500, flex: 1 }}>
              {`${i + 1}. ${s.title}`}
            </Typography>
            {s.done && i < 3 && next !== i && (
              <Button
                variant="outlined"
                size="small"
                onClick={() =>
                  set(s.id === "expected" ? "expectedOk" : s.id, false)
                }
                sx={{ minWidth: 0 }}
              >
                Change
              </Button>
            )}
          </Stack>
          {(!s.done || s.id === "save") && (i === next || s.id === "save") && (
            <Box sx={{ pl: 3, pt: 0.5 }}>{s.body}</Box>
          )}
        </Box>
      ))}
    </Box>
  );
}
ImportReviewSteps.propTypes = {
  drafts: PropTypes.array.isRequired,
  traceTotal: PropTypes.number,
  envName: PropTypes.string,
  value: PropTypes.object.isRequired,
  onChange: PropTypes.func.isRequired,
};
