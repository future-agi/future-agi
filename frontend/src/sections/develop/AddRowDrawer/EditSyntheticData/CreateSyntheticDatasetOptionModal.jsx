import {
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControlLabel,
  IconButton,
  Radio,
  RadioGroup,
  Stack,
  TextField,
  Typography,
  useTheme,
} from "@mui/material";
import React, { useState } from "react";
import SvgColor from "../../../../components/svg-color/svg-color";
import PropTypes from "prop-types";
import { ShowComponent } from "../../../../components/show/ShowComponent";
import { LoadingButton } from "@mui/lab";

export default function CreateSyntheticDatasetOptionModal({
  open,
  onClose,
  onAction,
  isLoading,
  editMode,
}) {
  const [optionState, setOptionState] = useState({
    option: "replace_dataset",
    datasetName: "",
  });
  const handleClose = () => {
    if (isLoading) return;
    onClose();
    setOptionState({
      option: "replace_dataset",
      datasetName: "",
    });
  };
  const theme = useTheme();

  return (
    <Dialog
      PaperProps={{
        sx: {
          p: 2,
          maxWidth: "560px",
          minWidth: "560px",
        },
      }}
      open={open}
      onClose={handleClose}
    >
      <DialogTitle
        sx={{
          display: "flex",
          flexDirection: "row",
          justifyContent: "space-between",
          alignItems: "center",
          padding: 0,
          gap: 4,
        }}
      >
        <Typography
          variant="m3"
          color={"text.primary"}
          fontWeight={"fontWeightBold"}
        >
          Create Synthetic Dataset
        </Typography>
        <IconButton disabled={isLoading} size="small" onClick={handleClose}>
          <SvgColor
            sx={{
              bgcolor: "text.primary",
            }}
            src="/assets/icons/ic_close.svg"
          />
        </IconButton>
      </DialogTitle>
      <DialogContent
        sx={{
          padding: 0,
          mt: 2,
        }}
      >
        <RadioGroup
          value={optionState.option}
          onChange={(e) =>
            setOptionState((prev) => ({ ...prev, option: e.target.value }))
          }
          sx={{
            display: "flex",
            flexDirection: "column",
            gap: theme.spacing(3),
          }}
        >
          <FormControlLabel
            value="replace_dataset"
            sx={{ padding: 0, alignItems: "flex-start" }}
            control={
              <Radio
                sx={{
                  padding: 0,
                  paddingLeft: "10px",
                  paddingTop: "4px",
                }}
              />
            }
            label={
              <Stack sx={{ pl: "12px" }} direction="column" gap="2px">
                <Typography
                  variant="s1"
                  color="text.primary"
                  fontWeight="fontWeightMedium"
                >
                  Replace the current dataset
                </Typography>
                <Typography
                  variant="s2"
                  fontWeight="fontWeightRegular"
                  color="text.secondary"
                >
                  The current synthetic dataset will be replaced by the newly
                  configured dataset
                </Typography>
              </Stack>
            }
          />

          <FormControlLabel
            value="new_dataset"
            sx={{ padding: 0, alignItems: "flex-start" }}
            control={
              <Radio
                sx={{
                  padding: 0,
                  paddingLeft: "10px",
                  paddingTop: "4px",
                }}
              />
            }
            label={
              <Stack sx={{ pl: "12px" }} direction="column" gap="2px">
                <Typography
                  variant="s1"
                  color="text.primary"
                  fontWeight="fontWeightMedium"
                >
                  Create as new dataset
                </Typography>
                <Typography
                  variant="s2"
                  fontWeight="fontWeightRegular"
                  color="text.secondary"
                >
                  We will generate this as a new dataset while the previous
                  synthetic dataset stays the same
                </Typography>
              </Stack>
            }
          />
          <ShowComponent condition={optionState.option === "new_dataset"}>
            <Box>
              <TextField
                fullWidth
                required
                label={"Dataset name"}
                size="small"
                value={optionState.datasetName}
                onChange={(e) =>
                  setOptionState((prev) => ({
                    ...prev,
                    datasetName: e.target.value,
                  }))
                }
              />
            </Box>
          </ShowComponent>
          <FormControlLabel
            value="add_to_existing_dataset"
            sx={{ padding: 0, alignItems: "flex-start" }}
            control={
              <Radio
                sx={{
                  padding: 0,
                  paddingLeft: "10px",
                  paddingTop: "4px",
                }}
              />
            }
            label={
              <Stack sx={{ pl: "12px" }} direction="column" gap="2px">
                <Typography
                  variant="s1"
                  color="text.primary"
                  fontWeight="fontWeightMedium"
                >
                  Add it to existing dataset
                </Typography>
                <Typography
                  variant="s2"
                  fontWeight="fontWeightRegular"
                  color="text.secondary"
                >
                  We will generate a new dataset and add it to the previously
                  generated synthetic dataset
                </Typography>
              </Stack>
            }
          />
        </RadioGroup>
        {/* <ShowComponent condition={optionState.option === "new_dataset"}>
          <Divider
            sx={{
              mt: 2,
            }}
          />
          <Box>
            <TextField
              fullWidth
              required
              label={"Dataset name"}
              size="small"
              value={optionState.datasetName}
              onChange={(e) =>
                setOptionState((prev) => ({
                  ...prev,
                  datasetName: e.target.value,
                }))
              }
              sx={{
                mt: "24px",
              }}
            />
          </Box>
        </ShowComponent> */}
      </DialogContent>
      <DialogActions
        sx={{
          p: 0,
          mt: 1.5,
        }}
      >
        <Stack direction={"row"} gap={1.5} justifyContent={"flex-end"}>
          <Button
            disabled={isLoading}
            variant="outlined"
            onClick={handleClose}
            size="small"
          >
            Cancel
          </Button>
          <LoadingButton
            disabled={
              isLoading ||
              !optionState.option ||
              (optionState.option === "new_dataset" &&
                !optionState.datasetName.trim())
            }
            onClick={() => {
              if (onAction) {
                onAction(optionState);
              }
            }}
            sx={{ minWidth: "90px" }}
            size="small"
            color="primary"
            variant="contained"
          >
            {editMode ? "Add" : "Create Dataset"}
          </LoadingButton>
        </Stack>
      </DialogActions>
    </Dialog>
  );
}

CreateSyntheticDatasetOptionModal.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  onAction: PropTypes.func,
  isLoading: PropTypes.bool,
  editMode: PropTypes.bool,
};
