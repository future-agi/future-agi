import PropTypes from "prop-types";
import { useNavigate } from "react-router-dom";
import { Button } from "@mui/material";
import { enqueueSnackbar } from "notistack";

import Iconify from "src/components/iconify";
import { paths } from "src/routes/paths";
import {
  fixErrorOf,
  useRetryFixRun,
} from "src/api/simulate-environments/fixRuns";

// A failed fix the backend allows to start again: the new fix takes the same
// request and reuses the check runs this one finished, then opens.
export default function RetryButton({ fix }) {
  const navigate = useNavigate();
  const retry = useRetryFixRun(fix);
  if (fix.state !== "failed" || !fix.retryable) return null;
  return (
    <Button
      size="small"
      variant="contained"
      color="primary"
      disabled={retry.isPending}
      startIcon={<Iconify icon="solar:refresh-linear" width={16} />}
      onClick={() =>
        retry.mutate(undefined, {
          onSuccess: (next) =>
            navigate(
              paths.dashboard.simulate.environments.improvement(
                fix.environment_id,
                next.id,
              ),
            ),
          onError: (e) =>
            enqueueSnackbar(
              fixErrorOf(e).message || "Could not retry the improvement",
              { variant: "error" },
            ),
        })
      }
    >
      Retry
    </Button>
  );
}

RetryButton.propTypes = { fix: PropTypes.object.isRequired };
