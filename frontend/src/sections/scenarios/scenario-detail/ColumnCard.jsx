import React, { useState } from "react";
import { Box, IconButton, Typography, Collapse } from "@mui/material";
import PropTypes from "prop-types";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import SvgColor from "src/components/svg-color";
import ModalWrapper from "src/components/ModalWrapper/ModalWrapper";
import FormSearchSelectFieldControl from "../../../components/FromSearchSelectField/FormSearchSelectFieldControl";
import { dataTypeOptions } from "./common";

export const ColumnCard = ({
  control,
  index,
  removeColumn,
  removable,
  ColumnError,
}) => {
  const [deleteModalOpen, setDeleteModalOpen] = useState(false);
  const hasErrors = ColumnError && Object.keys(ColumnError).length > 0;
  const [expand, setExpand] = useState(true);
  const renderContent = () => {
    return (
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          borderRadius: 1,
          paddingY: 2,
          gap: 2,
          width: "100%",
        }}
      >
        <Box sx={{ display: "flex", gap: 1, width: "100%" }}>
          <FormTextFieldV2
            label="Column name"
            required={true}
            control={control}
            size="small"
            sx={{ flexGrow: 1 }}
            fieldName={`columns.${index}.name`}
            placeholder={"Enter Column Name"}
          />
          <FormSearchSelectFieldControl
            options={dataTypeOptions}
            label="Data type"
            required={true}
            dropDownMaxHeight={"100%"}
            size="small"
            control={control}
            sx={{ width: "232px", color: "text.primary" }}
            fieldName={`columns.${index}.type`}
          />
        </Box>
        <FormTextFieldV2
          control={control}
          fieldName={`columns.${index}.description`}
          label="Description"
          multiline
          minRows={4}
          maxRows={10}
          required={true}
          inputProps={{
            style: {
              minHeight: "82px",
              overflowY: "auto",
              whiteSpace: "pre-wrap",
            },
          }}
          fullWidth
        />
      </Box>
    );
  };

  return (
    <>
      <Box
        sx={{
          display: "flex",
          flexDirection: "row",
          alignItems: "flex-start",
          width: "100%",
          gap: 1,
        }}
      >
        <Box
          sx={{
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "4px",
            padding: 2,
            flexGrow: 1,
            width: "100%",
          }}
        >
          {/* Header */}
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              cursor: "pointer",
            }}
            onClick={() => setExpand((prev) => !prev)}
          >
            <Typography variant="m3" fontWeight={"fontWeightSemiBold"}>
              Column {index + 1}
            </Typography>
            <SvgColor
              src="/assets/icons/custom/lucide--chevron-down.svg"
              sx={{
                width: "20px",
                height: "20px",
                color: "text.primary",
                transform: expand ? "rotate(180deg)" : "rotate(0deg)",
                transition: "transform 0.2s ease",
              }}
            />
          </Box>

          {/* Collapsible Content */}
          <Collapse in={expand || hasErrors}>
            <Box sx={{ marginTop: 1 }}>{renderContent()}</Box>
          </Collapse>
        </Box>

        {removable && (
          <IconButton
            sx={{ paddingTop: 2, flexShrink: 0 }}
            onClick={() => {
              setDeleteModalOpen(true);
            }}
          >
            <SvgColor
              src="/assets/icons/ic_delete.svg"
              sx={{
                width: 24,
                height: 24,
                color: "text.primary",
              }}
            />
          </IconButton>
        )}
      </Box>

      <ModalWrapper
        modalWidth="560px"
        actionBtnTitle={"Delete"}
        isValid={true}
        actionBtnSx={{
          backgroundColor: "status.danger.solid",
          "&:hover": {
            backgroundColor: "status.danger.solid",
          },
        }}
        open={deleteModalOpen}
        onClose={() => setDeleteModalOpen(false)}
        title={`Delete Column ${index + 1}`}
        subTitle={`Are you sure you want to delete Column ${index + 1}?`}
        onSubmit={() => {
          removeColumn(index);
          setDeleteModalOpen(false);
        }}
      />
    </>
  );
};

ColumnCard.propTypes = {
  control: PropTypes.object.isRequired,
  index: PropTypes.number.isRequired,
  removeColumn: PropTypes.func.isRequired,
  removable: PropTypes.bool.isRequired,
  onToggleExpand: PropTypes.func.isRequired,
  ColumnError: PropTypes.oneOfType([PropTypes.bool, PropTypes.object]),
};
