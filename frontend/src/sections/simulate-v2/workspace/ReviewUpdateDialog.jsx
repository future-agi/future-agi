import PropTypes from "prop-types";
import { Box, Button, Stack, Typography } from "@mui/material";
import { ConfirmDialog } from "src/components/custom-dialog";
import CopyCommandButton from "../components/CopyCommandButton";
import { envUpdateCli } from "../_mock/catalogue";

const AREA = { rules: "Rule", tools: "Tool", scenarios: "Scenarios" };

/* A titled, bordered list. */
function Group({ title, children }) {
  return (
    <Box>
      <Typography
        sx={{
          typography: "s3",
          fontWeight: 600,
          color: "text.tertiary",
          mb: 0.5,
        }}
      >
        {title}
      </Typography>
      <Box
        sx={{ border: "1px solid", borderColor: "divider", borderRadius: 1 }}
      >
        {children}
      </Box>
    </Box>
  );
}
Group.propTypes = { title: PropTypes.string, children: PropTypes.node };

const row = {
  px: 1.5,
  py: 0.75,
  "& + &": { borderTop: "1px solid", borderColor: "divider" },
};

/**
 * Review update (roadmap catalogue.md): the release's changes, each saying
 * whether it applies or is skipped because you edited what it touches; your
 * edits, kept; what's unchanged; and the scenarios that need re-checking.
 * Apply saves a new environment version — earlier runs stay pinned to the
 * version they ran on. Skip dismisses the release; it stays reviewable.
 */
export default function ReviewUpdateDialog({
  env,
  update,
  review,
  onClose,
  onApply,
  onSkip,
}) {
  if (!update) return null;
  const applies = review.items.filter((i) => i.status === "applies").length;
  return (
    <ConfirmDialog
      open
      onClose={onClose}
      PaperProps={{ sx: { maxWidth: 620 } }}
      title={`${update.entry.id} · release ${update.to}`}
      content={
        <Stack spacing={1.75}>
          <Typography sx={{ typography: "s2", color: "text.secondary" }}>
            {`You're on ${update.from}. ${update.notes}`}
          </Typography>
          <Group
            title={`From the release · ${applies} of ${review.items.length} apply`}
          >
            {review.items.map((i) => (
              <Stack key={i.text} direction="row" spacing={1.5} sx={row}>
                <Typography
                  sx={{
                    typography: "s3",
                    color: "text.tertiary",
                    width: 70,
                    flexShrink: 0,
                    pt: "2px",
                  }}
                >
                  {AREA[i.area] || i.area}
                </Typography>
                <Box flex={1} minWidth={0}>
                  <Typography sx={{ typography: "s2" }}>{i.text}</Typography>
                  {i.note && (
                    <Typography
                      sx={{ typography: "s3", color: "text.tertiary" }}
                    >
                      {i.note}
                    </Typography>
                  )}
                </Box>
                <Typography
                  sx={{
                    typography: "s3",
                    color:
                      i.status === "applies"
                        ? "text.secondary"
                        : "status.warning.fg",
                    flexShrink: 0,
                  }}
                >
                  {i.status === "applies" ? "Applies" : "Skipped"}
                </Typography>
              </Stack>
            ))}
          </Group>
          <Group title="Your edits · kept">
            {review.yourEdits.length ? (
              review.yourEdits.map((e) => (
                <Typography
                  key={e}
                  sx={{ ...row, typography: "s2", color: "text.secondary" }}
                >
                  {e}
                </Typography>
              ))
            ) : (
              <Typography
                sx={{ ...row, typography: "s2", color: "text.tertiary" }}
              >
                No edits since you made this environment.
              </Typography>
            )}
          </Group>
          <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
            {`Unchanged: ${review.unchanged.rules} rules and ${review.unchanged.tools} tools. ${
              review.recheck.length
                ? `${review.recheck.length} scenario${review.recheck.length === 1 ? "" : "s"} quote a changed rule and will be flagged to re-check.`
                : "No scenario quotes a changed rule."
            } Applying saves a new environment version; earlier runs stay on the version they ran.`}
          </Typography>
          <Box>
            <CopyCommandButton command={envUpdateCli(env, update.to)} />
          </Box>
        </Stack>
      }
      action={
        <Stack direction="row" spacing={1}>
          <Button
            size="small"
            onClick={onSkip}
            sx={{ color: "text.secondary" }}
          >
            Skip this release
          </Button>
          <Button
            size="small"
            variant="contained"
            color="primary"
            onClick={onApply}
          >
            Apply update
          </Button>
        </Stack>
      }
    />
  );
}
ReviewUpdateDialog.propTypes = {
  env: PropTypes.object.isRequired,
  update: PropTypes.object,
  review: PropTypes.object,
  onClose: PropTypes.func.isRequired,
  onApply: PropTypes.func.isRequired,
  onSkip: PropTypes.func.isRequired,
};
