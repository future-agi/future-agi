import { Box, Button, Divider, Stack, Typography } from "@mui/material";
import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import Iconify from "src/components/iconify";
import PageHeadings from "src/sections/develop-detail/Common/PageHeadings";
import FormTabs from "./CreateSyntheticData/FormTabs";
import { useLocation, useNavigate, useParams } from "react-router";
import SummarySection from "./CreateSyntheticData/SummarySection";
import DetailForm from "./CreateSyntheticData/DetailForm";
import { z } from "zod";
import { FormProvider, useForm, useWatch } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import AddColumnForm from "./CreateSyntheticData/AddColumnForm";
import AddDescriptionForm from "./CreateSyntheticData/AddDescriptionForm";
import HelpCreateSyntheticData from "./CreateSyntheticData/HelpCreateSyntheticData";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import axios, { endpoints } from "src/utils/axios";
import { enqueueSnackbar } from "notistack";
import { useMutation } from "@tanstack/react-query";
import { ConfirmDialog } from "src/components/custom-dialog";
import { LoadingButton } from "@mui/lab";
import { useKnowledgeBaseList } from "src/api/knowledge-base/files";
import PropTypes from "prop-types";

import { getSyntheticDefaultValues } from "./CreateSyntheticData/common";
import CreateSyntheticDatasetOptionModal from "./EditSyntheticData/CreateSyntheticDatasetOptionModal";
import { useEditSyntheticDataStore } from "./EditSyntheticData/state";
import { useDatasetOriginStore } from "../../develop-detail/states";
import { useBeforeUnload } from "src/hooks/useBeforeUnload";
import { getRequestErrorMessage } from "src/utils/errorUtils";

const allTabList = [
  { label: "Add details", value: "addDetails", status: "active" },
  {
    label: "Add column properties",
    value: "addColumnProperties",
    status: "pending",
  },
  { label: "Add description", value: "addDescription", status: "pending" },
];

const createValidationSchema = (datasetId, descriptionField) =>
  z.object({
    name: z.string().refine(
      (value) => (!datasetId ? value?.trim().length > 0 : true), // Conditional validation based on `datasetId`
      { message: "Name is required" },
    ),
    description: z.string().min(1, "Description is required"), // Optional field
    useCase: z.string().optional(),
    pattern: z.string().optional(),
    rowNumber: z
      .number()
      .refine((value) => (value > 0 ? Number(value) >= 10 : false), {
        message: "Row number must be at least 10",
      }),
    columns: z
      .array(
        z.object({
          name: z.string().min(1, "Column name is required"),
          description: descriptionField
            ? z.string().min(1, "Column Description is required")
            : z.string().optional(),
          // z.string().optional(), //.min(1, "Column Description is required"), // Optional description
          data_type: z.string().min(1, "Column Data Type is required"),
          property: z.array(
            z.object({
              type: z.string().min(1, "Property type is required"),
              value: z.preprocess(
                (val) => {
                  if (typeof val === "number") {
                    return val;
                  }
                  if (
                    typeof val === "string" &&
                    val.trim() !== "" &&
                    !isNaN(Number(val))
                  ) {
                    return Number(val);
                  }
                  return val;
                },
                z.union([
                  z.number(),
                  z.string().min(1, "Property value is required"),
                ]),
              ),
            }),
          ),
          // .min(1, "Property value is required"),
        }),
      )
      .min(1, "At least one column is required"),
  });

