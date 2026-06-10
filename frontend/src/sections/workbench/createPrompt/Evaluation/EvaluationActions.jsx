import { Box, Button, Typography, useTheme } from "@mui/material";
import React from "react";
import SvgColor from "src/components/svg-color";
import SwitchComponent from "src/components/Switch/SwitchComponent";
import EvaluationDrawer from "src/sections/common/EvaluationDrawer/EvaluationDrawer";
import { useWorkbenchEvaluationContext } from "./context/WorkbenchEvaluationContext";
import AddEvalsComparison from "./AddEvalsComparison";
import { useParams } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import { enqueueSnackbar } from "src/components/snackbar";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";

const EvaluationActions = () => {
  const { role } = useAuthContext();
  const theme = useTheme();
  const { id } = useParams();
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
          data-testid="workbench-add-evaluations-button"
          onClick={() => setIsEvaluationDrawerOpen(true)}
          variant="outlined"
          color="primary"
          disabled={!RolePermission.PROMPTS[PERMISSIONS.UPDATE][role]}
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
