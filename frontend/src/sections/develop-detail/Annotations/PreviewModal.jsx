import {
  Box,
  Divider,
  Grid,
  IconButton,
  Modal,
  Typography,
} from "@mui/material";
import { useQuery } from "@tanstack/react-query";
import PropTypes from "prop-types";
import React from "react";
import AccordionCategorical from "src/components/Accordion/AccordionCategorical";
import AccordionText from "src/components/Accordion/AccordionText";
import CustomAccordion from "src/components/Accordion/CustomAccordion";
import EditableAccordion from "src/components/Accordion/EditableAccordion";
import PreviewAccordionSlider from "src/components/Accordion/PreviewAccordionSlider";
import Iconify from "src/components/iconify";
import axios, { endpoints } from "src/utils/axios";

const MemoizedAccordion = React.memo(CustomAccordion);
const MemoizedEditAccordion = React.memo(EditableAccordion);

export default function PreviewModal({ open, onClose, previewData }) {
  // Check for valid label fields
  const hasValidLabelFields =
    previewData?.labelFields &&
    previewData.labelFields.some(
      (field) => field && Object.keys(field).length > 0,
    );

  // Extract column_ids from staticFields and responseFields
  const columnIds = [
    ...(previewData?.staticFields?.map((field) => field.value) || []),
    ...(previewData?.responseFields?.map((field) => field.value) || []),
  ].filter(Boolean); // Filter out any undefined or null values

  // Construct payload
  const payload = {
    dataset_id: previewData?.dataSetId,
    static_column: previewData?.staticFields?.map((field) => field.value) || [],
    response_column:
      previewData?.responseFields?.map((field) => field.value) || [],
  };

  // POST request using useQuery
  const { data: showLeftSideData } = useQuery({
    queryKey: ["previewData", previewData?.dataSetId],
    queryFn: async () => {
      const response = await axios.post(
        endpoints.annotation.previewAnnotations,
        payload,
      );
      return response.data;
    },
    enabled: !!previewData?.dataSetId && columnIds.length > 0,
  });

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
          // eslint-disable-next-line no-restricted-syntax -- ds: overlay shadow
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
          <Typography fontWeight={600} fontSize={16} color="text.primary">
            Preview
          </Typography>
          <IconButton onClick={onClose}>
            <Iconify icon="solar:close-linear" />
          </IconButton>
        </Box>
        <Divider />
        {!hasValidLabelFields ? (
          <Box
            display={"flex"}
            justifyContent={"center"}
            alignItems={"center"}
            sx={{ height: "80%" }}
          >
            <Typography fontWeight={600} fontSize={16} color="text.secondary">
              Please add atleast one label to enable preview...
            </Typography>
          </Box>
        ) : (
          <Box>
            <Grid container>
              {/* Left Section */}
              <Grid
                item
                xs={8}
                sx={{
                  padding: "16px",
                  borderRight: "2px solid grey",
                  borderColor: "background.neutral",
                  maxHeight: "80vh",
                  // minHeight: "calc(90vh - 57px)",
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
                  {showLeftSideData?.result?.previewData?.staticFields?.map(
                    (item) => {
                      const staticField = previewData?.staticFields?.find(
                        (field) => field?.value === item?.columnId,
                      );

                      return (
                        <MemoizedAccordion
                          key={item?.columnId}
                          labelText={item?.columnName}
                          detailsText={item?.value}
                          defaultExpanded={staticField?.view === "default_open"}
                          editable={item.edit === "editable"}
                        />
                      );
                    },
                  )}
                  {showLeftSideData?.result?.previewData?.responseFields?.map(
                    (item) => {
                      const responseFields = previewData?.responseFields?.find(
                        (field) => field?.value === item?.columnId,
                      );
                      if (responseFields?.edit !== "editable") {
                        return (
                          <MemoizedAccordion
                            key={item?.rowId + item?.columnId}
                            labelText={item?.columnName}
                            detailsText={item?.value}
                            defaultExpanded={
                              responseFields?.view === "default_open"
                            }
                            editable={item.edit === "editable"}
                          />
                        );
                      }

                      return (
                        <MemoizedEditAccordion
                          key={item?.rowId + item?.columnId}
                          labelText={item?.columnName}
                          detailsText={item?.value} // Use updated value or default
                          onDetailsChange={() => {}}
                        />
                      );
                    },
                  )}
                </Box>
              </Grid>
              {/* Right Section */}
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
                  fontSize={13}
                  fontWeight={600}
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
                  {previewData?.labelFields?.map((matchedField, i) => {
                    if (matchedField) {
                      if (matchedField?.type === "numeric") {
                        return (
                          <PreviewAccordionSlider
                            key={matchedField?.value}
                            sno={i + 1}
                            sliderText={matchedField?.type}
                            header={matchedField?.label}
                            step={matchedField?.settings?.stepSize}
                            min={matchedField?.settings?.min}
                            max={matchedField?.settings?.max}
                          />
                        );
                      } else if (matchedField.type === "text") {
                        return (
                          <AccordionText
                            sliderText={matchedField.type}
                            sno={i + 1}
                            key={matchedField?.value}
                            header={matchedField.label}
                            placeholderText={matchedField.settings.placeholder}
                            min={matchedField.settings.minLength}
                            max={matchedField.settings.maxLength}
                          />
                        );
                      } else if (matchedField.type === "categorical") {
                        return (
                          <AccordionCategorical
                            sliderText={matchedField.type}
                            sno={i + 1}
                            key={matchedField?.value}
                            header={matchedField.label}
                            options={matchedField.settings.options}
                            autoAnnotate={matchedField.settings.autoAnnotate}
                          />
                        );
                      }
                    }
                    return null;
                  })}
                </Box>
              </Grid>
            </Grid>
          </Box>
        )}
      </Box>
    </Modal>
  );
}

PreviewModal.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  previewData: PropTypes.any,
};
