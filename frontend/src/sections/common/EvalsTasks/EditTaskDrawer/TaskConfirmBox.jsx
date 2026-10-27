import React, { useEffect, useState } from "react";
import PropTypes from "prop-types";
import {
  Dialog,
  DialogContent,
  Box,
  Radio,
  RadioGroup,
  Typography,
} from "@mui/material";
import { LoadingButton } from "@mui/lab";

const RadioOption = ({
  value,
  title,
  description,
  selectedValue,
  onChange,
}) => (
  <Box
    sx={{
      marginBottom: 1.2,
      cursor: "pointer",
    }}
    onClick={() => onChange(value)}
  >
    <Box sx={{ display: "flex", alignItems: "center", marginLeft: "-8px" }}>
      <Radio
        value={value}
        checked={selectedValue === value}
        onChange={() => onChange(value)}
        sx={{
          "&:hover": { backgroundColor: "transparent" },
        }}
      />
      <Typography
        sx={{
          color: "text.primary",
          fontWeight: 600,
          fontSize: "12px",
          ml: 0.5,
        }}
      >
        {title}
      </Typography>
    </Box>
    <Typography
      sx={{
        color: "text.disabled",
        fontWeight: 400,
        fontSize: "12px",
        ml: 4,
        marginTop: "-6px",
      }}
    >
      {description}
    </Typography>
  </Box>
);

RadioOption.propTypes = {
  value: PropTypes.string.isRequired,
  title: PropTypes.string.isRequired,
  description: PropTypes.string.isRequired,
  selectedValue: PropTypes.string.isRequired,
  onChange: PropTypes.func.isRequired,
};

export default function TaskConfirmDialog({
  title,
  content,
  confirmText = "Run task",
  open,
  onClose,
  onConfirm,
  isLoading,
  ...other
}) {
  const [editType, setEditType] = useState("fresh_run");

  // The dialog stays mounted between opens, so a previous "edit_rerun" pick
  // would still be selected next time — and the other option deletes data.
  useEffect(() => {
    if (open) setEditType("fresh_run");
  }, [open]);

  const handleConfirm = () => {
    onConfirm(editType);
  };

  // Define radio options centrally
  const radioOptions = [
    {
      value: "fresh_run",
      title: "Delete Past Data & Start Fresh",
      description:
        "Remove previous evaluations and begin a new analysis from scratch.",
    },
    {
      value: "edit_rerun",
      title: "Edit & Re-run Existing Evals",
      description:
        "Make changes to your current data and run the evaluation again.",
    },
  ];

  return (
    <Dialog
      fullWidth
      maxWidth="sm"
      open={open}
      onClose={onClose}
      {...other}
      PaperProps={{
        sx: {
          borderRadius: "8px",
          boxShadow: "0px 4px 12px rgba(0, 0, 0, 0.1)",
          width: "544px",
          height: "282px",
        },
      }}
    >
      <Box sx={{ position: "relative", padding: "16px" }}>
        <Typography variant="m3" component="h2" sx={{ fontWeight: 600 }}>
          {title}
        </Typography>

        {content && (
          <DialogContent
            sx={{
              color: "text.primary",
              padding: 0,
              mt: 2.8,
              fontSize: "14px",
              fontWeight: 500,
              mb: 1.2,
            }}
          >
            {content}
          </DialogContent>
        )}

        <RadioGroup
          value={editType}
          onChange={(e) => setEditType(e.target.value)}
        >
          {radioOptions.map((option) => (
            <RadioOption
              key={option.value}
              value={option.value}
              title={option.title}
              description={option.description}
              selectedValue={editType}
              onChange={setEditType}
            />
          ))}
        </RadioGroup>

        <Box
          sx={{
            display: "flex",
            justifyContent: "center",
            mt: 2.2,
          }}
        >
          <LoadingButton
            variant="contained"
            onClick={handleConfirm}
            loading={isLoading}
            color="primary"
            sx={{
              fontSize: "16px",
              fontWeight: 600,
              borderRadius: "8px",
              textTransform: "none",
              px: 3,
            }}
          >
            {confirmText}
          </LoadingButton>
        </Box>
      </Box>
    </Dialog>
  );
}

TaskConfirmDialog.propTypes = {
  confirmText: PropTypes.string,
  content: PropTypes.node,
  onClose: PropTypes.func,
  onConfirm: PropTypes.func,
  open: PropTypes.bool,
  title: PropTypes.string,
  isLoading: PropTypes.bool,
};
