import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Button, Collapse, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import OverflowTooltip from "src/components/tooltip/OverflowTooltip";
import { REVIEW_COPY as COPY } from "../workspace.constants";

// A long file name gives way before the reason does.
const FIX_MAX_WIDTH = 200;

/** A change that couldn't apply: why, what it holds up, and how to fix it. */
export default function FailedChange({ failed, canEdit, onFix, onDismiss }) {
  const fixText = failed.source_id ? COPY.fixIn(failed.source_id) : COPY.fix;
  return (
    <Stack
      direction="row"
      spacing={1}
      alignItems="flex-start"
      sx={{
        px: 1.5,
        py: 1,
        "& + &": { borderTop: "1px solid", borderColor: "divider" },
      }}
    >
      <Iconify
        icon="solar:danger-triangle-linear"
        width={16}
        sx={{ color: "status.warning.fg", flexShrink: 0, mt: "2px" }}
      />
      <Box flex={1} minWidth={0}>
        <Typography sx={{ typography: "s1" }}>{failed.detail}</Typography>
        <Typography sx={{ typography: "s3", color: "text.secondary" }}>
          {failed.reason}
        </Typography>
        <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
          {COPY.failedMeta(failed)}
        </Typography>
      </Box>
      {canEdit && (
        <>
          <Button
            size="small"
            variant="outlined"
            color="inherit"
            onClick={onFix}
            sx={{
              flexShrink: 0,
              maxWidth: FIX_MAX_WIDTH,
              whiteSpace: "nowrap",
            }}
          >
            <OverflowTooltip title={fixText}>
              <Box
                component="span"
                sx={{ overflow: "hidden", textOverflow: "ellipsis" }}
              >
                {fixText}
              </Box>
            </OverflowTooltip>
          </Button>
          <Button
            size="small"
            color="inherit"
            data-review-focus
            onClick={onDismiss}
            sx={{ flexShrink: 0, color: "text.secondary" }}
          >
            {COPY.dismiss}
          </Button>
        </>
      )}
    </Stack>
  );
}

/** Failed changes set aside, behind a count, each of which can come back. */
export function DismissedChanges({ dismissed, canEdit, onBringBack }) {
  const [shown, setShown] = useState(false);
  return (
    <Box>
      <Stack direction="row" alignItems="center" spacing={0.5}>
        <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
          {COPY.dismissedCount(dismissed.length)}
        </Typography>
        <Button
          size="small"
          color="inherit"
          aria-expanded={shown}
          onClick={() => setShown((now) => !now)}
          sx={{ color: "text.tertiary" }}
        >
          {shown ? COPY.hide : COPY.show}
        </Button>
      </Stack>
      <Collapse in={shown} unmountOnExit>
        <Stack spacing={0.5} sx={{ mt: 0.5 }}>
          {dismissed.map((one) => (
            <Stack key={one.id} direction="row" alignItems="center" spacing={1}>
              <OverflowTooltip title={one.detail}>
                <Typography
                  noWrap
                  sx={{
                    typography: "s3",
                    color: "text.tertiary",
                    flex: 1,
                    minWidth: 0,
                  }}
                >
                  {one.detail}
                </Typography>
              </OverflowTooltip>
              {canEdit && (
                <Button
                  size="small"
                  color="inherit"
                  onClick={() => onBringBack(one.id)}
                  sx={{ flexShrink: 0, color: "text.secondary" }}
                >
                  {COPY.bringBack}
                </Button>
              )}
            </Stack>
          ))}
        </Stack>
      </Collapse>
    </Box>
  );
}

DismissedChanges.propTypes = {
  dismissed: PropTypes.arrayOf(
    PropTypes.shape({ id: PropTypes.string, detail: PropTypes.string }),
  ).isRequired,
  canEdit: PropTypes.bool,
  onBringBack: PropTypes.func,
};

FailedChange.propTypes = {
  failed: PropTypes.shape({
    detail: PropTypes.string,
    reason: PropTypes.string,
    source_id: PropTypes.string,
    scenarios: PropTypes.arrayOf(PropTypes.string),
  }).isRequired,
  canEdit: PropTypes.bool,
  onFix: PropTypes.func,
  onDismiss: PropTypes.func,
};
