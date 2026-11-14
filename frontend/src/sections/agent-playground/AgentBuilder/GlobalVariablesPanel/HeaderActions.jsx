import { Button } from "@mui/material";
import React from "react";
import SvgColor from "src/components/svg-color";
import {
  GeneratePromptButton,
  GeneratePromptButtonIcon,
} from "../../../../components/PromptCards/PromptCardStyleComponents";
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
              width="16px"
              sx={{ color: "text.primary" }}
            />
          }
          onClick={onOpenImportDatasetDrawer}
          sx={{
            color: "text.primary",
            border: "1px solid",
            borderColor: "divider",
          }}
        >
          Import from Dataset
        </Button>
      )}
      <GeneratePromptButton
        // onClick={handleGenerateDataClick}
        startIcon={<GeneratePromptButtonIcon />}
        size="small"
        borderRadius={"4px"}
        padding="5px 12px"
        height="30px"
        disabled={disabled}
        sx={{
          "& .svg-color": {
            mr: 1,
          },
        }}
      >
        Generate Sample Data
      </GeneratePromptButton>
    </>
  );
}

HeaderActions.propTypes = {
  handleUploadJson: PropTypes.func.isRequired,
  currentView: PropTypes.string,
  onOpenImportDatasetDrawer: PropTypes.func,
  disabled: PropTypes.bool,
};
