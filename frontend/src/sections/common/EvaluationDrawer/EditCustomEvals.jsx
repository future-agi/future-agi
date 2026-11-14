import { Box, Button, Drawer, IconButton, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React, { useMemo, useState } from "react";
import { ShowComponent } from "src/components/show";
import CustomEvalsForm from "./CustomEvalsForm";
import { useEvaluationContext } from "./context/EvaluationContext";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { FUTUREAGI_LLM_MODELS } from "./validation";
import Iconify from "src/components/iconify";
import CustomEvalsLoading from "src/sections/evals/EachEvalsView/CustomEvalsLoading";
import { enqueueSnackbar } from "notistack";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import { extractVariables } from "src/utils/utils";
import { LoadingButton } from "@mui/lab";
import { ConfirmDialog } from "src/components/custom-dialog";
import { coveredDrawerSx, useDrawerCovered } from "src/hooks/use-drawer-stack";

const defaultValues = {
  templateType: "Futureagi",
  name: "",
  criteria: "",
  outputType: "Pass/Fail",
  config: {
    model: "",
    reverseOutput: false,
  },
  choices: [],
  multiChoice: false,
  tags: [],
  description: "",
};

const transformChoices = (choices) => {
  const choicesCopy = {};
  choices?.forEach((choice) => {
    choicesCopy[choice.key] = choice.value;
  });
  return choicesCopy;
};

const transformCriteria = (formValues) => {
  let criteriaCopy = formValues.criteria;
  const extractedKeys = extractVariables(criteriaCopy);
  extractedKeys.forEach((key, index) => {
    const regex = new RegExp(`\\{\\{${key}\\}\\}`, "g");
    criteriaCopy = criteriaCopy.replace(regex, `{{variable_${index + 1}}}`);
  });
  return criteriaCopy;
};

const getCustomEvalPayload = (oldData, newData) => {
  const payload = {
    eval_template_id: oldData?.templateId || "",
  };

  if (oldData?.name != newData.name) {
    payload.name = newData.name || "";
  }
  if (oldData?.description != newData.description) {
    payload.description = newData.description || "";
  }
  if (oldData?.criteria != newData.criteria) {
    payload.criteria = transformCriteria(newData || "");
    payload.required_keys = extractVariables(newData.criteria);
  }
  if (oldData?.multiChoice != newData.multiChoice) {
    payload.multi_choice = newData.multiChoice || false;
  }
  if (oldData?.config.model != newData.config.model) {
    payload.model = newData.config.model || "";
  }
  if (JSON.stringify(oldData?.tags) != JSON.stringify(newData.tags)) {
    payload.eval_tags = newData?.tags?.map((tag) => tag.value);
  }
  if (
    newData.output === "choices" &&
    JSON.stringify(oldData?.choices) != JSON.stringify(newData.choices)
  ) {
    payload.choices_map = transformChoices(newData.choices);
  }

  if (oldData?.evalTypeId !== newData.evalTypeId) {
    payload.eval_type_id = newData.evalTypeId;
    payload.template_id = newData.templateId;
  }

  if (newData.templateType === "Function") {
    payload.config = {
      config: {
        ...newData.config.config,
      },
    };
    payload.function_eval = true;
  }

  return payload;
};

const transformCriteriaToKey = (data) => {
  let criteriaCopy = data.criteria;
  data.requiredKeys.forEach((key, index) => {
    const regex = new RegExp(`\\{\\{variable_${index + 1}\\}\\}`, "g");
    criteriaCopy = criteriaCopy.replace(regex, `{{${key}}}`);
  });
  return criteriaCopy;
};

const generateConfigData = (data) => {
  if (!data) {
    return defaultValues;
  }
  const templateType = FUTUREAGI_LLM_MODELS.some(
    (item) => item.value === data.model,
  );
  const functionEval = data.functionEval;
  const choicesMap = data?.config?.choicesMap || {};
  let defaultValue = {
    ...data,
    templateType: templateType ? "Futureagi" : "Llm",
    name: data.name,
    criteria: transformCriteriaToKey(data) || "",
    outputType: data.output,
    config: {
      model: data.model,
      reverseOutput: false,
    },
    choices: Object.entries(choicesMap).map(([key, value]) => ({
      key,
      value,
    })),
    multiChoice: data.multiChoice || false,
    tags: data.evalTags.map((tag) => ({
      key: tag,
      value: tag,
    })),
    description: data.description,
  };
  if (functionEval) {
    defaultValue = {
      ...defaultValue,
      templateType: "Function",
      config: {
        config: data.config,
      },
      evalTypeId: data.evalTypeId,
    };
  }
  return defaultValue;
};

const EditCustomEvalsChild = ({ onClose, evalId }) => {
  const {
    setSelectedEval,
    formValues,
    actionButtonConfig: { id },
  } = useEvaluationContext();
  const [evalsFormData, setEvalsFormData] = useState({});
  const [isDelete, setIsDelete] = useState(false);
  const queryClient = useQueryClient();

  const { data: configData, isPending } = useQuery({
    queryKey: ["evalsConfig", evalId],
    queryFn: () => {
      return axios.get(endpoints.develop.eval.getEvalConfigs, {
        params: { eval_id: evalId },
      });
    },
    enabled: !!evalId,
    select: (data) => {
      const result = data?.data;
      setSelectedEval(result?.result?.eval);
      if (result?.result?.owner === "user") {
        const generated = generateConfigData(result?.result?.eval);
        return { ...generated, owner: result?.result?.owner };
      }
      return {
        ...(result?.result?.eval && { ...result?.result?.eval }),
        evalTemplateTags: result?.result?.type?.toUpperCase?.(),
      };
    },
    staleTime: 1000,
  });

  const { mutate: saveCustomEvalMutate, isPending: isCustomEvalSaving } =
    useMutation({
      /**
       * @param {Object} data
       * @returns
       */
      mutationFn: (data) =>
        axios.post(endpoints.develop.eval.updateEvalsTemplate, data),
      onSuccess: (data) => {
        data?.data?.result &&
          enqueueSnackbar(data.data.result, { variant: "success" });
        trackEvent(Events.usageSaveConfigClicked, {
          [PropertyName.click]: true,
        });
        queryClient.invalidateQueries({
          queryKey: ["evalsConfig", evalId],
        });
        queryClient.invalidateQueries({
          queryKey: ["develop", "user-eval-list", id],
        });
        onClose();
      },
    });

  useMemo(() => {
    if (
      formValues &&
      JSON.stringify(formValues) !== JSON.stringify(evalsFormData)
    ) {
      setEvalsFormData(formValues);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [formValues]);

  const saveCustomEvals = () => {
    const payload = getCustomEvalPayload(configData, formValues);
    if (Object.keys(payload).length > 1) saveCustomEvalMutate(payload);
    else
      enqueueSnackbar({
        message: "Nothing to update!",
        variant: "info",
      });
  };

  const { mutate: handleDelete, isPending: deletePending } = useMutation({
    mutationFn: () =>
      axios.post(endpoints.develop.eval.deleteEvalsTemplate, {
        eval_template_id: evalId,
      }),
    onSuccess: () => {
      enqueueSnackbar(`${configData?.name} evaluation has been deleted`, {
        variant: "success",
      });
      onClose();
      queryClient.invalidateQueries({
        queryKey: ["develop", "user-eval-list"],
      });
    },
  });

  if (isPending) {
    return (
      <Box width="35vw">
        <CustomEvalsLoading onClose={onClose} />
      </Box>
    );
  }

  return (
    <Box sx={{ padding: 2 }}>
      <ConfirmDialog
        open={isDelete}
        maxWidth="xs"
        onClose={() => setIsDelete(false)}
        title={"Delete Evaluation"}
        content={
          <Typography
            typography={"s1"}
            fontWeight={"fontWeightRegular"}
            color="text.primary"
          >
            Are you sure you’d like to delete this evaluation :
            <b> {configData?.name || "Unnamed Evaluation"}</b> Once deleted, the
            evaluation applied on the platform will also be deleted.
          </Typography>
        }
        action={
          <LoadingButton
            onClick={handleDelete}
            variant="contained"
            size="small"
            color="error"
            loading={deletePending}
          >
            Delete Evaluation
          </LoadingButton>
        }
      />
      <ShowComponent condition={configData?.owner === "user"}>
        <CustomEvalsForm
          onClose={onClose}
          showTest={true}
          isEvalsView
          evalsData={configData}
          onFormSave={saveCustomEvals}
          loadingSaveButton={isCustomEvalSaving}
          saveButtonTitle="Update Evaluation"
          disableOutputType
          defaultCriteria={configData?.criteria}
          isEvalConfig
          hideTitle
          hideBackButtons
          deleteButon={
            <Button
              type="button"
              variant="outlined"
              onClick={() => setIsDelete(true)}
            >
              <Typography typography={"s2"} fontWeight={"fontWeightMedium"}>
                Delete Evaluation
              </Typography>
            </Button>
          }
          titleComponent={
            <Box
              sx={{
                display: "flex",
                gap: "4px",
                marginBottom: 2,
                justifyContent: "space-between",
              }}
            >
              <Box
                sx={{ display: "flex", flexDirection: "column", gap: "4px" }}
              >
                <Typography
                  typography="s1"
                  fontWeight={"fontWeightMedium"}
                  color="text.primary"
                >
                  Configure
                </Typography>
                <Typography
                  typography="s1"
                  fontWeight={"fontWeightRegular"}
                  color="text.primary"
                >
                  Configure your own evaluation using custom metrics and test
                  cases.
                </Typography>
              </Box>
              <IconButton
                onClick={onClose}
                sx={{
                  padding: 0,
                  paddingY: 0,
                }}
              >
                <Iconify
                  // @ts-ignore
                  icon="solar:close-linear"
                  sx={{
                    width: (theme) => theme.spacing(4),
                    height: (theme) => theme.spacing(2),
                    color: "text.primary",
                  }}
                />
              </IconButton>
            </Box>
          }
        />
      </ShowComponent>
    </Box>
  );
};

EditCustomEvalsChild.propTypes = {
  evaluation: PropTypes.object,
  onClose: PropTypes.func,
  evalId: PropTypes.string,
};

const EditCustomEvals = ({ open, onClose, evaluation, ...rest }) => {
  const covered = useDrawerCovered(open);
  return (
    <Drawer
      anchor="right"
      open={open}
      sx={coveredDrawerSx(covered)}
      onClose={onClose}
      PaperProps={{
        sx: {
          height: "100vh",
          position: "fixed",
          zIndex: 9999,
          borderRadius: "8px",
          backgroundColor: "background.paper",
        },
      }}
      ModalProps={{
        BackdropProps: {
          style: { backgroundColor: "transparent" },
        },
      }}
    >
      <EditCustomEvalsChild
        onClose={onClose}
        evalId={evaluation?.id}
        {...rest}
      />
    </Drawer>
  );
};

export default EditCustomEvals;

EditCustomEvals.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  evaluation: PropTypes.object,
};
