import React from "react";
import { Stack, Typography, ButtonBase } from "@mui/material";
import PropTypes from "prop-types";
import SvgColor from "src/components/svg-color";

function ActionCard({ icon, label, onClick }) {
  return (
    <ButtonBase
      component="div"
      onClick={onClick}
      sx={{
        minWidth: "150px",
        display: "flex",
        flexDirection: "column",
        alignItems: "flex-start",
        justifyContent: "center",
        gap: 1,
        p: 1.5,
        position: "relative",
        background: "background.paper",
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "4px",
        transition: "all 0.2s ease-in-out",
        // Gradient border using pseudo-element (works with border-radius)
        "&::before": {
          content: '""',
          position: "absolute",
          inset: 0,
          borderRadius: "inherit",
          padding: "1px",
          background: "linear-gradient(90deg, #7857FC 0%, #CF6BE8 100%)",
          WebkitMask:
            "linear-gradient(#fff 0 0) content-box, linear-gradient(#fff 0 0)",
          WebkitMaskComposite: "xor",
          maskComposite: "exclude",
          opacity: 0,
          transition: "opacity 0.2s ease-in-out",
        },
        "&:hover": {
          border: "1px solid transparent",
          "&::before": {
            opacity: 1,
          },
        },
        // Change icon color on container hover
        "&:hover .action-card-icon": {
          background: "linear-gradient(90deg, #7857FC 0%, #CF6BE8 100%)",
        },
        // Change text color on container hover
        "&:hover .action-card-label": {
          background: "linear-gradient(90deg, #7857FC 0%, #CF6BE8 100%)",
          backgroundClip: "text",
          WebkitBackgroundClip: "text",
          WebkitTextFillColor: "transparent",
        },
      }}
    >
      <SvgColor
        src={icon}
        className="action-card-icon"
        sx={{
          width: 24,
          height: 24,
          transition: "all 0.2s ease-in-out",
        }}
      />
      <Typography
        typography="s2_1"
        fontWeight="fontWeightMedium"
        color="text.primary"
        className="action-card-label"
        sx={{
          transition: "all 0.2s ease-in-out",
        }}
      >
        {label}
      </Typography>
    </ButtonBase>
  );
}

ActionCard.propTypes = {
  icon: PropTypes.string.isRequired,
  label: PropTypes.string.isRequired,
  onClick: PropTypes.func,
};

export default function Actions({
  handleUploadJson: _handleUploadJson,
  handleAddManually,
  handleGenerateAI,
}) {
  return (
    <Stack direction="row" spacing={1.5}>
      {/* <ActionCard
        icon="/icons/datasets/upload_file.svg"
        label="Upload JSON"
        onClick={handleUploadJson}
      /> */}
      <ActionCard
        icon="/assets/icons/ic_edit.svg"
        label="Add data manually"
        onClick={handleAddManually}
      />
      <ActionCard
        icon="/assets/icons/components/ic_generate_prompt.svg"
        label="Generate using AI"
        onClick={handleGenerateAI}
      />
    </Stack>
  );
}

Actions.propTypes = {
  handleUploadJson: PropTypes.func.isRequired,
  handleAddManually: PropTypes.func.isRequired,
  handleGenerateAI: PropTypes.func.isRequired,
};
