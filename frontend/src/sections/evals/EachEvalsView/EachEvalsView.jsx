import React from "react";
import { Box, Button, Drawer, IconButton, Typography } from "@mui/material";
import { useQuery } from "@tanstack/react-query";
import PropTypes from "prop-types";
import HeadingAndSubHeading from "src/components/HeadingAndSubheading/HeadingAndSubheading";
import Iconify from "src/components/iconify";
import { FUTUREAGI_LLM_MODELS } from "src/sections/common/EvaluationDrawer/validation";
import axios, { endpoints } from "src/utils/axios";
import { ShowComponent } from "src/components/show";
import CustomEvalsForm from "src/sections/common/EvaluationDrawer/CustomEvalsForm";
import EvaluationMappingForm from "src/sections/common/EvaluationDrawer/EvaluationMappingForm";
import SystemEvalsLoading from "./SystemEvalsLoading";
import CustomEvalsLoading from "./CustomEvalsLoading";
import SvgColor from "src/components/svg-color";
import { Evals_Docs_mapping, evalsDoc } from "../constant";
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
  // checkInternet: false,
};

const transformCriteriaToKey = (data) => {
  let criteriaCopy = data.criteria;
  data.requiredKeys.forEach((key, index) => {
    const regex = new RegExp(`\\{\\{variable_${index + 1}\\}\\}`, "g");
    criteriaCopy = criteriaCopy.replace(regex, `{{${key}}}`);
  });
  return criteriaCopy;
};

