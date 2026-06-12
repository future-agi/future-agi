import React, { useState } from "react";
import {
  Alert,
  Box,
  Button,
  Divider,
  Link,
  Typography,
  useTheme,
} from "@mui/material";
import EditAgentDetails from "./EditAgentDetails";
import { handleOnDocsClicked } from "src/utils/Mixpanel";
import { useAgentConfigForm, useAgentSubmit } from "../useAgentConfigForm";
import { useQueryClient } from "@tanstack/react-query";
import { useParams } from "react-router";
import { createAgentDefinitionSchema } from "../helper";
import { useSnackbar } from "notistack";
import { LoadingButton } from "@mui/lab";
import { useAgentDetailsStore } from "../store/agentDetailsStore";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
// import SaveNewAgentVersionModal from "./SaveNewAgentVersionModal";

const AgentConfigurationView = () => {
  const { agentDefinitionId } = useParams();
  const { agentDetails, setSelectedVersion } = useAgentDetailsStore();
  const { enqueueSnackbar } = useSnackbar();
  const queryClient = useQueryClient();
  const theme = useTheme();
  // const [open, setOpen] = useState(false);
  const {
    control,
    handleSubmit,
    reset,
    formState,
    setValue,
    getValues,
    trigger,
  } = useAgentConfigForm(createAgentDefinitionSchema(), agentDetails);

  const { errors, isSubmitting, isDirty } = formState;
  const [error, setError] = useState("");

  const { onSubmit } = useAgentSubmit({
    agentDefinitionId,
    reset,
    queryClient,
    enqueueSnackbar,
    setError,
    onClose: null,
    setSelectedVersion,
  });

  return (
    <Box
      sx={{
        display: "flex",
        justifyContent: "center",
        p: 1,
        boxSizing: "border-box",
        height: "100%",
        minHeight: 0,
      }}
    >
      <Box
        sx={{
          width: "100%",
          maxWidth: "52vw",
          bgcolor: "background.paper",
          p: 2.5,
          borderRadius: "4px",
          boxShadow: theme.shadows[6],
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
        }}
      >
        <Box
          sx={{
            flexShrink: 0,
          }}
        >
          <Box
            display="flex"
            justifyContent="space-between"
            alignItems="center"
            mb={2}
          >
            <Box sx={{ display: "flex", flexDirection: "column", gap: "2px" }}>
              <Typography
                typography="s1"
                fontWeight="fontWeightMedium"
                color="text.primary"
              >
                Current Configuration
              </Typography>
              <Typography
                typography="s2_1"
                color="text.secondary"
                fontWeight={"fontWeightRegular"}
              >
                A configuration that specifies how your AI agent behaves during
                voice/chat conversations{" "}
                <Link
                  onClick={() => handleOnDocsClicked("agent_definition_page")}
                  href="https://docs.futureagi.com/docs/simulation/concepts/agent-definition"
                  color="blue.500"
                  target="_blank"
                  rel="noopener noreferrer"
                  fontWeight="fontWeightMedium"
                  fontSize="14px"
                  sx={{ textDecoration: "underline", fontSize: "13px" }}
                >
                  Learn more
                </Link>{" "}
              </Typography>
            </Box>
            {/* Right side buttons */}
            {isDirty && (
              <Box display="flex" gap={1}>
                <Button
                  variant="outlined"
                  size="small"
                  onClick={() => reset()}
                  sx={{ borderRadius: "4px", height: "35px", width: "70px" }}
                >
                  Cancel
                </Button>
                <LoadingButton
                  variant="contained"
                  size="small"
                  color="primary"
                  onClick={handleSubmit(onSubmit)}
                  // onClick={() => setOpen(true)}
                  loading={isSubmitting}
                  // disabled={!isValid}
                  sx={{ borderRadius: "4px", height: "35px" }}
                >
                  Save
                </LoadingButton>
              </Box>
            )}
          </Box>
          <Divider sx={{ borderColor: "divider" }} />
        </Box>
        <Box sx={{ flex: 1, overflowY: "auto", minHeight: 0, pt: 3 }}>
          {error && (
            <Alert severity="error" sx={{ mb: 2 }}>
              {error}
            </Alert>
          )}

          <Box display="flex" flexDirection="column" gap={2}>
            <EditAgentDetails
              control={control}
              errors={errors}
              trigger={trigger}
              setValue={setValue}
              getValues={getValues}
            />
            <Box sx={{ display: "flex", flexDirection: "column" }}>
              <FormTextFieldV2
                label="Commit Message"
                required
                control={control}
                fieldName="commitMessage"
                placeholder="Describe your changes"
                size="small"
                fullWidth
                sx={{
                  "& .MuiInputLabel-root": {
                    fontWeight: 500,
                  },
                }}
              />
            </Box>
          </Box>
        </Box>
      </Box>
      {/* <SaveNewAgentVersionModal
        open={open}
        onClose={() => setOpen(false)}
        control={control}
        handleSubmit={handleSubmit}
        onSubmit={onSubmit}
        isSubmitting={isSubmitting}
      /> */}
    </Box>
  );
};

export default AgentConfigurationView;
