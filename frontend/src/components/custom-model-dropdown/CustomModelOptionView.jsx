import React from "react";
import ToolHoverState from "../custom-model-options/ToolHoverState";
import CustomTooltip from "../tooltip";
import Iconify from "../iconify";
import PropTypes from "prop-types";
import { Box } from "@mui/material";

const CustomModelOptionView = ({ toolConfig }) => {
  return (
    <CustomTooltip
      card
      show={true}
      placement="bottom-end"
      title={<ToolHoverState config={toolConfig} />}
      enterDelay={100}
      enterNextDelay={100}
      slotProps={{
        popper: {
          modifiers: [
            {
              name: "offset",
              options: {
                offset: [0, -10],
              },
            },
          ],
        },
      }}
      sx={{
        "& .MuiTooltip-tooltip": {
          padding: 0,
          width: "300px",
        },
      }}
    >
      <Box
        sx={{
          display: "flex",
          gap: 0.5,
          border: "1px solid",
          borderColor: "background.neutral",
          borderRadius: "4px",
          paddingX: 1,
          paddingY: 0.5,
          backgroundColor: "background.default",
        }}
      >
        <Iconify
          // @ts-ignore
          icon="solar:tuning-2-linear"
          width="18px"
          height="18px"
          sx={{
            cursor: "pointer",
            color: "text.primary",
          }}
        />
      </Box>
    </CustomTooltip>
  );
};

CustomModelOptionView.propTypes = {
  toolConfig: PropTypes.object,
};

export default CustomModelOptionView;
