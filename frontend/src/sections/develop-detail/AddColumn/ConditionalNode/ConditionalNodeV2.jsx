import { Box, Collapse, Drawer } from "@mui/material";
import React, { useState } from "react";
import { useConditionalNodeStore } from "../../states";
import PropTypes from "prop-types";
import { useMutation, useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { useParams } from "react-router";
import DynamicColumnSkeleton from "../DynamicColumnSkeleton";
import { ShowComponent } from "../../../../components/show";
import PreviewAddColumn from "../PreviewAddColumn";
import { useDatasetColumnConfig } from "src/api/develop/develop-detail";
import ConditionalNodeMainForm from "./ConditionalNodeMainForm";
import { FormProvider, useForm } from "react-hook-form";
import {
  _transformDynamicColumnConfig,
  getConditionalNodeDefaultValues,
  getConditionalNodeValidation,
} from "./common";
import { RunPromptForm } from "../../RunPrompt/RunPrompt";
import { RetrievalChild } from "../Retrieval/Retrieval";
import { ExtractEntitiesChild } from "../ExtractEntities/ExtractEntities";
import { ExtractJsonKeyChild } from "../ExtractJsonKey/ExtractJsonKey";
import { ExecuteCodeChild } from "../ExecuteCode/ExecuteCode";
import { ClassificationChild } from "../Classification/Classification";
import { AddColumnApiCallChild } from "../AddColumnApiCall/AddColumnApiCall";
import { zodResolver } from "@hookform/resolvers/zod";
import _ from "lodash";
import { transformParameterType } from "../../RunPrompt/common";
import { coveredDrawerSx, useDrawerCovered } from "src/hooks/use-drawer-stack";

const FORM_COMPONENTS = {
  run_prompt: RunPromptForm,
  retrieval: RetrievalChild,
  extract_entities: ExtractEntitiesChild,
  extract_json: ExtractJsonKeyChild,
  extract_code: ExecuteCodeChild,
  classification: ClassificationChild,
  api_call: AddColumnApiCallChild,
};

// New separate component for rendering forms with useQuery
const FormRenderer = ({
  openForm,
  formControls,
  allColumns,
  onFormSubmit,
  onClose,
}) => {
  const config = formControls.getValues(
    `config.${openForm?.index}.branchNodeConfig.config`,
  );

  const transformedInitialData = config
    ? _transformDynamicColumnConfig(openForm.formType, config, allColumns)
    : config;

  const model = transformedInitialData?.model;
  const provider = transformedInitialData?.runPromptConfig?.providers;
  const modelType = transformedInitialData?.runPromptConfig?.modelType;

  const { data: modelParams, isLoading: isLoadingModelParams } = useQuery({
    queryKey: ["model-params", model, provider, modelType],
    queryFn: () =>
      axios.get(endpoints.develop.modelParams, {
        params: {
          model: model,
          provider: provider,
          model_type: modelType,
        },
      }),
    enabled: !!(
      model &&
      provider &&
      modelType &&
      openForm?.formType === "run_prompt"
    ),
    select: (d) => d.data?.result,
  });
  if (!openForm) return null;

  const FormComponent = FORM_COMPONENTS[openForm.formType];
  if (!FormComponent) return null;

  const transformedModelParamsSliders = modelParams?.sliders?.map((item) => {
    if (
      transformedInitialData?.runPromptConfig[_.camelCase(item?.label)] !==
      undefined
    ) {
      return {
        ...item,
        id: _.camelCase(item?.label),
        value:
          transformedInitialData?.runPromptConfig[_.camelCase(item?.label)],
      };
    }
    return item;
  });

  const finalTransformation = {
    sliders: transformedModelParamsSliders ?? [],
    ...(modelParams?.booleans && {
      booleans: transformParameterType(
        modelParams?.booleans,
        transformedInitialData?.runPromptConfig,
        "booleans",
      ),
    }),
    ...(modelParams?.dropdowns && {
      dropdowns: transformParameterType(
        modelParams?.dropdowns,
        transformedInitialData?.runPromptConfig,
        "dropdowns",
      ),
    }),
  };

  if (
    openForm?.formType === "run_prompt" &&
    isLoadingModelParams &&
    transformedModelParamsSliders === undefined
  )
    return null;

  return (
    <FormComponent
      onClose={onClose}
      open
      onFormSubmit={onFormSubmit}
      initialData={transformedInitialData}
      initialModelParams={finalTransformation}
    />
  );
};

FormRenderer.propTypes = {
  openForm: PropTypes.shape({
    formType: PropTypes.string.isRequired,
    index: PropTypes.number.isRequired,
  }),
  formControls: PropTypes.object.isRequired,
  allColumns: PropTypes.array,
  onFormSubmit: PropTypes.func.isRequired,
  onClose: PropTypes.func.isRequired,
};

const ConditionalNodeV2Child = ({ editId, initialData }) => {
  const { dataset } = useParams();
  const allColumns = useDatasetColumnConfig(dataset);
  const [openForm, setOpenForm] = useState(null);

  const formControls = useForm({
    defaultValues: getConditionalNodeDefaultValues(initialData, allColumns),
    resolver: zodResolver(
      getConditionalNodeValidation(allColumns, Boolean(editId)),
    ),
  });
  const {
    mutate: preview,
    isPending: isPreviewPending,
    data: previewData,
  } = useMutation({
    mutationFn: (data) => {
      return axios.post(
        endpoints.develop.addColumns.preview(dataset, "conditional"),
        data,
      );
    },
    mutationKey: "conditional-node-preview",
  });

  const onFormSubmit = (data) => {
    let finalData = data;
    if (openForm.formType === "run_prompt") {
      finalData = _.mapKeys(data?.config, (v, k) => _.camelCase(k));
    }
    formControls.setValue(
      `config.${openForm.index}.branchNodeConfig.config`,
      finalData,
    );
    setOpenForm(null);
  };

  return (
    <FormProvider {...formControls}>
      <Box sx={{ display: "flex", height: "100vh", minWidth: "550px" }}>
        <Box
          sx={{
            display: "flex",
            wordWrap: "break-word",
            overflowY: "auto",
            height: "90vh",
          }}
        >
          <PreviewAddColumn open={previewData} previewData={previewData} />
        </Box>
        <Collapse orientation="horizontal" in={!openForm}>
          <ConditionalNodeMainForm
            preview={preview}
            isPreviewPending={isPreviewPending}
            editId={editId}
            setOpenForm={setOpenForm}
          />
        </Collapse>
        <Collapse orientation="horizontal" in={Boolean(openForm)}>
          <FormRenderer
            openForm={openForm}
            formControls={formControls}
            allColumns={allColumns}
            onFormSubmit={onFormSubmit}
            onClose={() => setOpenForm(null)}
          />
        </Collapse>
      </Box>
    </FormProvider>
  );
};

ConditionalNodeV2Child.propTypes = {
  editId: PropTypes.string,
  initialData: PropTypes.object,
};

const ConditionalNodeV2 = () => {
  const { openConditionalNode, setOpenConditionalNode } =
    useConditionalNodeStore();

  const onClose = () => {
    setOpenConditionalNode(false);
  };

  const editId = openConditionalNode?.editId;

  const { data: columnConfig, isLoading: isLoadingColumnConfig } = useQuery({
    queryKey: ["dynamic-column-config", editId],
    queryFn: () =>
      axios.get(endpoints.develop.addColumns.getColumnConfig(editId)),
    enabled: Boolean(editId),
    select: (data) => data?.data?.result?.metadata,
    gcTime: 0, // Don't cache at all
    staleTime: 0, // Consider data stale immediately
  });

  const covered = useDrawerCovered(openConditionalNode);
  return (
    <Drawer
      anchor="right"
      open={openConditionalNode}
      sx={coveredDrawerSx(covered)}
      onClose={onClose}
      PaperProps={{
        sx: {
          height: "100vh",
          position: "fixed",
          zIndex: 2,
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
      {isLoadingColumnConfig && (
        <Box sx={{ minWidth: "510px", height: "100%" }}>
          <DynamicColumnSkeleton />
        </Box>
      )}
      <ShowComponent condition={!isLoadingColumnConfig}>
        <ConditionalNodeV2Child editId={editId} initialData={columnConfig} />
      </ShowComponent>
    </Drawer>
  );
};

export default ConditionalNodeV2;
