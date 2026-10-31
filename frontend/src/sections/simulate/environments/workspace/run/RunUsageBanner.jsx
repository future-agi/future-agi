import { useState } from "react";
import { Link as RouterLink } from "react-router-dom";
import { Alert, AlertTitle, Box, IconButton, Link, Stack } from "@mui/material";
import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import {
  SAMPLE_USAGE_NOTICE,
  SAMPLE_USAGE_REFUSAL,
} from "src/api/simulate-environments/_fixtures/detail/usage";
import UiOnlyFlag from "../../components/UiOnlyFlag";
import useUiSample from "../../hooks/useUiSample";

// Vel's run pre-check, said at the top of the workspace: red when the next run
// would cross a usage limit, a quiet dismissible line past a budget threshold.
// We have no pre-run check yet (only the credit banner after a run fails), so
// it shows only with ?uiSample=usageheld or ?uiSample=usage.
export default function RunUsageBanner() {
  const held = useUiSample("usageheld");
  const warn = useUiSample("usage");
  const [hidden, setHidden] = useState(false);

  if (held)
    return (
      <Box sx={{ px: 3, pt: 1.5 }}>
        <Alert
          severity="error"
          icon={<Iconify icon="solar:forbidden-circle-linear" width={16} />}
          action={
            <Link
              component={RouterLink}
              to={paths.dashboard.settings.billing}
              underline="hover"
              color="text.primary"
              typography="s2"
              fontWeight={600}
            >
              Upgrade
            </Link>
          }
        >
          <AlertTitle>
            <Stack
              direction="row"
              alignItems="center"
              spacing={0.75}
              component="span"
            >
              <span>{`Run held: ${SAMPLE_USAGE_REFUSAL.title.charAt(0).toLowerCase()}${SAMPLE_USAGE_REFUSAL.title.slice(1)}`}</span>
              <UiOnlyFlag feature="runEstimate" />
            </Stack>
          </AlertTitle>
          {`${SAMPLE_USAGE_REFUSAL.line} Lower the repeats or the scenarios to run, or upgrade.`}
        </Alert>
      </Box>
    );

  if (warn && !hidden)
    return (
      <Box sx={{ px: 3, pt: 1.5 }}>
        <Alert
          severity="info"
          icon={<Iconify icon="solar:info-circle-linear" width={16} />}
          action={
            <Stack direction="row" alignItems="center" spacing={0.5}>
              <Link
                component={RouterLink}
                to={paths.dashboard.settings.usageSummary}
                underline="hover"
                color="text.secondary"
                typography="s2"
                fontWeight={600}
              >
                Usage
              </Link>
              <IconButton
                size="small"
                onClick={() => setHidden(true)}
                aria-label="Dismiss"
              >
                <Iconify icon="solar:close-linear" width={14} />
              </IconButton>
            </Stack>
          }
        >
          <Stack
            direction="row"
            alignItems="center"
            spacing={0.75}
            component="span"
          >
            <span>{SAMPLE_USAGE_NOTICE.line}</span>
            <UiOnlyFlag feature="runEstimate" />
          </Stack>
        </Alert>
      </Box>
    );

  return null;
}