const CreateSyntheticDataView = ({
  editMode,
  onClose,
  editData,
  onEditSuccessCallback,
}) => {
  const [tabList, setTabList] = useState([...allTabList]);
  const [activeTab, setActiveTab] = useState(allTabList[0]);
  const [openHelp, setOpenHelp] = useState(false);
  const [showConfirmation, setShowConfirmation] = useState(false);
  const [focusField, setFocusField] = useState("");
  const navigate = useNavigate();
  const { state } = useLocation();
  const [newProps, setNewProps] = useState([[]]);
  const validationSchema = createValidationSchema(
    null,
    tabList[2].status === "active",
  );
  const { openDatasetCreateOptions, setOpenDatasetCreateOptions } =
    useEditSyntheticDataStore();
  const { setProcessingComplete } = useDatasetOriginStore();
  const modalSubmitRef = useRef(null);
  const { dataset } = useParams();
  const methods = useForm({
    defaultValues: getSyntheticDefaultValues(editData),
    resolver: zodResolver(validationSchema),
  });

  const {
    handleSubmit,
    control,
    reset,
    formState: { isDirty },
    getValues,
    setValue,
  } = methods;

  useEffect(() => {
    reset(getSyntheticDefaultValues(editData));
  }, [editData, reset]);

  const [name, description, rowNumber, kb_id] = useWatch({
    control,
    name: ["name", "description", "rowNumber", "kb_id"],
  });

  const handleTabChange = (index) => {
    const next = checkNext(activeTab, index, getValues);
    if (!next) return;
    setTabList((pre) =>
      pre.map((temp, ind) => {
        if (ind === index) setActiveTab({ ...temp, status: "active" });
        return {
          ...temp,
          status:
            ind < index ? "completed" : ind === index ? "active" : "pending",
        };
      }),
    );
  };

  const handleBack = () => {
    reset();
    editMode
      ? navigate(`/dashboard/develop/${dataset}`)
      : navigate("/dashboard/develop");
  };

  const { data: knowledgeBaseList } = useKnowledgeBaseList("", null);

  const knowledgeBaseOptions = useMemo(
    () =>
      (knowledgeBaseList || []).map(({ id, name }) => ({
        label: name,
        value: id,
      })),
    [knowledgeBaseList],
  );

  const selectedKB = useMemo(() => {
    if (kb_id) {
      return knowledgeBaseOptions.find((item) => item.value === kb_id)?.label;
    }
    return "";
  }, [knowledgeBaseOptions, kb_id]);

  const handleDatasetCreateOptionAction = useCallback((optionState) => {
    modalSubmitRef.current?.(optionState);
  }, []);

  const { mutate: createSyntheticData, isPending } = useMutation({
    mutationFn: (data) => {
      // these commented code is required when we are working for single dataset.
      // if (datasetId) {
      //   return axios.post(
      //     endpoints.develop.addSyntheticDataset(datasetId),
      //     data,
      //   );
      // } else {
      return axios.post(endpoints.develop.createSyntheticDataset, data);
      // }
    },
    onSuccess: (res) => {
      const data = res?.data?.result?.data;
      if (onClose) onClose();
      setTimeout(() => {
        navigate(`/dashboard/develop/${data?.id}?tab=data`, { replace: true });
      }, 0);
      enqueueSnackbar(
        res?.data?.result?.message || "Dataset uploaded successfully",
        {
          variant: "success",
        },
      );
      reset();
    },
    onError: (error) => {
      enqueueSnackbar(
        getRequestErrorMessage(error, "Failed to create synthetic dataset", {
          retryAction: "creating this synthetic dataset",
        }),
        { variant: "error" },
      );
    },
  });

  const { mutate: updateSyntheticData, isPending: isUpdating } = useMutation({
    mutationFn: (data) => {
      return axios.put(
        endpoints.develop.updateSyntheticDataset(dataset),
        data.payload,
      );
    },
    onSuccess: (res) => {
      const updatedDatasetId =
        res?.data?.result?.data?.dataset_id ??
        res?.data?.result?.data?.datasetId ??
        dataset;
      if (onClose) onClose();
      if (onEditSuccessCallback) {
        onEditSuccessCallback();
      }
      enqueueSnackbar(res?.data?.result?.message || "Dataset updated", {
        variant: "success",
      });
      setTimeout(() => {
        navigate(`/dashboard/develop/${updatedDatasetId}?tab=data`, {
          replace: true,
        });
      }, 0);
      reset();
    },
    onError: (error) => {
      enqueueSnackbar(
        getRequestErrorMessage(error, "Failed to update synthetic dataset", {
          retryAction: "updating this synthetic dataset",
        }),
        { variant: "error" },
      );
    },
  });

  const onSubmit = (formData) => {
    trackEvent(Events.dataAddSuccessfull, {
      [PropertyName.method]: "add using synthetic data",
    });
    const cols = {};
    const replaceColumn = (data, key) => {
      let incomingText = data || "";
      formData.columns.forEach(({ name }) => {
        const pattern = new RegExp(`{{${name}}}`, "g");
        if (incomingText && incomingText?.length)
          incomingText = incomingText.replace(pattern, cols[name]);
      });
      cols[key] = incomingText;
      return incomingText;
    };

    const payload = {
      dataset: {
        name: formData.name,
        description: formData.description,
        objective: formData.useCase,
        patterns: formData.pattern,
      },
      num_rows: Number(formData.rowNumber),
      kb_id: kb_id,
      columns: formData.columns.map((item, index) => {
        const property = {};
        item.property.forEach((dummy) => {
          property[dummy.type] = dummy.value;
        });
        if (index === 0) {
          cols[item.name] = item.description;
        }
        const newItem = {
          ...item,
          description: replaceColumn(item.description, item.name),
          property,
          // ...(datasetId && { is_new: true, skip: true }),  // It need for the perticular dataset
        };
        return newItem;
      }),
    };

    if (!kb_id) {
      delete payload.kb_id;
    }

    trackEvent(Events.createSyntheticDatasetClicked, {
      [PropertyName.formFields]: {
        ...payload,
        ...(kb_id && { knowledgeBaseName: selectedKB }),
      },
    });
    setProcessingComplete(false);

    if (editMode) {
      // Define closure over `payload`
      modalSubmitRef.current = ({ option, datasetName }) => {
        const updatedPayload = {
          ...payload,
          dataset: {
            ...payload.dataset,
            ...(option === "replace_dataset"
              ? { name: payload?.dataset?.name }
              : option === "new_dataset"
                ? { name: datasetName }
                : {}),
          },
          ...(option === "replace_dataset" && { regenerate: true }),
        };

        if (
          option === "replace_dataset" ||
          option === "add_to_existing_dataset"
        ) {
          updateSyntheticData({
            payload: updatedPayload,
          });
        } else if (option === "new_dataset") {
          // reuse create
          createSyntheticData(updatedPayload);
        }
        setOpenDatasetCreateOptions(false);
      };

      setOpenDatasetCreateOptions(true);
      return;
    }
    createSyntheticData(payload);
  };

  useEffect(() => {
    if (state?.knowledgeId) {
      setValue("kb_id", state?.knowledgeId);
    }
  }, [setValue, state?.knowledgeId]);

  useBeforeUnload(isDirty);

  // useEffect(() => {
  //   return () => {
  //     reset();
  //   };
  // }, []);

  return (
    <>
      <Box
        sx={{
          backgroundColor: "background.paper",
          height: "100%",
          padding: 2,
          display: "flex",
          flexDirection: "column",
          gap: 2,
        }}
      >
        <ConfirmDialog
          open={showConfirmation}
          maxWidth="sm"
          onClose={() => setShowConfirmation(false)}
          title="Are you sure you want to cancel the progress?"
          content={`By clicking "Yes" you will lose the progress on this step.`}
          action={
            <LoadingButton
              variant="contained"
              type="button"
              color="error"
              size="small"
              sx={{ paddingX: "24px", minWidth: "88px" }}
              onClick={handleBack}
            >
              Yes
            </LoadingButton>
          }
        />

        <Button
          size="small"
          sx={{
            color: "text.secondary",
            width: "max-content",
            border: "1px solid",
            borderColor: "action.hover",
            paddingX: "12px",
          }}
          startIcon={
            <Iconify
              // @ts-ignore
              icon="octicon:chevron-left-24"
              width="24px"
              sx={{ color: "text.secondary" }}
            />
          }
          onClick={() => (isDirty ? setShowConfirmation(true) : handleBack())}
        >
          Back to dataset
        </Button>

        <Box
          sx={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "flex-start",
          }}
        >
          <PageHeadings
            title={editMode ? "Configure Dataset" : "Create Synthetic data"}
            description={
              editMode
                ? "Edit, replace, create, or add data to an existing dataset."
                : "Create datasets and experiments to evaluate your application"
            }
          />
          <Stack direction={"row"} gap={3} alignItems={"center"}>
            {activeTab.value !== "addDetails" && (
              <Typography
                variant="s1"
                fontWeight="fontWeightMedium"
                color="text.secondary"
                sx={{
                  display: "flex",
                  alignItems: "center",
                  gap: 0.5,
                  cursor: "pointer",
                }}
                onClick={() => setOpenHelp(true)}
              >
                Help
                <Iconify
                  icon="material-symbols-light:help-outline"
                  color="text.secondary"
                />
              </Typography>
            )}
            {/* <ShowComponent condition={editMode}>
              <IconButton
                onClick={() =>
                  isDirty ? setShowConfirmation(true) : onClose()
                }
              >
                <SvgColor
                  src={"/assets/icons/ic_close.svg"}
                  sx={{
                    height: 24,
                    width: 24,
                    color: "text.primary",
                  }}
                />
              </IconButton>
            </ShowComponent> */}
          </Stack>
        </Box>
        <FormProvider {...methods}>
          <Box
            sx={{ display: "flex", gap: 2, height: "calc(100vh - 150px)" }}
            component={"form"}
            onSubmit={handleSubmit(onSubmit)}
          >
            <Box
              sx={{
                flex: 1,
                display: "flex",
                flexDirection: "column",
                gap: "32px",
                height: "100%",
              }}
            >
              <FormTabs handleTabChange={handleTabChange} tabList={tabList} />
              {activeTab.value === "addDetails" && (
                <DetailForm
                  control={control}
                  handleNextTab={handleTabChange}
                  setFocusField={setFocusField}
                  knowledgeId={state?.knowledgeId}
                  rowNumber={rowNumber}
                  editMode={editMode}
                  disabledNext={
                    !(name && description && Number(rowNumber || 0) >= 10)
                  }
                />
              )}
              {activeTab.value === "addColumnProperties" && (
                <AddColumnForm
                  control={control}
                  handleNextTab={handleTabChange}
                  setNewProps={setNewProps}
                  newProps={newProps}
                  reset={reset}
                />
              )}
              {activeTab.value === "addDescription" && (
                <AddDescriptionForm
                  control={control}
                  editMode={editMode}
                  handleNextTab={handleTabChange}
                  isPending={isPending || isUpdating}
                />
              )}
            </Box>
            <Divider orientation="vertical" />
            <Box sx={{ width: "440px", height: "100%" }}>
              <SummarySection
                focusField={focusField}
                activeTab={activeTab}
                selectedKB={selectedKB}
              />
            </Box>
          </Box>
        </FormProvider>
        <HelpCreateSyntheticData
          open={openHelp}
          onClose={() => setOpenHelp(false)}
        />
      </Box>
      <CreateSyntheticDatasetOptionModal
        editMode={editMode}
        open={openDatasetCreateOptions}
        onClose={() => setOpenDatasetCreateOptions(false)}
        onAction={handleDatasetCreateOptionAction}
        isLoading={isPending || isUpdating}
      />
    </>
  );
};

export default CreateSyntheticDataView;

CreateSyntheticDataView.propTypes = {
  editMode: PropTypes.bool,
  onClose: PropTypes.func,
  editData: PropTypes.object,
  onEditSuccessCallback: PropTypes.func,
};

const checkNext = (prevTab, nextIndex, getValues) => {
  const { name, description, rowNumber, columns } = getValues();

  if (nextIndex === 0) return true;

  const detailFieldFilled = Boolean(
    name && description && Number(rowNumber || 0) >= 10,
  );

  if (nextIndex === 1) {
    if (prevTab.value === "addDetails") {
      return detailFieldFilled;
    }
    return prevTab.value === "addDescription";
  }

  if (nextIndex === 2) {
    const emptyColumnField = columns?.some(
      (item) =>
        !item.name ||
        !item.data_type ||
        item?.property?.some(
          (temp) => !temp.type || !(temp.value && temp.value != 0),
        ),
    );
    if (prevTab.value === "addColumnProperties") {
      return !emptyColumnField;
    } else if (prevTab.value === "addDetails") {
      return detailFieldFilled && !emptyColumnField;
    }
    return false;
  }

  return false;
};
