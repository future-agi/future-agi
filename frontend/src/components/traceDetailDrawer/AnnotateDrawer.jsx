import {
  Box,
  Button,
  Divider,
  Drawer,
  IconButton,
  LinearProgress,
  Link,
  Typography,
} from "@mui/material";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useDeleteAnnotationLabel } from "./useDeleteAnnotationLabel";
import PropTypes from "prop-types";
import React, { useMemo, useState } from "react";
import { useSelectedNode } from "./useSelectedNode";
import axios, { endpoints } from "src/utils/axios";
import Iconify from "src/components/iconify";
import { useForm } from "react-hook-form";
import AnnotationFieldWrapper from "src/sections/develop-detail/Annotations/CreateEditLabel/AnnotationFieldWrapper";
import { useAuthContext } from "src/auth/hooks";
import { ShowComponent } from "src/components/show";
import { transformLabelObject } from "src/sections/develop-detail/Annotations/CreateEditLabel/common";
import { useAnnotationLabels } from "src/api/annotation/annotation";
import { useScrollEnd } from "src/hooks/use-scroll-end";
import AnnotationFieldSkeleton from "src/sections/develop-detail/Annotations/CreateEditLabel/AnnotationFieldSkeleton";
import FormTextFieldV2 from "../FormTextField/FormTextFieldV2";
import { createFormSchema } from "./validation/validation";
import { zodResolver } from "@hookform/resolvers/zod";
import CreateEditLabel from "src/sections/develop-detail/Annotations/CreateEditLabel/CreateEditLabel";
import { PropertyName } from "src/utils/Mixpanel";
import { Events } from "src/utils/Mixpanel";
import { trackEvent } from "src/utils/Mixpanel/mixpanel";
import ConfirmAnnotationDelete from "./ConfirmAnnotationDelete";
import SvgColor from "../svg-color";

const AnnotateDrawerChild = ({
  onClose,
  onSubmit,
  defaultValues,
  projectId,
  onAnnotationChanges = null,
  showCreateLabel = false,
}) => {
  const [openCreateEditLabel, setOpenCreateEditLabel] = useState(null);
  const { labels, fetchNextPage, isPending, isFetchingNextPage } =
    useAnnotationLabels(projectId);
  const [deleteLabelId, setDeleteLabelId] = useState(null);
  const formSchema = useMemo(() => {
    return createFormSchema(labels, defaultValues);
  }, [labels, defaultValues]);
  const { mutate: deleteAnnotationLabel, isPending: isDeletingLabel } =
    useDeleteAnnotationLabel({
      onSuccess: () => {
        queryClient.invalidateQueries({ queryKey: ["callLogs"] });
        setDeleteLabelId(null);
        onAnnotationChanges?.();
      },
    });

  const {
    control,
    handleSubmit,
    formState: { errors, isDirty },
  } = useForm({
    defaultValues,
    resolver: zodResolver(formSchema),
    mode: "onChange",
  });
  const scrollContainer = useScrollEnd(() => {
    if (isPending || isFetchingNextPage) return;
    fetchNextPage();
  }, [fetchNextPage, isFetchingNextPage, isPending]);
  const queryClient = useQueryClient();

  return (
    <>
      <CreateEditLabel
        open={openCreateEditLabel}
        onClose={() => {
          setOpenCreateEditLabel(false);
        }}
        projectId={projectId}
        onSuccess={(_, variables) => {
          queryClient.invalidateQueries({
            queryKey: ["project-annotations-labels", projectId],
          });
          queryClient.invalidateQueries({
            queryKey: ["project-annotations-labels-paginated", projectId],
          });
          trackEvent(Events.projectAnnotation[variables.type], {
            [PropertyName.click]: variables,
          });
          setOpenCreateEditLabel(null);
          onAnnotationChanges?.();
        }}
        editData={
          typeof openCreateEditLabel === "object" &&
          openCreateEditLabel !== null
            ? transformLabelObject(openCreateEditLabel)
            : null
        }
      />
      <Box
        sx={{
          overflowY: "auto",
          flex: 1,
        }}
        ref={scrollContainer}
      >
        {labels?.map((item, index) => {
          const transformedItem = transformLabelObject(item);
          return (
            <Box key={transformedItem.id} mb={2}>
              <AnnotationFieldWrapper
                index={index}
                labelName={transformedItem.name}
                type={transformedItem.type}
                settings={transformedItem.settings}
                control={control}
                fieldName={transformedItem.id}
                disableHotkeys={true}
                defaultOpen={true}
                error={errors[transformedItem.id]}
                showActionButtons={true}
                onEditLabelClick={() => setOpenCreateEditLabel(transformedItem)}
                onDeleteLabelClick={() => {
                  setDeleteLabelId(transformedItem.id);
                }}
              />
            </Box>
          );
        })}
        <ShowComponent
          condition={!isPending && !isFetchingNextPage && labels.length !== 0}
        >
          <FormTextFieldV2
            control={control}
            fieldName="notes"
            size="small"
            label="Notes (optional)"
            fullWidth
            multiline
            rows={4}
            placeholder="Add optional notes here"
            sx={{
              mt: 1,
            }}
          />
        </ShowComponent>
        <ShowComponent condition={isFetchingNextPage || isPending}>
          <AnnotationFieldSkeleton hideButtons />
          <AnnotationFieldSkeleton hideButtons />
          <AnnotationFieldSkeleton hideButtons />
        </ShowComponent>
        <ShowComponent condition={showCreateLabel}>
          <Button
            sx={{ mt: 2 }}
            startIcon={
              <SvgColor
                sx={{
                  height: 16,
                  width: 16,
                  color: "inherit",
                }}
                src={"/assets/icons/ic_add.svg"}
              />
            }
            variant="outlined"
            color="primary"
            onClick={() => setOpenCreateEditLabel(true)}
          >
            Create Label
          </Button>
        </ShowComponent>
      </Box>

      <Box sx={{ display: "flex", gap: 2, pt: 2, justifyContent: "flex-end" }}>
        <Button
          onClick={onClose}
          variant="outlined"
          sx={{ padding: "10px 50px" }}
        >
          Cancel
        </Button>
        <Button
          color="primary"
          variant="contained"
          sx={{ padding: "10px 50px" }}
          onClick={() => {
            if (!isDirty) {
              onClose();
            } else {
              handleSubmit(onSubmit)();
            }
          }}
        >
          Annotate
        </Button>
      </Box>
      <ConfirmAnnotationDelete
        open={Boolean(deleteLabelId)}
        onClose={() => setDeleteLabelId(null)}
        onConfirm={() => deleteAnnotationLabel(deleteLabelId)}
        loading={isDeletingLabel}
      />
    </>
  );
};

