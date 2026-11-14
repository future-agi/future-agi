import { LoadingButton } from "@mui/lab";
import { Box, Button, Grid, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React, { useState } from "react";
import { trackEvent, Events } from "src/utils/Mixpanel";
import {
  AnnotatorsAccordion,
  LabelsAccordion,
  ResponseAccordion,
  StaticAccordion,
} from "./AccordionFields/AnnotatorsAccordion";
import LabelModal from "./LabelModal";
import PreviewModal from "./PreviewModal";
import { useParams } from "react-router";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";

const mapSelectedFields = (selectedFields, sourceColumn) => {
  if (!sourceColumn || !selectedFields) return [];
  return selectedFields.map((field) => {
    const id = field.labelName || field.column || field.addAnnotator; // Check various field keys
    return Object.assign(field, {
      ...(sourceColumn.find((item) => item.value === id) || {}),
    });
  });
};

const AnnotationsMainForm = ({
  control,
  isWatch,
  formHandle,
  onHandleSubmit,
  onSubmit,
  loading,
  columnData,
  annotationId,
  handleLabels,
  setSelectedLabelIndex,
  fetchNextPage,
  isFetchingNextPage,
}) => {
  const [modals, setModals] = useState({
    addNewLabel: false,
    previewModal: false,
  });
  const [modalLabel, setModalLabel] = useState("");
  const [previewData, setPreviewData] = useState(null);
  const { dataset: dataSetId } = useParams();

  const toggleModal = (modalName, value) => {
    setModals((prev) => ({ ...prev, [modalName]: value }));
  };

  const onAddNewLabel = (value = "", index) => {
    setModalLabel(value);
    toggleModal("addNewLabel", true);
    setSelectedLabelIndex(index);
  };

  const onAddLabelClose = () => {
    toggleModal("addNewLabel", false);
  };

  const handlePreview = () => {
    const staticFieldObjects = mapSelectedFields(
      isWatch("staticFields"),
      columnData?.[0]?.staticFieldColumn,
    );

    const responseFieldObjects = mapSelectedFields(
      isWatch("responseFields"),
      columnData?.[0]?.staticFieldColumn,
    );

    const labelFieldObjects = mapSelectedFields(
      isWatch("labelFields"),
      columnData?.[1]?.labelFieldColumn,
    );

    const annotatorObjects = mapSelectedFields(
      isWatch("annotatorFields"),
      columnData?.[2]?.annotationUserColumn,
    );

    const data = {
      name: isWatch("name"),
      staticFields: staticFieldObjects,
      responseFields: responseFieldObjects,
      labelFields: labelFieldObjects,
      annotators: annotatorObjects,
      dataSetId: dataSetId,
    };

    setPreviewData(data);
    toggleModal("previewModal", true);
  };

  const filteredColumnData = columnData.map((section) => ({
    ...section,
    staticFieldColumn: section.staticFieldColumn?.filter(
      (field) => field.dataType !== "audio",
    ),
  }));

  return (
    <Box sx={{ ...style.wrapper, gap: "5px" }}>
      <Typography fontWeight={600} color="text.secondary">
        Configure annotation view template
      </Typography>
      <form
        onSubmit={onHandleSubmit(onSubmit)}
        style={{ flex: 1, overflow: "hidden" }}
      >
        <Grid
          sx={{
            flex: 1,
            gap: 2,
            display: "flex",
            flexDirection: "column",
            overflow: "hidden",
            height: "100%",
          }}
        >
          <Box sx={style.container}>
            <Box sx={{ width: "50%", marginBottom: 2 }}>
              <FormTextFieldV2
                control={control}
                size="small"
                fieldName="name"
                label="Name"
                fullWidth
                placeholder="Test View"
                rules={{
                  required: "Name is required",
                }}
                helperText={null}
              />
            </Box>

            <StaticAccordion
              formHandle={formHandle}
              control={control}
              columnData={filteredColumnData}
              isWatch={isWatch}
            />
            <ResponseAccordion
              formHandle={formHandle}
              control={control}
              columnData={filteredColumnData}
              isWatch={isWatch}
            />
            <LabelsAccordion
              formHandle={formHandle}
              control={control}
              columnData={columnData}
              onAddNewLabel={onAddNewLabel}
              isWatch={isWatch}
              isFetchingNextPage={isFetchingNextPage}
              fetchNextPage={fetchNextPage}
            />
            <AnnotatorsAccordion
              formHandle={formHandle}
              control={control}
              columnData={columnData}
              isWatch={isWatch}
            />
          </Box>

          <Box sx={{ display: "flex", gap: 2, width: "100%" }}>
            <Button
              onClick={() => {
                trackEvent(Events.annPreviewViewed); // Track event for new annotation view click
                handlePreview(); // Execute existing function
              }}
              // size="small"
              fullWidth
              variant="outlined"
            >
              Preview
            </Button>
            <LoadingButton
              fullWidth
              // size="small"
              type="submit"
              variant="contained"
              color="primary"
              loading={loading}
            >
              {annotationId ? "Update" : "Create"}
            </LoadingButton>
          </Box>
        </Grid>
      </form>

      <LabelModal
        open={modals.addNewLabel}
        columnData={columnData}
        onClose={onAddLabelClose}
        modalLabel={modalLabel}
        handleLabels={handleLabels}
      />

      {modals.previewModal && (
        <PreviewModal
          open={modals.previewModal}
          onClose={() => toggleModal("previewModal", false)}
          previewData={previewData}
        />
      )}
    </Box>
  );
};

AnnotationsMainForm.propTypes = {
  control: PropTypes.object.isRequired,
  isWatch: PropTypes.func.isRequired,
  formHandle: PropTypes.any,
  onSubmit: PropTypes.func.isRequired,
  onHandleSubmit: PropTypes.func.isRequired,
  loading: PropTypes.bool,
  handleLabels: PropTypes.any,
  columnData: PropTypes.array,
  annotationId: PropTypes.string,
  setSelectedLabelIndex: PropTypes.func,
  fetchNextPage: PropTypes.func,
  isFetchingNextPage: PropTypes.bool,
};

export default AnnotationsMainForm;

const style = {
  wrapper: {
    padding: "20px",
    // gap: "20px",
    display: "flex",
    flexDirection: "column",
    height: "100%",
    width: "100%",
  },
  container: {
    flex: 1,
    display: "flex",
    gap: 2,
    flexDirection: "column",
    overflowY: "scroll",
    paddingY: 1,
  },
};
