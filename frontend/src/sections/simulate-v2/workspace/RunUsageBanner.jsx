import PropTypes from "prop-types";
import { useState } from "react";
import { Alert, AlertTitle, Box, IconButton, Link, Stack } from "@mui/material";
import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";

/*
  The run pre-check, said at the top of the workspace (AC-16.7, AC-16.8).

  Refused: the next run would cross a usage limit. Persistent, and the one
  place it earns red — the standard limit message (dimension, current use,
  limit) and the way out: fewer repeats or scenarios, or an upgrade.

  Warn: a budget threshold. A quiet neutral line; the run still goes, and
  the notice can be put away for the session.

  `refusal` = metering.limitMessage(...), `notice` = metering.budgetNotice(...).
*/
export default function RunUsageBanner({ refusal, notice }) {
  const [hidden, setHidden] = useState(null);
  if (refusal) {
    return (
      <Box sx={{ px: 3, pt: 1.5 }}>
        <Alert
          severity="error"
          icon={<Iconify icon="solar:forbidden-circle-linear" width={16} />}
          action={
            <Link
              href={paths.dashboard.settings.billing}
              underline="hover"
              color="text.primary"
              typography="s2"
              fontWeight={600}
            >
              Upgrade
            </Link>
          }
        >
          <AlertTitle>{`Run held — ${refusal.title.charAt(0).toLowerCase()}${refusal.title.slice(1)}`}</AlertTitle>
          {`${refusal.line} Lower the repeats or the scenarios to run, or upgrade.`}
        </Alert>
      </Box>
    );
  }
  if (notice && hidden !== notice.line) {
    return (
      <Box sx={{ px: 3, pt: 1.5 }}>
        <Alert
          severity="info"
          icon={<Iconify icon="solar:info-circle-linear" width={16} />}
          action={
            <Stack direction="row" alignItems="center" spacing={0.5}>
              <Link
                href={paths.dashboard.settings.usageSummary}
                underline="hover"
                color="text.secondary"
                typography="s2"
                fontWeight={600}
              >
                Usage
              </Link>
              <IconButton
                size="small"
                onClick={() => setHidden(notice.line)}
                aria-label="Dismiss"
              >
                <Iconify icon="solar:close-linear" width={14} />
              </IconButton>
            </Stack>
          }
        >
          {notice.line}
        </Alert>
      </Box>
    );
  }
  return null;
}
RunUsageBanner.propTypes = {
  refusal: PropTypes.object,
  notice: PropTypes.object,
};
