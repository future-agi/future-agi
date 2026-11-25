import { Box, Drawer, IconButton } from "@mui/material";
import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { useSnackbar } from "notistack";
import PropTypes from "prop-types";
import React, { useMemo, useState } from "react";
import { useFieldArray, useForm } from "react-hook-form";
import { useParams } from "react-router";
import Iconify from "src/components/iconify";
import { useOrganization } from "src/contexts/OrganizationContext";
import axios, { endpoints } from "src/utils/axios";
import AnnotationsModalSkeleton from "../Common/Skeletons/AnnotationsModalSkeleton";
import AnnotationsMainForm from "./AnnotationsMainForm";
import { zodResolver } from "@hookform/resolvers/zod";
import { AnnotationFormSchema } from "./validation";
import logger from "src/utils/logger";
import { coveredDrawerSx, useDrawerCovered } from "src/hooks/use-drawer-stack";

const ConfigureAnnotationsForm = ({
  onClose,
  annotationId,
  handleSetRenderAnnotationTable,
}) => {
  const { dataset } = useParams();
  const { currentOrganizationId } = useOrganization();
  const [selectedLabelIndex, setSelectedLabelIndex] = useState(null);

  const { control, watch, handleSubmit, reset, setValue, trigger } = useForm({
    resolver: zodResolver(AnnotationFormSchema),
    defaultValues: {
      name: "",
      staticFields: [{ column: "", type: "", view: "" }],
      responseFields: [{ column: "", type: "", edit: "", view: "" }],
      labelFields: [{ labelName: "", assign: "" }],
      annotatorFields: [{ addAnnotator: [] }],
      responses: 1,
    },
  });

  const labelWatch = watch("labelFields");

  const {
    fields: staticFields,
    append: staticAppend,
    remove: staticRemove,
  } = useFieldArray({
    control,
    name: "staticFields",
  });
  const {
    fields: responseFields,
    append: responseAppend,
    remove: responseRemove,
  } = useFieldArray({
    control,
    name: "responseFields",
  });
  const {
    fields: labelFields,
    append: labelAppend,
    remove: labelRemove,
    update: labelUpdate,
  } = useFieldArray({
    control,
    name: "labelFields",
  });
  const {
    fields: annotatorFields,
    append: annotatorAppend,
    remove: annotatorRemove,
  } = useFieldArray({
    control,
    name: "annotatorFields",
  });

  const formHandle = {
    staticFields: staticFields,
    staticAppend: staticAppend,
    staticRemove: staticRemove,
    staticItems: "staticFields",
    responseFields: responseFields,
    responseAppend: responseAppend,
    responseRemove: responseRemove,
    responseItems: "responseFields",
    labelFields: labelFields,
    labelAppend: labelAppend,
    labelRemove: labelRemove,
    labelUpdate: labelUpdate,
    labelItems: "labelFields",
    annotatorFields: annotatorFields,
    annotatorAppend: annotatorAppend,
    annotatorRemove: annotatorRemove,
    annotatorItems: "annotatorFields",
    setValue: setValue,
    reset: reset,
  };

  const [, setColumnData] = useState([]);
  const queryClient = useQueryClient();
  const { enqueueSnackbar } = useSnackbar();

  // get staticFieldscolumn data
  const { isLoading: staticLoading, data: staticFieldsApiData } = useQuery({
    queryKey: ["dataset", dataset],
    queryFn: () =>
      axios.get(endpoints.develop.getDatasetDetail(dataset), {
        params: {
          current_page_index: 1,
          filters: [],
          sort: [],
        },
      }),
  });

  // get annotation data
  const { data: annotationData } = useQuery({
    queryKey: [`annotationId-${annotationId}`, annotationId],
    queryFn: () =>
      axios.get(endpoints.annotation.getAnnotationById(annotationId)),
    select: (data) => data.data,
  });

  useMemo(() => {
    if (annotationData) {
      const transformedDefaultValues = {
        name: annotationData.name || "",
        staticFields: annotationData.staticFields.map((field) => ({
          column: field.columnId || "",
          type: field.type || "",
          view: field.view || "",
        })),
        responseFields: annotationData.responseFields.map((field) => ({
          column: field.columnId || "",
          type: field.type || "",
          edit: field.edit || "",
          view: field.view || "",
        })),
        labelFields: annotationData.labels.map((labelId) => ({
          labelName: labelId,
          // assign: annotationData.assignedUsers.join(", ") || "",
          assign:
            annotationData?.labelRequirements?.[labelId] !== undefined
              ? annotationData?.labelRequirements?.[labelId]
                ? "required"
                : "optional"
              : "",
        })),
        annotatorFields: [
          {
            addAnnotator: annotationData.assignedUsers.map((au) => au.id),
          },
        ],
        responses:
          annotationData?.responses?.toString() ||
          annotationData.assignedUsers?.length?.toString() ||
          "1",
        // annotatorFields: {
        //   addAnnotator: annotationData?.assignedUsers.map((userId) => userId),
        //   response: annotationData?.assignedUsers?.length,
        // },
        // annotationData.assignedUsers.map((userId) => ({
        //   addAnnotator: userId.id,
        //   response: "",
        // })),
      };

      reset(transformedDefaultValues);
    }
  }, [annotationData]);

  // get addAnnotations User
  const { data: annotationUserApiData } = useQuery({
    queryKey: ["organizationId", currentOrganizationId],
    queryFn: () =>
      axios.get(endpoints.annotation.annotationsUser(currentOrganizationId)),
    enabled: !!currentOrganizationId,
  });

  // get labelFieldscolumn data
  const {
    data: labelFieldsApiData,
    isLoading: labelLoading,
    fetchNextPage,
    isFetchingNextPage,
  } = useInfiniteQuery({
    queryKey: ["annotationLabels", dataset],
    queryFn: ({ pageParam }) =>
      axios.get(endpoints.annotation.annotationLabelText, {
        params: { page: pageParam, dataset, limit: 20 },
      }),
    getNextPageParam: (o) => (o.data.next ? o.data.current_page + 1 : null),
    initialPageParam: 1,
  });

  const options = useMemo(() => {
    const filtered =
      labelFieldsApiData?.pages.reduce(
        (acc, curr) => [...acc, ...curr.data.results],
        [],
      ) || [];
    return filtered.length > 0
      ? filtered
      : [{ label: "No options provided", value: "no", disabled: true }];
  }, [labelFieldsApiData]);

  const fieldColumnData = useMemo(() => {
    // Filter static data for originType === "OTHERS"
    const staticData = (
      staticFieldsApiData?.data?.result?.columnConfig || []
    ).filter(
      (column) =>
        column.originType === "OTHERS" || column.originType === "run_prompt",
    );

    const labelData = options;
    const annotationUserData = annotationUserApiData?.data?.results;

    // Fallback data if any of the API responses are unavailable
    // if (!staticData || !labelData || !annotationUserData) {
    //   return [
    //     {
    //       staticFieldColumn: [
    //         { value: "", label: "label", required: false },
    //       ],
    //       labelFieldColumn: [{ value: "123", label: "label", required: false }],
    //       annotationUserColumn: [
    //         { value: "123", label: "label", required: false },
    //       ],
    //     },
    //   ];
    // }

    // Map static fields with additional fields
    const mappedStaticData =
      staticData?.map(({ id, name, ...rest }) => ({
        value: id,
        label: name,
        ...rest, // Include all other fields from the original data
      })) || [];

    // Map label fields with additional fields
    const mappedLabelData =
      labelData?.map(({ id, name, ...rest }) => ({
        value: id,
        label: name,
        ...rest, // Include all other fields from the original data
      })) || [];

    // Map annotation users with additional fields
    const mappedAnnoUserDataForm =
      annotationUserData?.map(({ id, name }) => ({
        id: id,
        label: name,
        img: "https://cdn-icons-png.flaticon.com/512/149/149071.png",
      })) || [];

    // Map annotation users with additional fields
    const mappedAnnoUserData =
      annotationUserData?.map(({ id, name, ...rest }) => ({
        value: id,
        label: name,
        ...rest, // Include all other fields from the original data
      })) || [];

    // Update state with processed data (if needed elsewhere)
    setColumnData((prev) => [
      ...prev,
      {
        staticFieldColumn: mappedStaticData,
        labelFieldColumn: mappedLabelData,
        annotationUserColumn: mappedAnnoUserData,
      },
    ]);

    // Return processed data
    return [
      { staticFieldColumn: mappedStaticData },
      { labelFieldColumn: mappedLabelData },
      { annotationUserColumn: mappedAnnoUserData },
      { mappedAnnoUserDataForm: mappedAnnoUserDataForm },
    ];
  }, [staticFieldsApiData, options, annotationUserApiData]);

  // Annotation Labels added
  const { mutate: handleLabelsAdd } = useMutation({
    mutationFn: (payload) =>
      axios.post(endpoints.annotation.annotationLabelText, payload),
    onSuccess: (data) => {
      const newlabel = data?.data;
      enqueueSnackbar("Labels created successfully", {
        variant: "success",
      });
      queryClient.invalidateQueries({
        queryKey: ["annotationLabels"],
      });
      if (selectedLabelIndex !== null && newlabel) {
        labelUpdate(selectedLabelIndex, {
          labelName: newlabel?.id,
          assign: "",
        });
        trigger(`labelFields.${selectedLabelIndex}.labelName`);
      }
    },
    onError: (d) => {
      logger.error("onerror ", d);
    },
  });

  const handleLabels = (payload) => {
    if (
      payload.type == "text" ||
      payload.type === "numeric" ||
      payload.type === "categorical"
    ) {
      const data = {
        ...payload,
        organization: currentOrganizationId,
      };
      handleLabelsAdd(data);
    }
  };

  // update annotation
  const handleAnnotationUpdateCall = (data) => {
    if (!annotationData?.id) {
      enqueueSnackbar("Annotation ID is missing. Cannot update.", {
        variant: "error",
      });
      return;
    }

    const payload = {
      name: data?.name,
      dataset: dataset,
      // labels: data?.labelFields?.map((field) => field?.label) || [],
      labels: labelWatch?.map(({ labelName, assign }) => ({
        id: labelName,
        required: assign === "optional" ? false : true,
      })),
      assigned_users:
        data?.annotatorFields
          ?.flatMap((field) => field?.addAnnotator)
          ?.filter((value) => value !== "") || "",
      static_fields:
        data?.staticFields?.map((field) => ({
          column_id: field?.column || "",
          type: field?.type || "plain_text",
          view: field?.view || "default_collapsed",
        })) || [],
      response_fields:
        data?.responseFields?.map((field) => ({
          column_id: field?.column || "",
          type: field?.type || "plain_text",
          edit: field?.edit || "editable",
          view: field?.view || "default_collapsed",
        })) || [],
      responses: data?.responses || 1,
    };

    handleAnnotationUpdate(payload);
  };

  // Form Submission
  const onSubmit = (data = {}) => {
    handleAnnotationUpdateCall(data);
  };

  // Annotations PUT
  const { mutate: handleAnnotationUpdate } = useMutation({
    mutationFn: (payload) =>
      axios.put(
        `${endpoints.annotation.putAnnotationById}${annotationId}/`,
        payload,
      ),
    onSuccess: () => {
      enqueueSnackbar("Annotation updated successfully", {
        variant: "success",
      });
      queryClient.invalidateQueries({
        queryKey: [`preview-annotation`],
      });
      handleSetRenderAnnotationTable();
      onClose();
    },
  });

  if (staticLoading || labelLoading) {
    return <AnnotationsModalSkeleton />;
  }

  return (
    <Box
      sx={{
        display: "flex",
        height: "100%",
        justifyContent: "flex-end",
        width: "100%",
      }}
    >
      <AnnotationsMainForm
        formHandle={formHandle}
        control={control}
        onHandleSubmit={handleSubmit}
        isWatch={watch}
        onSubmit={onSubmit}
        columnData={fieldColumnData}
        handleLabels={handleLabels}
        annotationId={annotationId}
        setSelectedLabelIndex={setSelectedLabelIndex}
        fetchNextPage={fetchNextPage}
        isFetchingNextPage={isFetchingNextPage}
      />
    </Box>
  );
};

