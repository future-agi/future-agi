import { Box, ButtonBase } from "@mui/material";
import React, { useState } from "react";
import SvgColor from "src/components/svg-color";
import LabelSelectPopover from "./LabelSelectPopover";
import PropTypes from "prop-types";
import CustomTooltip from "src/components/tooltip";

const LabelDropdown = ({ version, haveTag }) => {
  const [open, setOpen] = useState(false);
  const handleClose = () => {
    setOpen(false);
  };
  return (
    <Box>
      <CustomTooltip
        show={true}
        title={haveTag ? "Change Tag" : "Add Tag"}
        size="small"
        arrow={true}
      >
        <ButtonBase
          component="div"
          sx={{
            padding: "5px 12px 5px 12px",
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "4px",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
          }}
          onClick={() => setOpen(true)}
        >
          <SvgColor
            sx={{
              height: 20,
              width: 20,
              color: "text.primary",
            }}
            src="/assets/icons/ic_tag.svg"
          />
        </ButtonBase>
      </CustomTooltip>

      <LabelSelectPopover
        promptId={version?.original_template}
        open={open}
        handleClose={handleClose}
        version={version?.template_version}
        versionId={version?.id}
        selectedLabels={version?.labels}
      />
    </Box>
  );
};

LabelDropdown.propTypes = {
  version: PropTypes.object,
  haveTag: PropTypes.bool,
};

export default LabelDropdown;
