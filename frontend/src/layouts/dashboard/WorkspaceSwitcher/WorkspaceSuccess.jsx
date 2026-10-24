import {
  Box,
  Button,
  Divider,
  IconButton,
  Stack,
  Typography,
} from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import SvgColor from "src/components/svg-color";
import AddedUsers from "./AddedUsers";
import FailedUsersComponent from "./FailedUsersComponent";
import { ShowComponent } from "src/components/show";
import { enqueueSnackbar } from "notistack";
import { Events, trackEvent } from "src/utils/Mixpanel";
import { useWorkspace } from "src/contexts/WorkspaceContext";
import { LoadingButton } from "@mui/lab";

const WorkspaceSuccess = ({ workspaceData, onClose }) => {
  const addedUsers = workspaceData?.addedUsers || [];
  const allFailedUsers = [
    ...(workspaceData?.failedUsers || []),
    ...(workspaceData?.otherOrgUsers || []),
  ];
  const { currentWorkspaceId, switchWorkspace: switchWs } = useWorkspace();

  const [isLoading, setIsLoading] = React.useState(false);
  const handleSwitchWorkspace = async (newWorkspaceId) => {
    setIsLoading(true);
    try {
      await switchWs(newWorkspaceId);
      trackEvent(Events.workspaceNewWorkspaceSelected, {
        workspaces: { oldWorkSpaceId: currentWorkspaceId, newWorkspaceId },
      });
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <Box sx={{ p: 1 }}>
      <Box
        sx={{
          display: "flex",
          flexDirection: "row",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        <Box
          sx={{
            display: "flex",
            flexDirection: "row",
            gap: 1,
            alignItems: "center",
          }}
        >
          <Box
            sx={{
              width: 16,
              height: 16,
              borderRadius: "50%",
              bgcolor: "status.success.solid",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
            }}
          >
            <SvgColor
              sx={{ height: 14, width: 14, color: "common.white" }}
              src={"/assets/icons/ic_tick.svg"}
            />
          </Box>
          <Typography typography={"m3"} fontWeight={"fontWeightBold"}>
            Workspace created successfully
          </Typography>
        </Box>
        <IconButton
          sx={{
            color: "text.primary",
          }}
          size="small"
          onClick={onClose}
        >
          <SvgColor
            sx={{
              height: "24px",
              width: "24px",
            }}
            src="/assets/icons/ic_close.svg"
          />
        </IconButton>
      </Box>
      <Divider sx={{ my: 1 }} />
      <Stack spacing={1}>
        <Typography
          typography={"s2_1"}
          fontWeight={"fontWeightSemiBold"}
          sx={{
            padding: 1,
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "4px",
          }}
        >
          Workspace Name: {workspaceData?.workspace?.name}
        </Typography>

        <ShowComponent condition={addedUsers.length > 0}>
          <AddedUsers addedUsers={addedUsers} />
        </ShowComponent>
        <ShowComponent condition={allFailedUsers.length > 0}>
          <FailedUsersComponent failedUsers={allFailedUsers} />
        </ShowComponent>

        <Box
          sx={{
            display: "flex",
            justifyContent: "flex-end",
            gap: 2,
            marginTop: 1,
          }}
        >
          <Button variant="outlined" size="medium" onClick={onClose}>
            Stay here
          </Button>
          <LoadingButton
            loading={isLoading}
            variant="contained"
            size="medium"
            color="primary"
            onClick={() => {
              if (!workspaceData?.workspace?.id) {
                enqueueSnackbar("something went wrong, please try again", {
                  variant: "error",
                });
                return;
              }
              handleSwitchWorkspace(workspaceData?.workspace?.id);
            }}
          >
            Go to workspace
          </LoadingButton>
        </Box>
      </Stack>
    </Box>
  );
};

export default WorkspaceSuccess;

WorkspaceSuccess.propTypes = {
  workspaceData: PropTypes.object,
  onClose: PropTypes.func,
};
