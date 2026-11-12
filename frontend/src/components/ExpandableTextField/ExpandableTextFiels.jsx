import React, { useState, useEffect } from "react";
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  IconButton,
  Box,
  TextField,
  Typography,
  Stack,
} from "@mui/material";
import PropTypes from "prop-types";
import { useFormContext } from "react-hook-form";
import SvgColor from "../svg-color/svg-color";
import _ from "lodash";
import CustomTooltip from "src/components/tooltip";

// Design system dialog widths: form 560, wide 720, data 960.
const DIALOG_WIDTH = { xs: 560, sm: 720, md: 960, lg: 960 };

const ExpandableTextField = ({
  children,
  fieldName,
  dialogTitle = "Edit Content",
  dialogMaxWidth = "md",
  fullWidth = true,
  textFieldLabel = "Edit Content",
  disabled = false,
  otherActions = [],
  onSave,
}) => {
  const [open, setOpen] = useState(false);
  const [tempValue, setTempValue] = useState("");

  const {
    setValue,
    getValues,
    formState: { errors },
  } = useFormContext();

  const hasError = Boolean(_.get(errors, fieldName));

  const handleOpen = () => {
    const currentValue = getValues(fieldName) || "";
    setTempValue(currentValue);
    setOpen(true);
  };

  const handleClose = () => {
    setOpen(false);
  };

  const handleSave = () => {
    setValue(fieldName, tempValue, {
      shouldValidate: true,
      shouldDirty: true,
      shouldTouch: true,
    });
    // Call onSave callback with synthetic event-like object for consistency
    if (onSave) {
      onSave({ target: { value: tempValue } });
    }
    setOpen(false);
  };

  const handleChange = (e) => {
    setTempValue(e.target.value);
  };

  useEffect(() => {
    if (open) {
      const currentValue = getValues(fieldName) || "";
      setTempValue(currentValue);
    }
  }, [open, getValues, fieldName]);

  return (
    <>
      <Box sx={{ position: "relative" }}>
        {children}
        <Stack
          sx={{
            position: "absolute",
            right: "7px",
            bottom: hasError ? "30px" : "8px",
            zIndex: 1,
          }}
          direction={"row"}
          gap={1}
        >
          {otherActions}
          <CustomTooltip
            key={"1"}
            show
            title="Maximize"
            placement="top"
            arrow
            type={"black"}
            size="small"
          >
            <IconButton
              onClick={handleOpen}
              disabled={disabled}
              sx={{
                border: "1px solid",
                borderColor: "divider",
                bgcolor: "background.paper",
                "&:hover": {
                  bgcolor: "background.paper",
                },
              }}
              size="small"
            >
              <SvgColor
                sx={{
                  height: "16px",
                  width: "16px",
                  bgcolor: "text.primary",
                }}
                src="/assets/icons/ic_maximize.svg"
              />
            </IconButton>
          </CustomTooltip>
        </Stack>
      </Box>

      <Dialog
        open={open}
        onClose={handleClose}
        maxWidth={false}
        fullWidth={fullWidth}
        PaperProps={{
          sx: {
            maxWidth: DIALOG_WIDTH[dialogMaxWidth] || 960,
            p: 2,
            display: "flex",
            flexDirection: "column",
          },
        }}
      >
        <DialogTitle
          sx={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            p: 0,
          }}
        >
          <Typography typography={"m3"} fontWeight={600} color={"text.primary"}>
            {dialogTitle}
          </Typography>
          <IconButton onClick={handleClose} size="small" sx={{ ml: 2 }}>
            <SvgColor
              src="/assets/icons/ic_close.svg"
              sx={{
                height: 24,
                width: 24,
                bgcolor: "text.primary",
              }}
            />
          </IconButton>
        </DialogTitle>

        <DialogContent sx={{ p: 0 }}>
          <Box
            sx={{
              py: 2,
            }}
          >
            <TextField
              multiline
              fullWidth
              rows={24}
              value={tempValue}
              onChange={handleChange}
              label={textFieldLabel}
              variant="outlined"
            />
          </Box>
        </DialogContent>

        <DialogActions sx={{ p: 0 }}>
          <Button
            fullWidth
            variant="outlined"
            onClick={handleClose}
            size="small"
          >
            Minimize
          </Button>
          <Button
            fullWidth
            color="primary"
            onClick={handleSave}
            variant="contained"
            size="small"
          >
            Save
          </Button>
        </DialogActions>
      </Dialog>
    </>
  );
};

export default ExpandableTextField;

ExpandableTextField.propTypes = {
  children: PropTypes.node.isRequired,
  fieldName: PropTypes.string.isRequired,
  dialogTitle: PropTypes.string,
  dialogMaxWidth: PropTypes.string,
  fullWidth: PropTypes.bool,
  textFieldLabel: PropTypes.string,
  disabled: PropTypes.bool,
  otherActions: PropTypes.array,
  onSave: PropTypes.func,
};
