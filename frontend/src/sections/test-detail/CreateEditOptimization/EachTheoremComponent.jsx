import { Box, Chip, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import { ShowComponent } from "src/components/show";
import SvgColor from "src/components/svg-color";
import { OPTIMIZER_TYPE } from "./common";

const EachTheoremComponent = ({ model }) => {
  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "row",
        alignItems: "flex-start",
        padding: (theme) => theme.spacing(1),
        gap: 1,
      }}
    >
      <SvgColor
        sx={{
          color: "primary.main",
          width: 16,
          height: 16,
          marginTop: "2px",
        }}
        src={model?.icon}
      />
      <Box display="flex" flexDirection="column" flex="1">
        <Typography
          fontSize={13}
          lineHeight="20px"
          fontWeight="fontWeightMedium"
          color="text.primary"
          sx={{ mb: 0.25 }}
        >
          {model?.label}
        </Typography>

        <Typography
          variant="s2"
          color="text.tertiary"
          sx={{
            wordBreak: "break-word",
            lineHeight: 1.4,
          }}
        >
          {model?.description}
        </Typography>
      </Box>
      <ShowComponent
        condition={[
          OPTIMIZER_TYPE.BAYESIAN,
          OPTIMIZER_TYPE.RANDOM_SEARCH,
        ].includes(model?.value)}
      >
        <Chip
          sx={{
            position: "absolute",
            top: 0,
            right: 0,
            color: "status.success.fg",
            bgcolor: "status.success.bg",
          }}
          label={"Recommended"}
        />
      </ShowComponent>
    </Box>
  );
};
EachTheoremComponent.propTypes = {
  model: PropTypes.shape({
    icon: PropTypes.string.isRequired,
    label: PropTypes.string.isRequired,
    description: PropTypes.string.isRequired,
    value: PropTypes.string.isRequired,
  }).isRequired,
};

export default EachTheoremComponent;
