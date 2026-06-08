import { Box, Button, Chip, Stack, Typography, useTheme } from "@mui/material";
import React from "react";
import SvgColor from "src/components/svg-color";
import SwitchComponent from "src/components/Switch/SwitchComponent";
import EvaluationDrawer from "src/sections/common/EvaluationDrawer/EvaluationDrawer";
import { useWorkbenchEvaluationContext } from "./context/WorkbenchEvaluationContext";
import AddEvalsComparison from "./AddEvalsComparison";
import { useNavigate, useParams } from "react-router";
import { useSearchParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { enqueueSnackbar } from "src/components/snackbar";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import {
  buildPromptEditorHref,
  getPromptOnboardingRouteParams,
  getSelectedPromptVersionsFromSearch,
  isPromptFailureCaptureOnboarding,
  PROMPT_ONBOARDING_MODES,
} from "../promptActions/promptOnboardingRoute";

const EvaluationActions = () => {
  const { role } = useAuthContext();
  const theme = useTheme();
  const { id } = useParams();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const queryClient = useQueryClient();
  const {
    versions,
    variables,
    showPrompts,
    showVariables,
    setShowPrompts,
    setShowVariables,
    setIsEvaluationDrawerOpen,
    isEvaluationDrawerOpen,
  } = useWorkbenchEvaluationContext();
  const handleCloseEvalsDrawer = () => {
    setIsEvaluationDrawerOpen(false);
  };
  const canUpdatePrompts = RolePermission.PROMPTS[PERMISSIONS.UPDATE][role];
  const promptOnboardingParams = getPromptOnboardingRouteParams(searchParams);
  const isFailureCaptureOnboarding = isPromptFailureCaptureOnboarding({
    mode: promptOnboardingParams.mode,
    source: promptOnboardingParams.isOnboarding
      ? "onboarding"
      : searchParams.get("source"),
  });
  const handleOpenEvaluationDrawer = () => setIsEvaluationDrawerOpen(true);
  const variableKeys = Object.keys(variables ?? {}).reduce((acc, curr) => {
    return [...acc, { headerName: curr, field: curr }];
  }, []);

  const columnOptions = [
    { headerName: "model_input", field: "input_prompt" },
    { headerName: "model_output", field: "output_prompt" },
    ...variableKeys,
  ];

  return (
    <>
      {isFailureCaptureOnboarding ? (
        <Box
          data-testid="prompt-failure-capture-focus"
          sx={{
            border: "1px solid",
            borderColor: "primary.main",
            borderRadius: 1,
            bgcolor: "background.paper",
            p: 1.5,
          }}
        >
          <Stack
            direction={{ xs: "column", md: "row" }}
            spacing={1.5}
            justifyContent="space-between"
            alignItems={{ xs: "flex-start", md: "center" }}
          >
            <Stack spacing={0.75} sx={{ minWidth: 0 }}>
              <Stack direction="row" spacing={0.75} flexWrap="wrap" useFlexGap>
                <Chip size="small" label="Prompt setup" />
                <Chip size="small" variant="outlined" label="Evaluation" />
                <Chip size="small" variant="outlined" label="Step 6 of 6" />
              </Stack>
              <Box>
                <Typography variant="subtitle2">
                  Capture the prompt failure
                </Typography>
                <Typography variant="body2" color="text.secondary">
                  Add an evaluation that checks the comparison issue, then run
                  it on the saved versions.
                </Typography>
              </Box>
              <Stack direction="row" spacing={0.75} flexWrap="wrap" useFlexGap>
                <Chip size="small" variant="outlined" label="Issue" />
                <Chip size="small" variant="outlined" label="Evaluation" />
                <Chip size="small" label="Next loop" />
              </Stack>
            </Stack>
            <Button
              variant="contained"
              disabled={!canUpdatePrompts}
              onClick={handleOpenEvaluationDrawer}
              startIcon={
                <SvgColor
                  src="/assets/icons/action_buttons/ic_add.svg"
                  sx={{
                    height: theme.spacing(2),
                    width: theme.spacing(2),
                  }}
                />
              }
              sx={{ flexShrink: 0 }}
            >
              Add Evaluation
            </Button>
          </Stack>
        </Box>
      ) : null}
      <Box display={"flex"} justifyContent={"space-between"}>
        <Box display={"flex"} gap={theme.spacing(2)} alignItems={"center"}>
          <Box
            border={"1px solid"}
            borderColor={"divider"}
            px={theme.spacing(1.5)}
            py={theme.spacing(0.25)}
            borderRadius={theme.spacing(0.5)}
          >
            <SwitchComponent
              label="Show Prompts"
              labelPlacement={"start"}
              labelStyle={{
                fontSize: theme.spacing(1.5),
              }}
              size={"small"}
              checked={showPrompts}
              disableRipple
              onChange={(e) => setShowPrompts(e.target.checked)}
            />
          </Box>
          <Box
            border={"1px solid"}
            borderColor={"divider"}
            px={theme.spacing(1.5)}
            py={theme.spacing(0.25)}
            borderRadius={theme.spacing(0.5)}
          >
            <SwitchComponent
              label="Show Variables"
              labelPlacement={"start"}
              labelStyle={{
                fontSize: theme.spacing(1.5),
              }}
              size={"small"}
              checked={showVariables}
              disableRipple
              onChange={(e) => setShowVariables(e.target.checked)}
            />
          </Box>
        </Box>
        <Button
          onClick={handleOpenEvaluationDrawer}
          variant="outlined"
          color="primary"
          disabled={!canUpdatePrompts}
          startIcon={
            <SvgColor
              src="/assets/icons/action_buttons/ic_add.svg"
              color="primary.main"
              sx={{
                height: theme.spacing(2),
                width: theme.spacing(2),
              }}
            />
          }
        >
          <Typography typography={"s2"} fontWeight={"600"}>
            Add Evaluations
          </Typography>
        </Button>
        <AddEvalsComparison />
      </Box>
      <EvaluationDrawer
        open={isEvaluationDrawerOpen}
        onClose={handleCloseEvalsDrawer}
        allColumns={columnOptions}
        showAdd={true}
        testLabel="Cancel"
        module="workbench"
        onSuccess={() => {
          queryClient.invalidateQueries({
            queryKey: ["workbench", "user-eval-list", id],
          });
          queryClient.invalidateQueries({
            queryKey: [
              "evaluations-workbench",
              showPrompts,
              showVariables,
              id,
              versions,
            ],
          });
          enqueueSnackbar({
            message: "Evaluation added",
          });
          if (isFailureCaptureOnboarding) {
            navigate(
              buildPromptEditorHref({
                promptId: id,
                mode: PROMPT_ONBOARDING_MODES.METRICS,
                search: searchParams,
                selectedVersions:
                  getSelectedPromptVersionsFromSearch(searchParams),
              }),
              { replace: true },
            );
          }
        }}
        id={id}
        refreshGrid={() => {
          queryClient.invalidateQueries({
            queryKey: [
              "evaluations-workbench",
              showPrompts,
              showVariables,
              id,
              versions,
            ],
          });
        }}
        SetIsSelectedEval={() => {}}
      />
    </>
  );
};

export default EvaluationActions;