AnnotateDrawerChild.propTypes = {
  onClose: PropTypes.func,
  onSubmit: PropTypes.func,
  defaultValues: PropTypes.object,
  projectId: PropTypes.string,
  showCreateLabel: PropTypes.bool,
  onAnnotationChanges: PropTypes.func,
};

const AnnotateDrawer = ({
  open,
  onClose,
  runName,
  observationType,
  observationName,
  onSubmit,
  projectId,
  voiceObserveSpanId = null,
  listSpanId = null,
}) => {
  const { selectedNode } = useSelectedNode();
  const { user } = useAuthContext();
  const updatedSpanId =
    listSpanId ?? (selectedNode ? selectedNode?.id : voiceObserveSpanId);
  const { data: spanAnnotationData, isPending } = useQuery({
    queryKey: ["span-annotation", updatedSpanId, [user?.id], "contains"],
    queryFn: () => {
      return axios.get(endpoints.project.getAnnotationsForSpanId(), {
        params: {
          observation_span_id: updatedSpanId,
          annotators: JSON.stringify([user?.id]),
          exclude_annotators: JSON.stringify([]),
        },
      });
    },
    enabled: open,
    select: (data) => data?.data?.result,
    refetchOnWindowFocus: true,
  });

  const formDefaultValues = useMemo(() => {
    const newAnnotations = spanAnnotationData?.annotations.reduce(
      (acc, curr) => {
        acc[curr.annotationLabelId] = curr.annotationValue;
        return acc;
      },
      {},
    );

    return {
      ...newAnnotations,
      notes: spanAnnotationData?.notes?.[0]?.notes ?? "",
    };
  }, [spanAnnotationData]);
  return (
    <Drawer
      anchor="right"
      open={open}
      onClose={onClose}
      BackdropProps={{ invisible: true }}
      PaperProps={{
        sx: { width: "40%", p: 2, display: "flex", flexDirection: "column" },
      }}
    >
      <ShowComponent condition={!isPending}>
        <Box
          sx={{
            display: "flex",
            flexDirection: "column",
            flex: 1,
            overflow: "hidden",
          }}
        >
          <ShowComponent condition={voiceObserveSpanId === null}>
            <Box
              sx={{
                display: "flex",
                justifyContent: "space-between",
              }}
            >
              <Typography variant="h6">
                Annotate{" "}
                <ShowComponent condition={Boolean(runName)}>
                  {`- ${runName}`}
                </ShowComponent>
              </Typography>
              <IconButton onClick={onClose}>
                <Iconify icon="mingcute:close-line" width={20} />
              </IconButton>
            </Box>

            <Typography
              sx={{
                mb: 1,
                fontSize: "16px",
                fontWeight: 400,
              }}
            >
              {observationType} - {observationName}
            </Typography>
          </ShowComponent>
          <ShowComponent condition={Boolean(voiceObserveSpanId)}>
            <Box
              sx={{
                display: "flex",
                flexDirection: "row",
                alignItems: "center",
                justifyContent: "space-between",
                mb: 1,
              }}
            >
              <Typography typography={"m2"} fontWeight={"fontWeightBold"}>
                Label Configuration
              </Typography>
              <Box
                sx={{
                  display: "flex",
                  flexDirection: "row",
                  alignItems: "center",
                  gap: 1,
                }}
              >
                <Link
                  href="https://docs.futureagi.com/docs/observe/features/manual-tracing/annotating-using-api"
                  underline="always"
                  color="blue.500"
                  target="_blank"
                  rel="noopener noreferrer"
                  fontWeight="fontWeightMedium"
                >
                  Learn more
                </Link>
                <IconButton onClick={onClose}>
                  <Iconify icon="mingcute:close-line" width={20} />
                </IconButton>
              </Box>
            </Box>
            <Divider />
          </ShowComponent>

          <ShowComponent condition={isPending}>
            <LinearProgress />
          </ShowComponent>
          <ShowComponent condition={!isPending}>
            <AnnotateDrawerChild
              onClose={onClose}
              onSubmit={onSubmit}
              defaultValues={formDefaultValues}
              projectId={projectId}
              showCreateLabel={Boolean(voiceObserveSpanId)}
            />
          </ShowComponent>
        </Box>
      </ShowComponent>
    </Drawer>
  );
};

AnnotateDrawer.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  runName: PropTypes.string,
  observationType: PropTypes.string,
  observationName: PropTypes.string,
  onSubmit: PropTypes.func,
  projectId: PropTypes.string,
  voiceObserveSpanId: PropTypes.string,
  listSpanId: PropTypes.string,
};

export default AnnotateDrawer;
