import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControl,
  FormControlLabel,
  IconButton,
  Radio,
  RadioGroup,
  Stack,
  Typography,
  useTheme,
} from "@mui/material";
import React, { useEffect, useState } from "react";
import Iconify from "../iconify";
import PropTypes from "prop-types";

const generate_choices = {
  ADD_TO_REMAINING: "add_to_remaining",
  GENERATE_ALL: "generate_all",
};

const GeneratePromptConfirmation = ({
  open,
  onClose,
  totalRowCount,
  incompleteRowCount,
}) => {
  const theme = useTheme();
  const [choice, setChoice] = useState(generate_choices.GENERATE_ALL);
  const [error, setError] = useState(null);

  const handleChoiceChange = (e) => {
    const newChoice = e.target.value;
    setChoice(newChoice);
    setError(null);
  };

  useEffect(() => {
    if (open) {
      // If there are no incomplete rows, automatically select GENERATE_ALL
      if (incompleteRowCount === 0) {
        setChoice(generate_choices.GENERATE_ALL);
      }
      setError(null);
    }
  }, [open, incompleteRowCount]);

  const handleClose = (confirmed) => {
    if (confirmed && !error) {
      onClose(choice);
    } else {
      onClose(null);
    }
  };

  return (
    <Dialog
      open={open}
      onClose={() => handleClose(false)}
      PaperProps={{
        sx: {
          minWidth: "560px",
          borderRadius: "8px",
          padding: theme.spacing(2),
          display: "flex",
          flexDirection: "column",
          gap: theme.spacing(2),
        },
      }}
    >
      <DialogTitle sx={{ padding: 0 }}>
        <Typography
          typography={"m3"}
          color={"text.primary"}
          fontWeight={"fontWeightSemiBold"}
        >
          Generate sample data
        </Typography>
        <IconButton
          onClick={() => handleClose(false)}
          sx={{
            position: "absolute",
            top: "12px",
            right: "12px",
            color: "text.primary",
          }}
        >
          <Iconify icon="solar:close-linear" />
        </IconButton>
      </DialogTitle>

      <DialogContent sx={{ padding: 0 }}>
        <FormControl>
          <RadioGroup
            value={choice}
            onChange={handleChoiceChange}
            sx={{
              display: "flex",
              flexDirection: "column",
              gap: theme.spacing(2),
            }}
          >
            {incompleteRowCount > 0 && (
              <FormControlLabel
                value={generate_choices.ADD_TO_REMAINING}
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
                  <Stack
                    sx={{ pl: theme.spacing(1.5) }}
                    direction="column"
                    gap="2px"
                  >
                    <Typography
                      variant="s1"
                      color="text.primary"
                      fontWeight="fontWeightMedium"
                    >
                      Add to Remaining Cells
                    </Typography>
                    <Typography
                      variant="s2"
                      fontWeight="fontWeightRegular"
                      color="text.primary"
                    >
                      Keeps your data and fills in the rest (
                      {incompleteRowCount} rows)
                    </Typography>
                  </Stack>
                }
              />
            )}

            <FormControlLabel
              value={generate_choices.GENERATE_ALL}
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
                <Stack
                  sx={{ pl: theme.spacing(1.5) }}
                  direction="column"
                  gap={theme.spacing(0.25)}
                >
                  <Typography
                    variant="s1"
                    color="text.primary"
                    fontWeight="fontWeightMedium"
                  >
                    Regenerate All Cells
                  </Typography>
                  <Typography
                    variant="s2"
                    fontWeight="fontWeightRegular"
                    color="text.primary"
                  >
                    Replaces all data with new values ({totalRowCount} rows)
                  </Typography>
                </Stack>
              }
            />
          </RadioGroup>
          {error && (
            <Typography
              color="error"
              variant="s2"
              sx={{ mt: theme.spacing(1), ml: theme.spacing(4) }}
            >
              {error}
            </Typography>
          )}
        </FormControl>
      </DialogContent>

      <DialogActions
        sx={{
          padding: 0,
          display: "flex",
          flexDirection: "row",
          gap: theme.spacing(2),
          "& button": {
            margin: 0,
            marginTop: 2,
          },
        }}
      >
        <Button
          variant="outlined"
          onClick={() => handleClose(false)}
          sx={{
            minWidth: "200px",
            "&:hover": {
              borderColor: "divider",
            },
          }}
        >
          <Typography
            variant="s1"
            color="text.primary"
            fontWeight="fontWeightMedium"
          >
            Cancel
          </Typography>
        </Button>
        <Button
          variant="contained"
          color="primary"
          onClick={() => handleClose(true)}
          disabled={!!error}
          sx={{
            minWidth: "200px",
          }}
        >
          <Typography
            variant="s1"
            color="primary.contrastText"
            fontWeight="fontWeightMedium"
          >
            Generate
          </Typography>
        </Button>
      </DialogActions>
    </Dialog>
  );
};

GeneratePromptConfirmation.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  selectedChoice: PropTypes.string,
  totalRowCount: PropTypes.number,
  incompleteRowCount: PropTypes.number,
};

export default GeneratePromptConfirmation;
