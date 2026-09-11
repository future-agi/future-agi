import React from "react";
import {
  Box,
  Button,
  Divider,
  Stack,
  Typography,
  useTheme,
} from "@mui/material";
import PropTypes from "prop-types";
import AlertTypeSelector from "./AlertTypeSelector";
import { alertDetails } from "../common";
import CellMarkdown from "src/sections/common/CellMarkdown";
import Image from "src/components/image";

export default function SelectAlertType({
  alertType,
  onChange,
  onCancel,
  handleNext,
}) {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "row",
        height: "100%",
        minHeight: "400px",
      }}
    >
      <Box
        sx={{
          flex: 1,
          display: "flex",
          flexDirection: "column",
          gap: 3,
        }}
      >
        <Stack>
          <Typography
            variant="s1"
            color={"text.primary"}
            fontWeight={"fontWeightSemiBold"}
          >
            Alert Type
          </Typography>
          <Typography
            variant="s3"
            fontWeight={"fontWeightRegular"}
            color={"text.secondary"}
          >
            Choose from our list of alert types to set up your alerts
          </Typography>
        </Stack>
        <AlertTypeSelector selectedAlert={alertType} onChange={onChange} />
      </Box>

      <Divider orientation="vertical" flexItem sx={{ mr: 2 }} />

      <Stack
        sx={{
          flex: 1,
          gap: 3,
        }}
      >
        <Box
          sx={{
            p: 2,
            border: "1px solid",
            borderColor: "divider",
            backgroundColor: "background.neutral",
            display: "flex",
            flexDirection: "column",
            gap: 0.5,
            borderRadius: 0.5,
          }}
        >
          <Typography
            variant="s1"
            color={"text.primary"}
            fontWeight={"fontWeightSemiBold"}
          >
            {alertDetails?.[alertType]?.title}
          </Typography>
          <CellMarkdown
            spacing={0}
            text={alertDetails?.[alertType]?.markdown}
          />
          <Image
            alt="alert preview"
            src={
              isDark
                ? "/assets/images/monitors/alerts-preview_dark.png"
                : "/assets/images/monitors/alerts-preview.png"
            }
          />
        </Box>
        <Stack
          direction="row"
          spacing={2}
          sx={{
            mt: "auto",
            justifyContent: "flex-end",
          }}
        >
          <Button onClick={onCancel} fullWidth variant="outlined">
            Cancel
          </Button>
          <Button
            disabled={!alertType}
            onClick={handleNext}
            fullWidth
            color="primary"
            variant="contained"
            data-alert-type-action="next"
          >
            Next
          </Button>
        </Stack>
      </Stack>
    </Box>
  );
}

SelectAlertType.displayName = "SelectAlertType";

SelectAlertType.propTypes = {
  alertType: PropTypes.string,
  onChange: PropTypes.func,
  onCancel: PropTypes.func,
  handleNext: PropTypes.func,
};
