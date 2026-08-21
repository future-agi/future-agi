import { Button } from "@mui/material";
import React from "react";
import SvgColor from "src/components/svg-color";
import PropTypes from "prop-types";
import { VIEW } from "../../store";

export default function HeaderActions({
  handleUploadJson: _handleUploadJson,
  currentView,
  onOpenImportDatasetDrawer,
  disabled,
}) {
  return (
    <>
      {/* <Button
        size="small"
        variant="outlined"
        startIcon={<SvgColor src="/icons/datasets/upload_file.svg" />}
        onClick={handleUploadJson}
      >
        Upload JSON
      </Button> */}
      {currentView === VIEW.MANUAL_FORM && (
        <Button
          aria-label="open-import-dataset"
          size="small"
          disabled={disabled}
          startIcon={
            <SvgColor
              src="/assets/icons/navbar/hugeicons.svg"
              sx={{
                width: "16px",
                height: "16px",
                color: "text.primary",
              }}
            />
          }
          onClick={onOpenImportDatasetDrawer}
          sx={{
            color: "text.primary",
            border: "1px solid",
            fontSize: "12px",
            fontWeight: 400,
            borderColor: "whiteScale.500",
            paddingX: 3,
          }}
        >
          Import from Dataset
        </Button>
      )}
    </>
  );
}

HeaderActions.propTypes = {
  handleUploadJson: PropTypes.func.isRequired,
  currentView: PropTypes.string,
  onOpenImportDatasetDrawer: PropTypes.func,
  disabled: PropTypes.bool,
};
