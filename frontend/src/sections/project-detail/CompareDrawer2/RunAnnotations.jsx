import { Box, Button, styled, Typography, useTheme } from "@mui/material";
import PropTypes from "prop-types";
import React, { useEffect, useMemo, useState } from "react";
import Iconify from "src/components/iconify";
import AddAnnotationsDrawer from "src/components/traceDetailDrawer/add-annotations-drawer";
import { useForm } from "react-hook-form";
import _ from "lodash";
import { Events, trackEvent } from "src/utils/Mixpanel";
import { useMutation, useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { enqueueSnackbar } from "notistack";
import { AgGridReact } from "ag-grid-react";
import { useAgThemeWith } from "src/hooks/use-ag-theme";
import { AG_THEME_OVERRIDES } from "src/theme/ag-theme";
import { ShowComponent } from "src/components/show";
import { commonBorder } from "src/sections/experiment-detail/ExperimentData/Common";
import { useAuthContext } from "src/auth/hooks";
import { useParams } from "react-router";

import AnnotateRunDrawer from "./AnnotateRunDrawer";
import { useAnnotationsColumnDefs } from "./CompareHelper";

const DEFAULT_COL_DEF = {
  lockVisible: true,
  sortable: true,
  filter: false,
  resizable: true,
  minWidth: 150,
  suppressHeaderMenuButton: true,
  suppressHeaderContextMenu: true,
};

const Section = styled(Box)(() => ({
  padding: "8px",
}));

const RunAnnotations = ({ traceData }) => {
  const theme = useTheme();
  const agTheme = useAgThemeWith(AG_THEME_OVERRIDES.noHeaderBorder);
  const { user } = useAuthContext();
  const { projectId } = useParams();
  const [isAddAnnotationOpen, setIsAddAnnotationOpen] = useState(false);
  const traceId = traceData?.id;
  const [open, setOpen] = useState(false);
  const AnnotationsColumnDefs = useAnnotationsColumnDefs();

  const { data: labelsData } = useQuery({
    queryKey: ["project-annotations-labels", projectId],
    queryFn: () =>
      axios.get(endpoints.project.getAnnotationLabels(), {
        params: { project_id: projectId },
      }),
    select: (data) => data?.data?.results,
  });

  const { data: valuesData, refetch: refetchAnnotationValues } = useQuery({
    queryKey: ["trace-annotation-values", traceId],
    queryFn: () =>
      axios.get(endpoints.project.getAnnotationsForSpanId(), {
        params: {
          trace_id: traceId,
          annotators: JSON.stringify([user?.id]),
        },
      }),
    enabled: !!traceId && !!user?.id,
    select: (data) => data?.data?.result?.annotations || [],
  });

  const rows = useMemo(() => {
    if (!valuesData) {
      return [];
    }

    return valuesData
      .filter(
        (value) =>
          !(
            Array.isArray(value.annotationValue) &&
            value.annotationValue.length === 0
          ),
      )
      .map((value) => ({
        id: value.annotationLabelId,
        annotationName: value.annotationLabelName,
        type: value.annotationType,
        value: value.annotationValue,
        updatedBy: value.annotator,
        settings: value.settings,
      }));
  }, [valuesData]);

  const hasAnnotationValues = useMemo(() => {
    return rows.length > 0;
  }, [rows]);

  const getDefaultValues = () => {
    const obj = {};
    valuesData?.forEach((value) => {
      obj[value.annotationLabelId] = value.annotationValue || "";
    });
    return obj;
  };

  const { control, handleSubmit, reset } = useForm({
    defaultValues: getDefaultValues(),
  });

  useEffect(() => {
    reset(getDefaultValues());
  }, [labelsData, valuesData]);

  const handleCancel = () => {
    reset(getDefaultValues());
    setOpen(false);
  };

  const onSubmit = (data) => {
    //@ts-ignore
    addAnnotationValues({
      trace_id: traceId,
      annotation_values: Object.fromEntries(
        Object.entries(data).filter(([_, value]) => value !== ""),
      ),
    });
  };

  const { mutate: addAnnotationValues } = useMutation({
    mutationFn: (data) =>
      axios.post(endpoints.project.addAnnotationValues(), data),
    onSuccess: () => {
      enqueueSnackbar(
        `Annotations has been added for ${traceData?.projectVersionName}`,
        {
          variant: "success",
        },
      );
      setOpen(false);
      refetchAnnotationValues();
    },
  });

  const hasLabels = useMemo(() => {
    return labelsData?.length > 0;
  }, [labelsData]);

  const hasLabelsWithoutValues = useMemo(() => {
    return hasLabels && !hasAnnotationValues;
  }, [hasLabels, hasAnnotationValues]);

  return (
    <Box
      sx={{
        paddingY: theme.spacing(1),
        height: "100%",
        display: "flex",
        flexDirection: "column",
        minHeight: 0,
        borderRight: commonBorder.border,
        borderColor: commonBorder.borderColor,
      }}
    >
      <Section
        sx={{
          flexShrink: 0,
          gap: theme.spacing(1.75),
          paddingLeft: theme.spacing(2),
          borderBottom: commonBorder.border,
          borderColor: commonBorder.borderColor,
        }}
      >
        <Typography fontWeight={700}>
          {traceData?.projectVersionName}
        </Typography>
      </Section>
      <Box
        sx={{
          flex: 1,
          minHeight: 0,
          overflow: "hidden",
          display: "flex",
          flexDirection: "column",
        }}
      >
        <ShowComponent condition={hasAnnotationValues}>
          <>
            <Box
              sx={{
                flexShrink: 0,
                display: "flex",
                gap: theme.spacing(1),
                mt: theme.spacing(1),
                paddingX: theme.spacing(2),
                flexWrap: "nowrap",
                overflowX: "auto",
                "&::-webkit-scrollbar": {
                  height: "3px",
                },
                "&::-webkit-scrollbar-thumb": {
                  backgroundColor: "rgba(0, 0, 0, 0.3)",
                  borderRadius: "3px",
                },
              }}
            >
              <Button
                aria-label="create-annotation-label"
                variant="outlined"
                size="small"
                color="primary"
                startIcon={
                  <Iconify icon="material-symbols:new-label-outline-rounded" />
                }
                onClick={() => {
                  setIsAddAnnotationOpen(true);
                  trackEvent(Events.modifyAnnotationsClicked);
                }}
                sx={{
                  border: commonBorder.border,
                  borderRadius: commonBorder.borderRadius,
                  whiteSpace: "nowrap",
                  flexShrink: 0,
                }}
              >
                Configure Label
              </Button>

              <Button
                aria-label="annotate-run"
                variant="outlined"
                size="small"
                color="primary"
                startIcon={<Iconify icon="ri:edit-line" />}
                onClick={() => setOpen(true)}
                sx={{
                  border: commonBorder.border,
                  borderRadius: commonBorder.borderRadius,
                  whiteSpace: "nowrap",
                  flexShrink: 0,
                }}
              >
                Annotate
              </Button>
            </Box>

            <Box
              className="ag-theme-quartz"
              sx={{
                mt: theme.spacing(1),
                px: theme.spacing(2),
                flex: 1,
                minHeight: 0,
                mb: "110px",
                overflow: "hidden",
                "& ::-webkit-scrollbar": {
                  width: "5px",
                  height: "6px",
                },
                "& ::-webkit-scrollbar-thumb": {
                  backgroundColor: "rgba(0, 0, 0, 0.3)",
                  borderRadius: "3px",
                },
                "& ::-webkit-scrollbar-track": {
                  background: "transparent",
                },
              }}
            >
              <AgGridReact
                theme={agTheme}
                columnDefs={AnnotationsColumnDefs}
                defaultColDef={DEFAULT_COL_DEF}
                suppressRowClickSelection={true}
                domLayout="normal"
                paginationPageSizeSelector={false}
                rowData={rows}
                onGridReady={(params) => params.api.sizeColumnsToFit()}
                rowStyle={{ cursor: "pointer" }}
              />
            </Box>
          </>
        </ShowComponent>
        <ShowComponent condition={hasLabelsWithoutValues}>
          <Box
            sx={{
              flex: 1,
              display: "flex",
              flexDirection: "column",
              justifyContent: "center",
              alignItems: "center",
              textAlign: "center",
              gap: theme.spacing(2),
              padding: theme.spacing(2),
            }}
          >
            <Typography sx={{ fontSize: 14, fontWeight: 400 }}>
              Labels have been created but no values added yet. Configure
              annotations or add values.
            </Typography>

            <Box
              sx={{
                display: "flex",
                gap: theme.spacing(1),
                flexWrap: "nowrap",
                overflowX: "auto",
                paddingBottom: theme.spacing(1),
                width: "100%",
                justifyContent: "center",
              }}
            >
              <Button
                aria-label="create-annotation-label"
                variant="outlined"
                size="small"
                color="primary"
                startIcon={
                  <Iconify icon="material-symbols:new-label-outline-rounded" />
                }
                onClick={() => {
                  setIsAddAnnotationOpen(true);
                  trackEvent(Events.modifyAnnotationsClicked);
                }}
                sx={{
                  border: commonBorder.border,
                  borderRadius: commonBorder.borderRadius,
                  whiteSpace: "nowrap",
                  flexShrink: 0,
                }}
              >
                Configure Annotations
              </Button>
              <Button
                aria-label="annotate-run"
                variant="outlined"
                size="small"
                color="primary"
                startIcon={<Iconify icon="ri:edit-line" />}
                onClick={() => setOpen(true)}
                sx={{
                  border: commonBorder.border,
                  borderRadius: commonBorder.borderRadius,
                  whiteSpace: "nowrap",
                  flexShrink: 0,
                }}
              >
                Annotate
              </Button>
            </Box>
          </Box>
        </ShowComponent>
        <ShowComponent
          condition={!hasAnnotationValues && labelsData?.length === 0}
        >
          <Box
            sx={{
              flex: 1,
              display: "flex",
              flexDirection: "column",
              justifyContent: "center",
              alignItems: "center",
              textAlign: "center",
              gap: theme.spacing(2),
              padding: theme.spacing(2),
            }}
          >
            <Typography sx={{ fontSize: 14, fontWeight: 400 }}>
              No Annotations have been added
            </Typography>

            <Box
              sx={{
                display: "flex",
                gap: theme.spacing(1),
                flexWrap: "nowrap",
                overflowX: "auto",
                paddingBottom: theme.spacing(1),
                width: "100%",
                justifyContent: "center",
              }}
            >
              <Button
                aria-label="create-annotation-label"
                variant="outlined"
                size="small"
                color="primary"
                startIcon={
                  <Iconify icon="material-symbols:new-label-outline-rounded" />
                }
                onClick={() => {
                  setIsAddAnnotationOpen(true);
                  trackEvent(Events.modifyAnnotationsClicked);
                }}
                sx={{
                  border: commonBorder.border,
                  borderRadius: commonBorder.borderRadius,
                  whiteSpace: "nowrap",
                  flexShrink: 0,
                }}
              >
                Create Label
              </Button>
            </Box>
          </Box>
        </ShowComponent>
      </Box>

      {/* Drawers */}
      <AnnotateRunDrawer
        open={open}
        onClose={handleCancel}
        annotationLabels={labelsData}
        control={control}
        runName={traceData?.projectVersionName}
        observationType={
          traceData?.observationSpans[0]?.observationSpan?.observationType
        }
        observationName={traceData?.observationSpans[0]?.observationSpan?.name}
        onSubmit={handleSubmit(onSubmit)}
      />
      <AddAnnotationsDrawer
        open={isAddAnnotationOpen}
        onClose={() => setIsAddAnnotationOpen(false)}
        projectId={projectId}
        onAnnotateClick={() => {
          if (!labelsData?.length) {
            enqueueSnackbar("Please create an annotation label first", {
              variant: "warning",
            });
            return;
          }
          setIsAddAnnotationOpen(false);
          setOpen(true);
        }}
      />
    </Box>
  );
};

RunAnnotations.propTypes = {
  traceData: PropTypes.object,
};

export default RunAnnotations;
