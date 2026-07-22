import {
  Box,
  Divider,
  Grid,
  IconButton,
  LinearProgress,
  Modal,
  Typography,
} from "@mui/material";
import { useQuery } from "@tanstack/react-query";
import PropTypes from "prop-types";
import React, { useState } from "react";
import { useHotkeys } from "react-hotkeys-hook";
import CustomAccordion from "src/components/Accordion/CustomAccordion";
import PreviewAccordionCategorical from "src/components/Accordion/PreviewAccordionCategorical";
import PreviewAccordionSlider from "src/components/Accordion/PreviewAccordionSlider";
import PreviewAccordionText from "src/components/Accordion/PreviewAccordionText";
import Iconify from "src/components/iconify";
import axios, { endpoints } from "src/utils/axios";

const MemoizedAccordion = React.memo(CustomAccordion);

export default function PreviewCreatedModal({
  open,
  onClose,
  annotationId,
  columnConfig,
}) {
  const [expandedAccordion, setExpandedAccordion] = useState(null);

  const { isLoading, data: previewData } = useQuery({
    queryKey: [`preview-annotation-created-${annotationId}`],
    queryFn: () =>
      axios.get(endpoints.annotation.annotateRow(annotationId), {
        params: {
          row_order: columnConfig ? columnConfig?.lowestUnfinishedRow : 0,
        },
      }),
    enabled: !!annotationId,
  });

  const toggleAccordion = (sno) => {
    setExpandedAccordion((prev) => (prev === sno ? null : sno));
  };

  useHotkeys(
    previewData?.data?.result?.data?.label
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
    [previewData],
  );

  return (
    <Modal open={open} onClose={onClose}>
      <Box
        sx={{
          position: "absolute",
          top: "50%",
          left: "50%",
          transform: "translate(-50%, -50%)",
          width: "90%",
          bgcolor: "background.paper",
          boxShadow: 24,
          borderRadius: "12px",
          height: "90%",
        }}
      >
        <Box
          display={"flex"}
          justifyContent={"space-between"}
          alignItems={"center"}
          sx={{ padding: "10px 20px" }}
        >
          <Typography fontWeight={700} fontSize={18} color="text.primary">
            Preview
          </Typography>
          <IconButton onClick={onClose}>
            <Iconify icon="mingcute:close-line" />
          </IconButton>
        </Box>
        <Divider />
        {!isLoading ? (
          <Box>
            <Grid container>
              {/* Left Side */}
              <Grid
                item
                xs={8}
                sx={{
                  borderRight: "2px solid grey",
                  borderColor: "background.neutral",
                  padding: "16px",
                  maxHeight: "calc(90vh - 57px)",
                  minHeight: "calc(90vh - 57px)",
                  overflow: "auto",
                }}
              >
                <Box
                  sx={{
                    display: "flex",
                    flexDirection: " column",
                    gap: "16px",
                  }}
                >
                  {previewData?.data?.result?.data?.staticFields?.map(
                    (item) => (
                      <MemoizedAccordion
                        key={item?.value}
                        labelText={item?.columnName}
                        detailsText={item?.value}
                        editable={item.edit === "editable"}
                      />
                    ),
                  )}
                  {previewData?.data?.result?.data?.responseFields.map(
                    (item) => (
                      <MemoizedAccordion
                        key={item?.value}
                        labelText={item?.columnName}
                        detailsText={item?.value}
                        editable={item.edit === "editable"}
                      />
                    ),
                  )}
                </Box>
              </Grid>
              {/* Right Side */}
              <Grid
                item
                xs={4}
                sx={{
                  padding: "16px",
                  maxHeight: "calc(90vh - 57px)",
                  overflow: "auto",
                }}
              >
                <Typography
                  color="text.secondary"
                  sx={{
                    mb: 1,
                    cursor: "pointer",
                  }}
                  fontSize={14}
                  fontWeight={700}
                >
                  Annotations
                </Typography>
                <Box
                  sx={{
                    display: "flex",
                    flexDirection: " column",
                    gap: "16px",
                  }}
                >
                  {previewData?.data?.result?.data?.label?.map(
                    (matchedField, i) => {
                      if (matchedField) {
                        if (matchedField?.labelType === "numeric") {
                          return (
                            <PreviewAccordionSlider
                              key={matchedField?.labelId}
                              sno={i + 1}
                              sliderText={
                                matchedField?.labelSettings?.displayType
                              }
                              header={matchedField?.labelName}
                              step={matchedField?.labelSettings?.stepSize}
                              min={matchedField?.labelSettings?.min}
                              max={matchedField?.labelSettings?.max}
                              isExpanded={expandedAccordion === i + 1}
                              setIsExpanded={() => toggleAccordion(i + 1)}
                              // initialValue={matchedField?.}
                            />
                          );
                        } else if (matchedField.labelType === "text") {
                          return (
                            <PreviewAccordionText
                              sliderText={matchedField.labelType}
                              sno={i + 1}
                              key={matchedField?.labelId}
                              header={matchedField.labelName}
                              placeholderText={
                                matchedField.labelSettings.placeholder
                              }
                              min={matchedField.labelSettings.minLength}
                              max={matchedField.labelSettings.maxLength}
                              isExpanded={expandedAccordion === i + 1}
                              setIsExpanded={() => toggleAccordion(i + 1)}
                            />
                          );
                        } else if (matchedField.labelType === "categorical") {
                          return (
                            <PreviewAccordionCategorical
                              sliderText={matchedField.labelType}
                              sno={i + 1}
                              key={matchedField?.labelId}
                              header={matchedField.labelName}
                              options={matchedField.labelSettings.options}
                              autoAnnotate={
                                matchedField.labelSettings.autoAnnotate
                              }
                              isExpanded={expandedAccordion === i + 1}
                              setIsExpanded={() => toggleAccordion(i + 1)}
                            />
                          );
                        }
                      }
                      return null;
                    },
                  )}
                </Box>
              </Grid>
            </Grid>
          </Box>
        ) : (
          <LinearProgress />
        )}
      </Box>
    </Modal>
  );
}

PreviewCreatedModal.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  annotationId: PropTypes.string,
  columnConfig: PropTypes.object,
};
