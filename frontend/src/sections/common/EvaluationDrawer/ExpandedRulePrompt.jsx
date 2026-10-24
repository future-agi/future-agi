import {
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  Typography,
  useTheme,
  alpha,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useMemo } from "react";
import Iconify from "../../../components/iconify";
import HeadingAndSubheading from "../../../components/HeadingAndSubheading/HeadingAndSubheading";
import FormTextFieldV2 from "../../../components/FormTextField/FormTextFieldV2";
import { extractVariables } from "src/utils/utils";
import { useWatch } from "react-hook-form";

const ExpandedRulePrompt = ({
  open,
  control,
  onClose,
  fieldName,
  handleSave,
}) => {
  const theme = useTheme();
  const criteria = useWatch({
    control,
    name: fieldName,
  });
  const extractedKeys = useMemo(() => {
    return extractVariables(criteria);
  }, [criteria]);
  return (
    <Dialog open={open} onClose={onClose} fullWidth>
      <DialogTitle
        sx={{
          padding: theme.spacing(2),
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          width: "100%",
        }}
      >
        Rule Prompt
        <IconButton
          sx={{
            padding: 0,
          }}
          onClick={onClose}
        >
          <Iconify
            icon="solar:close-linear"
            sx={{
              width: theme.spacing(3),
              height: theme.spacing(3),
              color: "text.primary",
            }}
          />
        </IconButton>
      </DialogTitle>
      <DialogContent
        sx={{
          display: "flex",
          flexDirection: "column",
          gap: theme.spacing(2),
          padding: theme.spacing(2),
        }}
      >
        <HeadingAndSubheading
          heading="Rule Prompt"
          subHeading="Write a prompt defining the specific rules, patterns, or criteria the Input must adhere to."
          required
        />
        <FormTextFieldV2
          control={control}
          size="medium"
          fieldName={"criteria"}
          rows={20}
          maxRows={20}
          label={<Typography typography={"s2"}>Prompt</Typography>}
          sx={{
            height: "70vh",
            "& .MuiInputBase-root": {
              overflow: "auto",
            },
            "& .MuiOutlinedInput-root": {
              minHeight: "100%",
            },
            display: "flex",
          }}
          InputProps={{
            style: {
              height: "100%",
              paddingX: theme.spacing(2),
              paddingY: theme.spacing(1),
            },
            inputProps: {
              style: {
                minHeight: "100%",
              },
            },
          }}
          multiline
          fullWidth
          helperText={undefined}
          defaultValue={undefined}
          onBlur={undefined}
          showExpand
          InputLabelProps={{
            style: {
              background: "var(--bg-paper)",
              padding: theme.spacing(0.5),
            },
          }}
          placeholder={"Write a prompt here. Use {{ to create a new variable"}
        />
        {extractedKeys.length > 0 && (
          <Box
            display={"flex"}
            gap={theme.spacing(1.5)}
            alignItems={"center"}
            flexWrap={"wrap"}
          >
            <Typography typography={"s2"}>Variables:</Typography>
            {extractedKeys.map((key, index) => {
              return (
                <Typography
                  key={index}
                  typography={"s2"}
                  sx={{
                    bgcolor: alpha(theme.palette.primary.main, 0.1),
                    color: "primary.main",
                    fontWeight: "fontWeightMedium",
                    borderRadius: theme.spacing(0.5),
                    paddingY: theme.spacing(0.5),
                    paddingX: theme.spacing(1),
                  }}
                >
                  {`{{${key}}}`}
                </Typography>
              );
            })}
          </Box>
        )}
      </DialogContent>
      <DialogActions
        sx={{
          display: "flex",
          padding: theme.spacing(2),
          paddingTop: 0,
        }}
      >
        <Button variant="outlined" color="primary" fullWidth onClick={onClose}>
          minimize
        </Button>
        <Button
          variant="contained"
          color="primary"
          fullWidth
          onClick={handleSave}
        >
          Save
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default ExpandedRulePrompt;

ExpandedRulePrompt.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  handleSave: PropTypes.func,
  control: PropTypes.any,
  fieldName: PropTypes.string,
};
