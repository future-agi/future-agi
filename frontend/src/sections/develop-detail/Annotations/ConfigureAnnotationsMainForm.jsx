import React, { useEffect, useState } from "react";
import { LoadingButton } from "@mui/lab";
import { Box, Button, Grid, Typography } from "@mui/material";
import PropTypes from "prop-types";
import {
  AnnotatorsAccordion,
  LabelsAccordion,
  ResponseAccordion,
  StaticAccordion,
} from "./AccordionFields/AnnotatorsAccordion";
import LabelModal from "./LabelModal";
import PreviewModal from "./PreviewModal";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";

const mapSelectedFields = (selectedFields, sourceColumn) => {
  if (!sourceColumn || !selectedFields) return [];
  return selectedFields.map((field) => {
    const id = field.label || field.column || field.addAnnotator; // Check various field keys
    return sourceColumn.find((item) => item.value === id) || {};
  });
};

const ConfigureAnnotationsMainForm = ({
  control,
  isWatch,
  formHandle,
  onHandleSubmit,
  onSubmit,
  loading,
  columnData,
  handleLabels,
  annotationData,
}) => {
  const [modals, setModals] = useState({
    addNewLabel: false,
    previewModal: false,
  });
  const [modalLabel, setModalLabel] = useState("");
  const [previewData, setPreviewData] = useState(null);

  // Set initial form values from annotationData
  useEffect(() => {
    if (annotationData?.name) {
      formHandle.setValue("name", annotationData.name);
    }
  }, [annotationData, formHandle]);

  const toggleModal = (modalName, value) => {
    setModals((prev) => ({ ...prev, [modalName]: value }));
  };

  const onAddNewLabel = (value = "") => {
    setModalLabel(value);
    toggleModal("addNewLabel", true);
  };

  const onAddLabelClose = () => {
    toggleModal("addNewLabel", false);
    formHandle.reset({
      addLabelFields: {
        name: "",
        type: "",
        displayOption: "",
        min: "",
        max: "",
        stepSize: "",
        placeholderText: "",
      },
      addLabelOptions: [{ label: "" }],
    });
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
    };

    setPreviewData(data);
    toggleModal("previewModal", true);
  };

  return (
    <Box sx={style.wrapper}>
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
                fieldName="name"
                label="Name"
                fullWidth
                placeholder="Enter Annotation Name"
                rules={{
                  required: "Name is required",
                }}
                helperText={null}
              />
            </Box>

            <StaticAccordion
              formHandle={formHandle}
              control={control}
              columnData={columnData}
            />
            <ResponseAccordion
              formHandle={formHandle}
              control={control}
              columnData={columnData}
            />
            <LabelsAccordion
              formHandle={formHandle}
              control={control}
              columnData={columnData}
              onAddNewLabel={onAddNewLabel}
              isWatch={isWatch}
            />
            <AnnotatorsAccordion
              formHandle={formHandle}
              control={control}
              columnData={columnData}
            />
          </Box>

          <LabelModal
            open={modals.addNewLabel}
            columnData={columnData}
            onClose={onAddLabelClose}
            modalLabel={modalLabel}
            handleLabels={handleLabels}
            control={control}
            formHandle={formHandle}
            isWatch={isWatch}
          />

          <Box sx={{ display: "flex", gap: 2, width: "100%" }}>
            <Button
              onClick={handlePreview}
              size="small"
              fullWidth
              variant="outlined"
            >
              Preview
            </Button>
            <LoadingButton
              fullWidth
              size="small"
              type="submit"
              variant="contained"
              color="primary"
              loading={loading}
            >
              Update
            </LoadingButton>
          </Box>
        </Grid>
      </form>

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

ConfigureAnnotationsMainForm.propTypes = {
  control: PropTypes.object.isRequired,
  isWatch: PropTypes.func.isRequired,
  formHandle: PropTypes.shape({
    reset: PropTypes.func.isRequired,
    setValue: PropTypes.func.isRequired,
  }).isRequired,
  onSubmit: PropTypes.func.isRequired,
  onHandleSubmit: PropTypes.func.isRequired,
  loading: PropTypes.bool,
  columnData: PropTypes.array,
  handleLabels: PropTypes.func.isRequired,
  annotationData: PropTypes.object, // PropType for annotationData
};

export default ConfigureAnnotationsMainForm;

const style = {
  wrapper: {
    padding: "20px",
    gap: "20px",
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
