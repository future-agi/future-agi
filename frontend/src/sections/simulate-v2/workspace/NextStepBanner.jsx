import PropTypes from "prop-types";
import { Box, Button, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";

/*
  What stands between this environment and its first run, said where the
  person is — not only in the disabled Run button's tooltip. The design
  system's banner: one sentence (a title, then what to do), one action that
  does it here, staying until the condition is resolved.
*/
export default function NextStepBanner({
  title,
  line,
  actionLabel,
  onAction,
  secondaryLabel,
  onSecondary,
  tone = "warning",
}) {
  if (!title) return null;
  return (
    <Stack
      direction="row"
      alignItems="flex-start"
      gap={1.25}
      role="status"
      sx={{
        px: 1.5,
        py: 1.25,
        borderRadius: "8px",
        border: "1px solid",
        borderColor: `status.${tone}.border`,
        bgcolor: `status.${tone}.bg`,
      }}
    >
      <Iconify
        icon={
          tone === "info"
            ? "solar:info-circle-linear"
            : "solar:danger-triangle-linear"
        }
        width={16}
        sx={{ color: `status.${tone}.fg`, mt: 0.25, flexShrink: 0 }}
      />
      <Box sx={{ flex: 1, minWidth: 0 }}>
        <Typography
          component="span"
          sx={{ typography: "s2", fontWeight: 500, color: "text.primary" }}
        >
          {title}
        </Typography>
        {line && (
          <Typography
            component="span"
            sx={{ typography: "s2", color: "text.secondary" }}
          >
            {` ${line}`}
          </Typography>
        )}
      </Box>
      {/* A way out that isn't the fix itself, e.g. Run sealed instead. */}
      {secondaryLabel && onSecondary && (
        <Button
          size="small"
          onClick={onSecondary}
          sx={{ flexShrink: 0, color: "text.secondary", whiteSpace: "nowrap" }}
        >
          {secondaryLabel}
        </Button>
      )}
      {actionLabel && onAction && (
        <Button
          size="small"
          variant="outlined"
          onClick={onAction}
          sx={{
            flexShrink: 0,
            color: "text.primary",
            borderColor: "divider",
            whiteSpace: "nowrap",
          }}
        >
          {actionLabel}
        </Button>
      )}
    </Stack>
  );
}

NextStepBanner.propTypes = {
  title: PropTypes.string,
  line: PropTypes.string,
  actionLabel: PropTypes.string,
  onAction: PropTypes.func,
  secondaryLabel: PropTypes.string,
  onSecondary: PropTypes.func,
  /* warning: blocks the run · info: worth doing, doesn't block. */
  tone: PropTypes.oneOf(["warning", "info"]),
};
