import { Box, Typography, useTheme } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import { useWatch } from "react-hook-form";
import TextLabelField from "./LabelFields/TextLabelField";
import NumericLabelField from "./LabelFields/NumericLabelField";
import StarLabelField from "./LabelFields/StarLabelField";
import ThumbsUpDownLabelField from "./LabelFields/ThumbsUpDownLabelField";
import CategoricalLabelField from "./LabelFields/CategoricalLabelField";

const LabelPreview = ({ control }) => {
  const annotationType = useWatch({ control, name: "type" });
  const labelName = useWatch({ control, name: "name" });
  const settings = useWatch({ control, name: "settings" });
  const theme = useTheme();
  const renderLabelPreview = () => {
    const hasLabel = labelName?.trim() !== "";

    if (!hasLabel) {
      return null;
    }

    switch (annotationType) {
      case "text":
        return <TextLabelField label={labelName} settings={settings} />;
      case "numeric":
        return <NumericLabelField label={labelName} settings={settings} />;
      case "star":
        return <StarLabelField label={labelName} settings={settings} />;
      case "thumbs_up_down":
        return <ThumbsUpDownLabelField label={labelName} settings={settings} />;
      case "categorical":
        return <CategoricalLabelField label={labelName} settings={settings} />;
      default:
        return null;
    }
  };

  return (
    <Box
      sx={{
        border: "1px solid ",
        borderRadius: theme.spacing(0.5),
        borderColor: theme.palette.divider,
        backgroundColor: "background.paper",
        minHeight: "147px",
        overflow: "auto",
      }}
    >
      <Box
        sx={{
          px: theme.spacing(1.5),
          py: theme.spacing(2),
        }}
      >
        <Typography fontSize="13px" fontWeight={500}>
          Preview
        </Typography>
      </Box>
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          pb: theme.spacing(2),
          px: theme.spacing(2),
        }}
      >
        {renderLabelPreview()}
      </Box>
    </Box>
  );
};

LabelPreview.propTypes = {
  control: PropTypes.object,
};

export default LabelPreview;