ConfigureAnnotationsForm.propTypes = {
  onClose: PropTypes.func,
  handleSetRenderAnnotationTable: PropTypes.any,
  annotationId: PropTypes.string,
};

const ConfigureAnnotations = ({
  open,
  onClose,
  annotationId,
  handleSetRenderAnnotationTable,
}) => {
  const covered = useDrawerCovered(open);
  return (
    <Drawer
      anchor="right"
      open={open}
      sx={coveredDrawerSx(covered)}
      // onClose={onClose}
      PaperProps={{
        sx: {
          height: "100vh",
          position: "fixed",
          zIndex: 99,
          borderRadius: "8px",
          backgroundColor: "background.paper",
          width: "640px",
        },
      }}
      ModalProps={{
        BackdropProps: {
          style: { backgroundColor: "transparent" },
        },
      }}
    >
      <IconButton
        onClick={onClose}
        sx={{ position: "absolute", top: "12px", right: "12px" }}
      >
        <Iconify icon="solar:close-linear" />
      </IconButton>
      <ConfigureAnnotationsForm
        onClose={onClose}
        handleSetRenderAnnotationTable={handleSetRenderAnnotationTable}
        annotationId={annotationId}
      />
    </Drawer>
  );
};

ConfigureAnnotations.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  annotationId: PropTypes.string.isRequired,
  handleSetRenderAnnotationTable: PropTypes.any,
};

export default ConfigureAnnotations;
