import PropTypes from "prop-types";
import { Box, Button, Link, Stack, Tooltip, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import StatusBadge from "src/components/status-badge/StatusBadge";

const linkSx = {
  typography: "s3",
  fontWeight: 600,
  color: "text.primary",
  verticalAlign: "baseline",
};

/**
 * How an epoch ended when it ended early — stopped, no safe change, not
 * reproduced, too few failing, over its budget. One layout for all of them,
 * read the way the issue page's strip reads a stopped epoch: the status word
 * (neutral; red only when a must-pass scenario broke), one sentence on what
 * happened, what it spent, one way on and at most two links.
 */
export default function EpochEnding({
  label,
  danger = false,
  sentence,
  spent,
  primary,
  links = [],
  /* The second line on hover instead (an info mark after the sentence),
     for a status box whose detail isn't needed at a glance. */
  spentOnHover = false,
  children,
}) {
  const shown = links.filter(Boolean).slice(0, 2);
  return (
    <Box
      sx={{
        mb: 2,
        flexShrink: 0,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "8px",
        bgcolor: "background.paper",
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        gap={1.5}
        sx={{ px: 2, py: 1.25 }}
      >
        <Box sx={{ flexShrink: 0, alignSelf: "flex-start", pt: "1px" }}>
          <StatusBadge
            status={danger ? "failed" : "stopped"}
            role={danger ? "danger" : "neutral"}
            variant="badge"
            label={label}
          />
        </Box>
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Typography sx={{ typography: "s2", color: "text.primary" }}>
            {sentence}
            {spentOnHover && spent && (
              <Tooltip arrow title={spent}>
                <Box
                  component="span"
                  sx={{
                    display: "inline-flex",
                    verticalAlign: "-2px",
                    ml: 0.75,
                  }}
                >
                  <Iconify
                    icon="solar:info-circle-linear"
                    width={14}
                    sx={{ color: "text.tertiary" }}
                  />
                </Box>
              </Tooltip>
            )}
          </Typography>
          {((spent && !spentOnHover) || shown.length > 0) && (
            <Typography
              component="div"
              sx={{ typography: "s3", color: "text.tertiary", mt: 0.25 }}
            >
              {!spentOnHover && spent}
              {shown.map((l, i) => (
                <Box component="span" key={l.label}>
                  {(spent && !spentOnHover) || i > 0 ? " · " : ""}
                  <Link
                    component="button"
                    type="button"
                    underline="hover"
                    onClick={l.onClick}
                    sx={linkSx}
                  >
                    {l.label}
                  </Link>
                </Box>
              ))}
            </Typography>
          )}
        </Box>
        {primary && (
          <Button
            size="small"
            variant="contained"
            color="primary"
            onClick={primary.onClick}
            disabled={!primary.onClick}
            sx={{ flexShrink: 0 }}
          >
            {primary.label}
          </Button>
        )}
      </Stack>
      {children && (
        <Box sx={{ borderTop: "1px solid", borderColor: "divider" }}>
          {children}
        </Box>
      )}
    </Box>
  );
}

EpochEnding.propTypes = {
  /* "Epoch 5 · Stopped" — the badge. */
  label: PropTypes.string.isRequired,
  /* Red only when a must-pass scenario broke. */
  danger: PropTypes.bool,
  sentence: PropTypes.node.isRequired,
  /* "Spent 1,234 credits before it stopped." */
  spent: PropTypes.string,
  spentOnHover: PropTypes.bool,
  primary: PropTypes.shape({
    label: PropTypes.string,
    onClick: PropTypes.func,
  }),
  links: PropTypes.arrayOf(
    PropTypes.shape({ label: PropTypes.string, onClick: PropTypes.func }),
  ),
  /* An inline control the ending needs (raise the budget and continue). */
  children: PropTypes.node,
};
