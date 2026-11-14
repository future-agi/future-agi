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
import { trackEvent, Events } from "src/utils/Mixpanel";
import { useDevelopDatasetList } from "src/api/develop/develop-detail";
import logger from "src/utils/logger";
import { useRunAnnotationsStore } from "../states";
import { coveredDrawerSx, useDrawerCovered } from "src/hooks/use-drawer-stack";

const RunAnnotationsForm = ({ onClose, setRenderAnnotationTable }) => {
  const { dataset } = useParams();
  const { data } = useDevelopDatasetList();

  const currentDataset = data?.find((d) => d.datasetId === dataset)?.name;

  const { currentOrganizationId } = useOrganization();
  const [selectedLabelIndex, setSelectedLabelIndex] = useState(null);

  const {
    control,
    watch,
    handleSubmit,
    formState: { errors },
    reset,
    setValue,
    trigger,
  } = useForm({
    resolver: zodResolver(AnnotationFormSchema),
    defaultValues: {
      name: "",
      staticFields: [{ column: "", type: "", view: "" }],
      responseFields: [{ column: "", type: "", edit: "", view: "" }],
      labelFields: [{ labelName: "", assign: "" }],
      annotatorFields: [{ addAnnotator: [], response: "" }],
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
    labelItems: "labelFields",
    annotatorFields: annotatorFields,
    annotatorAppend: annotatorAppend,
    annotatorRemove: annotatorRemove,
    annotatorItems: "annotatorFields",
    setValue: setValue,
    reset: reset,
    errors: errors,
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

  // get addAnnotations User
  const { data: annotationUserApiData } = useQuery({
    queryKey: ["organizationId", currentOrganizationId],
    queryFn: () =>
      axios.get(endpoints.annotation.annotationsUser(currentOrganizationId), {
        params: { is_active: true },
      }),
    enabled: !!currentOrganizationId,
  });

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
    if (!staticData || !labelData || !annotationUserData) {
      return [
        {
          staticFieldColumn: [],
          labelFieldColumn: [],
          annotationUserColumn: [],
        },
      ];
    }

    // Map static fields with additional fields
    const mappedStaticData = staticData.map(({ id, name, ...rest }) => ({
      value: id,
      label: name,
      ...rest,
    }));

    // Map label fields with additional fields
    const mappedLabelData = labelData.map(({ id, name, ...rest }) => ({
      value: id,
      label: name,
      ...rest,
    }));

    // Map annotation users with additional fields
    const mappedAnnoUserDataForm = annotationUserData.map(({ id, name }) => ({
      id: id,
      label: name,
      img: "https://cdn-icons-png.flaticon.com/512/149/149071.png",
    }));

    // Map annotation users with additional fields
    const mappedAnnoUserData = annotationUserData.map(
      ({ id, name, ...rest }) => ({
        value: id,
        label: name,
        ...rest,
      }),
    );

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

  const onSubmit = (data = {}) => {
    handleAnnotation(data);
    formHandle.reset();
    onClose();
  };

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

  // Annotations POST
  const { mutate: handleAnnotationAdd, isPending: isAnnotationTesting } =
    useMutation({
      mutationFn: (payload) =>
        axios.post(endpoints.annotation.createNewAnnotation, payload),
      onSuccess: () => {
        enqueueSnackbar("New view created successfully", {
          variant: "success",
        });
        setRenderAnnotationTable((prev) => !prev);
      },
    });

  const handleAnnotation = (data) => {
    const payload = {
      name: data?.name,
      dataset: dataset,
      labels: labelWatch?.map(({ labelName: label, assign }) => ({
        id: label,
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

    // Call the function to handle the payload (if applicable)
    handleAnnotationAdd(payload);
    trackEvent(Events.annNewViewCreate, {
      name: data?.name,
      dataset: currentDataset,
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
      labels: labelWatch?.map(({ labelName: label, assign }) => ({
        id: label,
        required: assign === "optional" ? false : true,
      })),
      assigned_users:
        data?.annotatorFields
          ?.flatMap((field) => field?.addAnnotator)
          ?.filter((value) => value !== "") || "",
    });
  };

  const handleLabels = (payload) => {
    handleLabelsAdd(payload);
  };

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
        loading={isAnnotationTesting}
        onSubmit={onSubmit}
        columnData={fieldColumnData}
        handleLabels={handleLabels}
        setSelectedLabelIndex={setSelectedLabelIndex}
        fetchNextPage={fetchNextPage}
        isFetchingNextPage={isFetchingNextPage}
      />
    </Box>
  );
};

RunAnnotationsForm.propTypes = {
  onClose: PropTypes.func,
  setRenderAnnotationTable: PropTypes.any,
};

const RunAnnotations = ({ setRenderAnnotationTable }) => {
  // Using individual store
  const { openRunAnnotations, setOpenRunAnnotations } =
    useRunAnnotationsStore();

  const onClose = () => {
    setOpenRunAnnotations(false);
  };

  const covered = useDrawerCovered(openRunAnnotations);
  return (
    <Drawer
      anchor="right"
      open={openRunAnnotations}
      sx={coveredDrawerSx(covered)}
      variant="persistent"
      PaperProps={{
        sx: {
          height: "100vh",
          position: "fixed",
          zIndex: 2,
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
      <RunAnnotationsForm
        onClose={onClose}
        setRenderAnnotationTable={setRenderAnnotationTable}
      />
    </Drawer>
  );
};

RunAnnotations.propTypes = {
  setRenderAnnotationTable: PropTypes.any,
};

export default RunAnnotations;
