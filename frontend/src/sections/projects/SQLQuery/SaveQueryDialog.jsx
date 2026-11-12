import React, { useId } from "react";
import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import {
  Alert,
  Box,
  FormControl,
  FormControlLabel,
  FormLabel,
  Radio,
  RadioGroup,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import ModalWrapper from "src/components/ModalWrapper/ModalWrapper";
import { MetaLine } from "./queryUi";
import { describeQueryWindow } from "./queryPresentation";

export default function SaveQueryDialog({
  open,
  onClose,
  onSave,
  updatingSaved,
  selectedName,
  name,
  onNameChange,
  visibility,
  onVisibilityChange,
  saving,
  error,
  sql,
  hours,
  chart,
}) {
  const visibilityLabelId = useId();
  return (
    <ModalWrapper
      open={open}
      onClose={onClose}
      title={updatingSaved ? "Update saved query" : "Save query"}
      subTitle={
        updatingSaved
          ? `Saving replaces “${selectedName}” for everyone who can open it.`
          : "Reopen it later from Saved, or share it with your project."
      }
      actionBtnTitle={updatingSaved ? "Save changes" : "Save"}
      actionBtnProps={{ color: "inherit" }}
      onSubmit={onSave}
      isValid={Boolean(name.trim())}
      isLoading={saving}
      modalWidth="480px"
    >
      <Stack spacing={2}>
        {error && <Alert severity="error">{error}</Alert>}
        <TextField
          autoFocus
          fullWidth
          size="small"
          label="Query name"
          value={name}
          onChange={(e) => onNameChange(e.target.value)}
          inputProps={{ maxLength: 160 }}
        />
        <FormControl>
          <FormLabel
            id={visibilityLabelId}
            sx={{
              typography: "s2",
              fontWeight: "fontWeightSemiBold",
              mb: 1,
              color: "text.secondary",
              "&.Mui-focused": { color: "text.secondary" },
            }}
          >
            Visibility
          </FormLabel>
          <RadioGroup
            aria-labelledby={visibilityLabelId}
            value={visibility}
            onChange={(e) => onVisibilityChange(e.target.value)}
            sx={{ gap: 1 }}
          >
            {[
              ["private", "Private", "Only you can open it."],
              [
                "project",
                "Project members",
                "Anyone in this project can open it. Each person runs it with their own access; results are never shared.",
              ],
            ].map(([value, label, hint]) => (
              <FormControlLabel
                key={value}
                value={value}
                control={<Radio size="small" sx={{ mt: -0.25 }} />}
                label={
                  <Box>
                    <Typography
                      sx={{
                        typography: "s2",
                        fontWeight: "fontWeightSemiBold",
                      }}
                    >
                      {label}
                    </Typography>
                    <Typography
                      sx={{ typography: "s3", color: "text.secondary" }}
                    >
                      {hint}
                    </Typography>
                  </Box>
                }
                sx={{
                  m: 0,
                  px: 1,
                  py: 1,
                  alignItems: "flex-start",
                  border: 1,
                  borderRadius: 1,
                  borderColor:
                    visibility === value ? "primary.main" : "divider",
                  bgcolor: (t) =>
                    visibility === value
                      ? alpha(t.palette.primary.main, 0.06)
                      : "transparent",
                }}
              />
            ))}
          </RadioGroup>
        </FormControl>
        <Box
          sx={{
            p: 1.5,
            border: 1,
            borderColor: "divider",
            borderRadius: 1,
            bgcolor: "background.neutral",
          }}
        >
          <MetaLine
            icon="solar:diskette-linear"
            parts={[
              "Saves this SQL",
              describeQueryWindow(hours),
              chart?.type === "bar" ? "chart view" : "table view",
            ]}
            sx={{ mb: 0.75 }}
          />
          <Typography
            sx={{
              typography: "s3",
              fontFamily: "monospace",
              color: "text.secondary",
              whiteSpace: "pre-wrap",
              overflowWrap: "anywhere",
              display: "-webkit-box",
              WebkitLineClamp: 4,
              WebkitBoxOrient: "vertical",
              overflow: "hidden",
            }}
          >
            {sql}
          </Typography>
        </Box>
      </Stack>
    </ModalWrapper>
  );
}

SaveQueryDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  onSave: PropTypes.func.isRequired,
  updatingSaved: PropTypes.bool.isRequired,
  selectedName: PropTypes.string,
  name: PropTypes.string.isRequired,
  onNameChange: PropTypes.func.isRequired,
  visibility: PropTypes.oneOf(["private", "project"]).isRequired,
  onVisibilityChange: PropTypes.func.isRequired,
  saving: PropTypes.bool.isRequired,
  error: PropTypes.string,
  sql: PropTypes.string.isRequired,
  hours: PropTypes.number.isRequired,
  chart: PropTypes.shape({ type: PropTypes.oneOf(["table", "bar"]) })
    .isRequired,
};
