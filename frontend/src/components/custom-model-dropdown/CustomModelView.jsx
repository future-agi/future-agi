import React from "react";
import PropTypes from "prop-types";
import CustomTooltip from "../tooltip";
import ModelHoverState from "./ModelHoverState";
import { Box, Typography, useTheme } from "@mui/material";
import Image from "../image";

const CustomModelView = ({ modelName, logoUrl }) => {
  const theme = useTheme();

  return (
    <CustomTooltip
      card
      show={Boolean(modelName)}
      placement="bottom-end"
      title={<ModelHoverState modelName={modelName} />}
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
          width: "350px",
        },
      }}
    >
      <Box
        sx={{
          display: "flex",
          gap: theme.spacing(0.5),
          border: "1px solid",
          borderColor: "background.neutral",
          borderRadius: "4px",
          paddingX: 1,
          paddingY: 0.5,
          backgroundColor: "background.default",
          maxWidth: "200px",
          minWidth: "100px",
        }}
      >
        {logoUrl && (
          <Image
            ratio="1/1"
            src={logoUrl}
            alt={modelName}
            flexShrink={0}
            style={{ width: "16px", height: "16px" }}
          />
        )}

        <Typography
          variant="s2"
          fontWeight={"fontWeightSemiBold"}
          color="text.primary"
          sx={{
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
            overflow: "hidden",
          }}
        >
          {modelName || "No model"}
        </Typography>
      </Box>
    </CustomTooltip>
  );
};

CustomModelView.propTypes = {
  modelName: PropTypes.string,
  logoUrl: PropTypes.string,
};

export default CustomModelView;
