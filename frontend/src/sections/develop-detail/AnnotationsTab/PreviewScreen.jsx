import { LoadingButton } from "@mui/lab";
import { Box, Button, Grid, IconButton, Typography } from "@mui/material";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";
import React, { useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "react-router";
import CustomAccordion from "src/components/Accordion/CustomAccordion";
import EditableAccordion from "src/components/Accordion/EditableAccordion";
import EditableAccordionCategorical from "src/components/Accordion/EditableAccordionCategorical";
import EditableAccordionSlider from "src/components/Accordion/EditableAccordionSlider";
import EditableAccordionText from "src/components/Accordion/EditableAccordionText";
import Iconify from "src/components/iconify";
import axios, { endpoints } from "src/utils/axios";
import AnnotationsStatusLine from "./AnnotationStatusLine";
import PreviewSkeleton from "../Common/Skeletons/PreviewSkeleton";
import { useSearchParams } from "src/routes/hooks";
import { useHotkeys } from "react-hotkeys-hook";
import logger from "src/utils/logger";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import { normalizeAnnotationPreviewData } from "./annotationPreviewShape";

const MemoizedAccordion = React.memo(CustomAccordion);
const MemoizedEditAccordion = React.memo(EditableAccordion);

export default function PreviewScreen() {
  const { id: annotationId } = useParams();
  const queryClient = useQueryClient();
  const [errors, setErrors] = useState([]);
  const [expandedAccordion, setExpandedAccordion] = useState(null);

  const [queryParamState, setQueryParamState] = useSearchParams({
    annotationIndex: 0,
  });
  const [, setSavedRows] = useState(new Set()); // Track saved rows
  const [updatedFields, setUpdatedFields] = useState(new Set());
  const [testValues, setTestValues] = useState({});
  const rowOrder = useMemo(() => {
    try {
      return parseInt(queryParamState["annotationIndex"]) || 0;
    } catch {
      return 0;
    }
  }, [queryParamState]);

  const startTimeRef = useRef(null); // stores when the timer started

  useEffect(() => {
    resetTimer();

    // Reset timer when page unloads
    window.addEventListener("beforeunload", resetTimer);

    return () => {
      window.removeEventListener("beforeunload", resetTimer);
    };
  }, []);

  const resetTimer = () => {
    startTimeRef.current = Date.now();
  };

  const getElapsedSeconds = () => {
    const now = Date.now();
    return Math.floor((now - (startTimeRef.current || 0)) / 1000);
  };

  const setRowOrder = (value) => {
    if (typeof value === "function") {
      setQueryParamState({ annotationIndex: value(rowOrder) });
    } else {
      setQueryParamState({ annotationIndex: value });
    }
  };

  const handleValueChange = (columnId, newValue, key = "value") => {
    setTestValues((prev) => ({
      ...prev,
      [columnId]: { ...prev?.[columnId], [key]: newValue },
    }));
    setUpdatedFields((prev) => new Set(prev).add(columnId));
  };

  // Query for fetching preview data
  const { isLoading, data: previewData } = useQuery({
    queryKey: [`preview-annotation`, rowOrder, annotationId],
    queryFn: async () => {
      const res = await axios.get(
        endpoints.annotation.annotateRow(annotationId),
        {
          params: {
            row_order: rowOrder,
          },
        },
      );
      return res.data;
    },

    enabled: !!annotationId && rowOrder >= 0,
  });
  const annotationData = useMemo(
    () => normalizeAnnotationPreviewData(previewData?.result?.data),
    [previewData],
  );
  const currentRowNumber = annotationData?.currentRowNumber;
  const totalRows = annotationData?.totalRows ?? 1;
  const firstRowOrder = annotationData?.firstRowOrder ?? 0;
  const lastRowOrder = annotationData?.lastRowOrder ?? 0;
  const labelsArray = annotationData?.label;
  const responseFieldsArray = annotationData?.responseFields;
  const nextRowOrder = annotationData?.nextRowOrder;
  const prevRowOrder = annotationData?.previousRowOrder;

  const toggleAccordion = (sno) => {
    setExpandedAccordion((prev) => (prev === sno ? null : sno));
  };

  useHotkeys(
    annotationData?.label
      ?.map((_, index) => [
        `meta+${index + 1}`,
        `cmd+${index + 1}`,
        `ctrl+${index + 1}`,
      ])
      .flat(),
    (e, handler) => {
      e.preventDefault();
      const pressedKey = handler.keys[0].match(/\d+/)?.[0];
      if (pressedKey) toggleAccordion(Number(pressedKey));
    },
    { preventDefault: true },
    [annotationData],
  );

  // Save annotation data
  const { mutate: updateAnnotationData, isPending: isUpdatingAnnotation } =
    useMutation({
      /**
       *
       * @param {Object} payload
       * @param {any} payload.labelValues
       * @param {any} payload.responseFieldValues
       *
       */
      mutationFn: (payload) =>
        axios.post(
          endpoints.annotation.updateAnnotation(annotationId),
          payload,
        ),
      onSuccess: (response) => {
        // Access the message from the response
        const successMessage =
          response?.data?.result?.message || "Annotation updated successfully";

        enqueueSnackbar(successMessage, {
          variant: "success",
        });

        // Add the current row to the savedRows set
        setSavedRows((prev) => new Set(prev).add(rowOrder));

        // Navigate to the next row if not the last row
        if (rowOrder + 1 < totalRows) {
          setRowOrder((prev) => prev + 1);
        }

        // Reset state for the new row
        setTestValues({});
        setUpdatedFields(new Set());

        // Invalidate preview data query to refetch updated data
        queryClient.invalidateQueries({
          queryKey: [`preview-annotation`, rowOrder, annotationId],
        });
        resetTimer();
      },
    });

  // Save and move to the next row
  const handleSave = () => {
    setErrors([]);
    const error = [];

    const labelValues = labelsArray
      ?.map((item) => {
        const value = testValues[item?.labelId]?.value || item?.cellValue;
        if (
          item.labelType === "text" &&
          value &&
          value.length < item?.labelSettings?.minLength
        ) {
          error.push({
            message: "Length less than minimum required length",
            labelId: item?.labelId,
          });
        }
        return {
          row_id: item?.rowId,
          label_id: item?.labelId,
          value: testValues[item?.labelId]?.value || item?.cellValue,
          column_id: item?.columnId,
          description: testValues[item?.labelId]?.description?.length
            ? testValues[item?.labelId]?.description
            : item?.cellDescription,
          time_taken: getElapsedSeconds(),
        };
      })
      ?.filter((item) => Boolean(item?.value));

    setErrors(error);
    if (error.length > 0) return;

    const responseFieldValues = responseFieldsArray
      ?.filter((item) => updatedFields.has(item?.columnId)) // Include only updated fields
      ?.map((item) => ({
        row_id: item?.rowId,
        column_id: item?.columnId,
        value: testValues[item?.columnId]?.value,
      }));

    resetTimer();

    if (labelValues?.length || responseFieldValues?.length) {
      updateAnnotationData({
        label_values: labelValues,
        response_field_values: responseFieldValues,
      });
    } else {
      enqueueSnackbar("No changes to save", { variant: "info" });
    }
  };

  // Reset annotation data
  const { mutate: resetAnnotationData, isPending: isResettingAnnotation } =
    useMutation({
      /**
       * @param {Object} payload
       * @param {string} payload.rowId
       */
      mutationFn: (payload) =>
        axios.post(endpoints.annotation.resetAnnotation(annotationId), payload),
      onSuccess: () => {
        enqueueSnackbar("Annotation reset successfully", {
          variant: "success",
        });
        trackEvent(Events.annPreviewReset, { [PropertyName.id]: annotationId });

        // Remove the current row from the savedRows set
        setSavedRows((prev) => {
          const updatedSet = new Set(prev);
          updatedSet.delete(rowOrder);
          return updatedSet;
        });

        // Reset state for the current row
        setTestValues({});
        setUpdatedFields(new Set());

        // Invalidate preview data query to refetch updated data
        queryClient.invalidateQueries({
          queryKey: [`preview-annotation`, rowOrder, annotationId],
        });
      },
    });

  // Handle reset action
  const handleResetAnnotation = () => {
    const rowId = annotationData?.label?.[0]?.rowId;
    if (rowId) {
      resetAnnotationData({ row_id: rowId });
    } else {
      enqueueSnackbar("No row to reset", { variant: "info" });
    }
  };

  // const handleSkip = () => {
  //   setSkippedRows((prev) => new Set(prev).add(rowOrder)); // Log the skipped row

  //   // Navigate to the next valid row
  //   navigateToRow(nextRowOrder);
  // };

  const navigateToRow = (rowOrder) => {
    // Skip over rows in `skippedRows` if not explicitly skipping
    // if (!isSkip) {
    //   while (skippedRows.has(rowOrder) && rowOrder < totalRows - 1) {
    //     rowOrder++;
    //   }
    // }
    trackEvent(Events.annPreviewSkipped);
    setRowOrder(rowOrder);
    resetForNewRow();
  };

  const annotationProgress = useMemo(() => {
    const summary = previewData?.result?.data?.summary;
    if (!summary)
      return {
        progress: 0,
        text: `0/0 Completed`,
      };
    return {
      progress: (summary?.completed / summary?.total) * 100,
      text: `${summary?.completed}/${summary?.total} Completed`,
    };
  }, [previewData]);

  const resetForNewRow = () => {
    setTestValues({});
    setUpdatedFields(new Set());
    setErrors([]);
    queryClient.invalidateQueries({
      queryKey: [`preview-annotation`, rowOrder, annotationId],
    });
  };

  if (isLoading || isUpdatingAnnotation || isResettingAnnotation) {
    return <PreviewSkeleton />;
  }

  return (
    <Box
      sx={{
        width: "100%",
        bgcolor: "background.paper",
        boxShadow: 24,
        height: "100%",
        overflow: "auto",
        marginTop: "10px",
        borderTop: 1,
        borderColor: "divider",
      }}
    >
      <Box height={"100%"}>
        <Grid container height={"100%"}>
          {/* Left Grid */}
          <Grid
            item
            xs={8}
            sx={{
              borderRight: "2px solid",
              borderColor: "divider",
              padding: "16px",
              // paddingRight: "10px",
              // paddingBottom: "24px",
            }}
          >
            <Box
              sx={{ display: "flex", flexDirection: " column", gap: "16px" }}
            >
              {annotationData?.staticFields?.map((item) => {
                return (
                  <MemoizedAccordion
                    key={item?.rowId + item?.columnId}
                    labelText={item?.columnName}
                    detailsText={item?.value}
                    defaultExpanded={item?.view === "default_open"}
                  />
                );
              })}
              {annotationData?.responseFields?.map((item) => {
                if (item?.edit !== "editable") {
                  return (
                    <MemoizedAccordion
                      key={item?.rowId + item?.columnId}
                      labelText={item?.columnName}
                      detailsText={item?.value}
                      defaultExpanded={item?.view === "default_open"}
                    />
                  );
                }
                return (
                  <MemoizedEditAccordion
                    key={item?.rowId + item?.columnId}
                    labelText={item?.columnName}
                    detailsText={
                      testValues[item?.columnId]?.value ?? item?.value
                    } // Use updated value or default
                    onDetailsChange={(newValue) => {
                      logger.debug(
                        `Updated value for ${item?.columnName}:`,
                        newValue,
                      );
                      handleValueChange(item?.columnId, newValue); // Track changes for the field
                    }}
                  />
                );
              })}
            </Box>
          </Grid>

          {/* Right Grid */}
          <Grid item xs={4} sx={{ padding: "16px", position: "relative" }}>
            <Box
              sx={{
                // paddingBottom: "50px",
                overflowY: "auto",
                height: "calc(100% - 100px)",
              }}
            >
              <Box
                display={"flex"}
                alignItems={"center"}
                justifyContent={"space-between"}
              >
                <Typography
                  sx={{
                    color: "text.secondary",
                    mb: 1,
                    cursor: "pointer",
                  }}
                  fontSize={14}
                  fontWeight={700}
                >
                  Annotations
                </Typography>
                <AnnotationsStatusLine value={annotationProgress || {}} />
              </Box>
              {/* Render Annotations */}
              <Box
                sx={{ display: "flex", flexDirection: " column", gap: "16px" }}
              >
                {annotationData?.label?.map((item, index) => {
                  const error = errors.find(
                    (temp) => temp.labelId === item?.labelId,
                  );
                  if (item) {
                    if (item?.labelType === "numeric") {
                      return (
                        <EditableAccordionSlider
                          key={item?.rowId + item?.labelId}
                          sliderText={item?.labelType}
                          sno={index + 1}
                          header={item?.labelName}
                          step={item?.labelSettings?.stepSize}
                          min={item?.labelSettings?.min}
                          max={item?.labelSettings?.max}
                          initialValue={
                            testValues[item?.labelId]?.value ?? item?.cellValue
                          }
                          disabled={!item?.canAnnotate}
                          onValueChange={(newValue) => {
                            handleValueChange(item?.labelId, newValue);
                          }}
                          isExpanded={expandedAccordion === index + 1}
                          setIsExpanded={() => toggleAccordion(index + 1)}
                        />
                      );
                    } else if (item?.labelType === "text") {
                      return (
                        <EditableAccordionText
                          key={item?.rowId + item?.labelId}
                          sliderText={item?.labelType}
                          sno={index + 1}
                          header={item?.labelName}
                          placeholderText={item?.labelSettings?.placeholder}
                          min={item?.labelSettings?.minLength}
                          max={item?.labelSettings?.maxLength}
                          editableText={
                            testValues[item?.labelId]?.value || item?.cellValue
                          }
                          disabled={!item?.canAnnotate}
                          onTextChange={(newValue) => {
                            handleValueChange(item?.labelId, newValue);
                            setErrors([]);
                          }}
                          errors={error}
                          isExpanded={expandedAccordion === index + 1}
                          setIsExpanded={() => toggleAccordion(index + 1)}
                        />
                      );
                    } else if (item?.labelType === "categorical") {
                      return (
                        <EditableAccordionCategorical
                          sliderText={item?.labelType}
                          sno={index + 1}
                          key={item?.rowId + item?.labelId}
                          header={item?.labelName}
                          options={item?.labelSettings?.options}
                          autoAnnotate={item?.labelSettings?.autoAnnotate}
                          disabled={!item?.canAnnotate}
                          onOptionSelect={(option) => {
                            const multiChoice =
                              item?.labelSettings?.multiChoice;
                            if (multiChoice) {
                              const newVal = [
                                ...(testValues[item?.labelId]?.value || []),
                                option?.label,
                              ];
                              handleValueChange(item?.labelId, newVal);
                            } else {
                              handleValueChange(item?.labelId, [option?.label]);
                            }
                          }}
                          onDescriptionChange={(description) => {
                            handleValueChange(
                              item?.labelId,
                              description,
                              "description",
                            );
                          }}
                          labelValue={
                            testValues[item?.labelId]?.value || item?.cellValue
                          }
                          description={
                            testValues[item?.labelId]?.description ||
                            item?.cellDescription
                          }
                          isExpanded={expandedAccordion === index + 1}
                          setIsExpanded={() => toggleAccordion(index + 1)}
                        />
                      );
                    }
                  }
                  return null;
                })}
              </Box>
            </Box>

            <Box>
              <Box
                sx={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  mb: 3,
                }}
              >
                <IconButton
                  size="small"
                  onClick={() => navigateToRow(firstRowOrder)} // Go to the first row
                  disabled={prevRowOrder === null}
                >
                  <Iconify
                    color="text.primary"
                    icon="ri:skip-left-line"
                    height={14}
                    width={14}
                  />
                </IconButton>
                <IconButton
                  size="small"
                  onClick={() => navigateToRow(prevRowOrder)} // Navigate to the previous valid row
                  disabled={prevRowOrder === null}
                >
                  <Iconify
                    color="text.primary"
                    icon="mdi:chevron-left"
                    height={18}
                    width={18}
                  />
                </IconButton>
                <Typography
                  sx={{
                    color: "text.primary",
                  }}
                  fontSize={13}
                  fontWeight={700}
                >
                  Row {currentRowNumber} of {totalRows}
                </Typography>
                <IconButton
                  size="small"
                  onClick={() => navigateToRow(nextRowOrder)}
                  disabled={nextRowOrder === null}
                >
                  <Iconify
                    color="text.primary"
                    icon="mdi:chevron-right"
                    height={18}
                    width={18}
                  />
                </IconButton>
                <IconButton
                  size="small"
                  onClick={() => navigateToRow(lastRowOrder)} // Go to the last row
                  disabled={nextRowOrder === null}
                >
                  <Iconify
                    color="text.primary"
                    icon="ri:skip-right-line"
                    height={14}
                    width={14}
                  />
                </IconButton>
              </Box>
              <Box
                sx={{
                  display: "flex",
                  gap: 2,
                  width: "100%",
                }}
              >
                <Button
                  onClick={handleResetAnnotation}
                  size="small"
                  fullWidth
                  variant="outlined"
                  disabled={isResettingAnnotation}
                >
                  Reset
                </Button>
                <Button
                  onClick={() => navigateToRow(nextRowOrder)}
                  size="small"
                  fullWidth
                  variant="outlined"
                  disabled={currentRowNumber === totalRows}
                >
                  Skip
                </Button>
                <LoadingButton
                  fullWidth
                  size="small"
                  type="submit"
                  variant="contained"
                  color="primary"
                  onClick={handleSave}
                  loading={isUpdatingAnnotation}
                  sx={{ minWidth: "max-content" }}
                >
                  {currentRowNumber === totalRows ? "Save" : "Save & Next"}
                </LoadingButton>
              </Box>
            </Box>
          </Grid>
        </Grid>
      </Box>
    </Box>
  );
}