const generateDefaultValue = (data) => {
  if (!data) {
    return defaultValues;
  }
  const templateType = FUTUREAGI_LLM_MODELS.some(
    (item) => item.value === data.model,
  );
  const choicesMap = data?.config?.choices_map || {};
  const functionEval = data.function_eval;
  let defaultValue = {
    ...data,
    templateType: templateType ? "Futureagi" : "Llm",
    name: data.name,
    criteria: templateType ? transformCriteriaToKey(data) : data.criteria || "",
    outputType: data.output,
    config: {
      model: data.model,
      reverseOutput: false,
    },
    choices: Object.entries(choicesMap).map(([key, value]) => ({
      key,
      value,
    })),
    multiChoice: data.multi_choice || false,
    tags: data.eval_tags.map((tag) => ({
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
      evalTypeId: data.eval_type_id,
    };
  }
  return defaultValue;
};

export const EachSystemEvalsViewChild = ({ evaluation, onClose }) => {
  const { data: evalConfig } = useQuery({
    queryKey: ["evalsConfig", evaluation?.id],
    queryFn: () => {
      return axios.get(endpoints.develop.eval.getEvalConfigs, {
        params: { eval_id: evaluation?.id },
      });
    },
    enabled: !!evaluation?.id,
    select: (data) => {
      const result = data?.data;
      if (result?.result?.owner === "user") {
        const generated = generateDefaultValue(result?.result?.eval);
        return { ...generated, owner: result?.result?.owner };
      }
      return result?.result?.eval;
    },
    staleTime: 1000 * 10,
  });

  if (!evalConfig) {
    return <SystemEvalsLoading onClose={onClose} />;
  }
  return (
    <Box sx={{ padding: 2, display: "flex", flexDirection: "column", gap: 2 }}>
      <Box
        display={"flex"}
        flexWrap={"nowrap"}
        gap={1}
        flexDirection={"row"}
        justifyContent={
          Evals_Docs_mapping[evalConfig?.name] ? "flex-start" : "space-between"
        }
      >
        <HeadingAndSubHeading
          heading={
            <Typography typography={"m3"} fontWeight={"fontWeightSemiBold"}>
              {evalConfig?.name}
            </Typography>
          }
          subHeading={
            <Typography
              typography={"s1"}
              color={"text.primary"}
              fontWeight={"fontWeightRegular"}
            >
              {evalConfig?.description}
            </Typography>
          }
        />
        {Evals_Docs_mapping[evalConfig?.name] && (
          <Button
            variant="outlined"
            size="small"
            sx={{
              color: "text.primary",
              minWidth: "100px",
              maxWidth: "100px",
              marginLeft: "auto",
              whiteSpace: "nowrap",
              borderColor: "text.disabled",
            }}
            startIcon={<SvgColor src="/assets/icons/ic_docs_single.svg" />}
            component="a"
            href={`${evalsDoc}/builtin/${Evals_Docs_mapping[evalConfig?.name]}`}
            target="_blank"
            rel="noopener noreferrer"
          >
            View Docs
          </Button>
        )}
        <IconButton onClick={onClose} size="small">
          <Iconify
            icon="solar:close-linear"
            sx={{
              width: 20,
              height: 20,
              color: "text.primary",
            }}
          />
        </IconButton>
      </Box>
      <ShowComponent condition={Boolean(evalConfig)}>
        <EvaluationMappingForm
          onClose={onClose}
          evalsData={{
            requiredKeys: evalConfig.requiredKeys,
            optionalKeys: evalConfig.optionalKeys,
            name: evalConfig.name,
            templateName: evalConfig.templateName,
            evalTemplateName: evalConfig.evalTemplateName,
            functionParamsSchema:
              evalConfig.functionParamsSchema ||
              evalConfig.function_params_schema ||
              evalConfig.config?.functionParamsSchema ||
              evalConfig.config?.function_params_schema ||
              null,
            params: evalConfig.params || evalConfig.config?.params || {},
            configParamsDesc:
              evalConfig.configParamsDesc ||
              evalConfig.config_params_desc ||
              evalConfig.config?.configParamsDesc ||
              evalConfig.config?.config_params_desc ||
              null,
          }}
          isViewMode
          isEvalConfig
          fullWidth
          hideTitle
          hideFieldColumns
          hideBackButtons
          allColumns={[]}
          refreshGrid={() => {}}
          onBack={undefined}
          onFormSave={undefined}
          requiredColumnIds={undefined}
          preserveName
        />
      </ShowComponent>
    </Box>
  );
};

EachSystemEvalsViewChild.propTypes = {
  evaluation: PropTypes.object,
  onClose: PropTypes.func,
  data: PropTypes.object,
};

const EachCustomEvalsViewChild = ({ evaluation, onClose }) => {
  const { data: evalConfig } = useQuery({
    queryKey: ["evalsConfig", evaluation?.id],
    queryFn: () => {
      return axios.get(endpoints.develop.eval.getEvalConfigs, {
        params: { eval_id: evaluation?.id },
      });
    },
    enabled: !!evaluation?.id,
    select: (data) => {
      const result = data?.data;
      if (result?.result?.owner === "user") {
        const generated = generateDefaultValue(result?.result?.eval);
        return { ...generated, owner: result?.result?.owner };
      }
      return result?.result?.eval;
    },
    staleTime: 1000 * 10,
  });

  if (!evalConfig) {
    return <CustomEvalsLoading onClose={onClose} />;
  }

  return (
    <Box sx={{ padding: 2 }}>
      <ShowComponent condition={Boolean(evalConfig)}>
        <CustomEvalsForm
          onClose={onClose}
          evalsData={evalConfig}
          isViewMode
          fullWidth
          hideBackButtons
        />
      </ShowComponent>
    </Box>
  );
};

EachCustomEvalsViewChild.propTypes = {
  evaluation: PropTypes.object,
  onClose: PropTypes.func,
};

const EachEvalsView = ({ evaluation, onClose, open }) => {
  const isUserBuild = evaluation?.eval_template_tags?.includes("USER_BUILT");
  const covered = useDrawerCovered(open);
  return (
    <Drawer
      anchor="right"
      open={open}
      sx={coveredDrawerSx(covered)}
      variant="temporary"
      onClose={onClose}
      PaperProps={{
        sx: {
          height: "100vh",
          width: "640px",
          position: "fixed",
          zIndex: 10,
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
      <ShowComponent condition={Boolean(!isUserBuild)}>
        <EachSystemEvalsViewChild evaluation={evaluation} onClose={onClose} />
      </ShowComponent>
      <ShowComponent condition={Boolean(isUserBuild)}>
        <EachCustomEvalsViewChild evaluation={evaluation} onClose={onClose} />
      </ShowComponent>
    </Drawer>
  );
};

export default EachEvalsView;
EachEvalsView.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  evaluation: PropTypes.object,
};
