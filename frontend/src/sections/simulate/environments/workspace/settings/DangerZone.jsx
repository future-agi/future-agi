import PropTypes from "prop-types";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { enqueueSnackbar } from "notistack";
import { Box, Button, Stack, Typography } from "@mui/material";
import CustomTooltip from "src/components/tooltip";
import { paths } from "src/routes/paths";
import { useDeleteEnvironment } from "src/api/simulate-environments/environments";
import { errorMessage } from "src/pages/dashboard/harness/harnessShared";
import DeleteEnvironmentDialog from "../../components/DeleteEnvironmentDialog";
import { ENTRY_TAB } from "../../environmentOptions";
import SettingsCard from "./SettingsCard";

// The real delete, behind the same confirm the environments list uses. On
// success it returns to My Environments, as the header's Delete does.
export default function DangerZone({ env, name, canDelete = false }) {
  const navigate = useNavigate();
  const deleteEnv = useDeleteEnvironment();
  const [confirming, setConfirming] = useState(false);

  const onConfirm = () => {
    setConfirming(false);
    deleteEnv.mutate(env.id, {
      onSuccess: () =>
        navigate(
          `${paths.dashboard.simulate.environments.root}?tab=${ENTRY_TAB.MY}`,
        ),
      onError: (error) =>
        enqueueSnackbar(errorMessage(error), { variant: "error" }),
    });
  };

  return (
    <SettingsCard label="Danger zone" title="Danger zone">
      <Stack
        direction={{ xs: "column", sm: "row" }}
        alignItems={{ sm: "center" }}
        spacing={2}
        sx={{ p: 2.5 }}
      >
        <Box flex={1}>
          <Typography sx={{ typography: "s2", fontWeight: 600 }}>
            Delete this environment
          </Typography>
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
            Removes the environment and everything derived from it: scenarios,
            personas, evaluations and run history. This cannot be undone.
          </Typography>
        </Box>
        <CustomTooltip
          show={!canDelete}
          arrow
          size="small"
          title="Only a built environment can be deleted."
        >
          <span>
            <Button
              variant="outlined"
              size="small"
              color="error"
              disabled={!canDelete || deleteEnv.isPending}
              onClick={() => setConfirming(true)}
              sx={{ flexShrink: 0 }}
            >
              Delete environment
            </Button>
          </span>
        </CustomTooltip>
      </Stack>
      <DeleteEnvironmentDialog
        env={confirming ? { name } : null}
        onCancel={() => setConfirming(false)}
        onConfirm={onConfirm}
      />
    </SettingsCard>
  );
}
DangerZone.propTypes = {
  env: PropTypes.object.isRequired,
  name: PropTypes.string,
  canDelete: PropTypes.bool,
};
