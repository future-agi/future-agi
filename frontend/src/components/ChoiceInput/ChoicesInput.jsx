import {
  Box,
  Button,
  Divider,
  FormHelperText,
  IconButton,
  List,
  ListItem,
  ListItemText,
  TextField,
  Typography,
  useTheme,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useState } from "react";
import { camelCaseToTitleCase, getRandomId } from "src/utils/utils";
import HelperText from "../../sections/develop-detail/Common/HelperText";
import Iconify from "src/components/iconify";
import { useFieldArray, useFormState, useWatch } from "react-hook-form";
import _ from "lodash";
import SvgColor from "../svg-color";
import { enqueueSnackbar } from "src/components/snackbar";

const ChoicesInput = ({
  control,
  config,
  configKey,
  helperText,
  fieldPrefix = "config.config.",
  label,
  fieldLabel,
}) => {
  const fieldName = `${fieldPrefix}${configKey}`;
  const theme = useTheme();
  const [newChoice, setNewChoice] = useState("");
  const resolvedHelperText =
    helperText ||
    config?.configParamsDesc?.[configKey] ||
    config?.config_params_desc?.[configKey];

  const { fields, append, remove } = useFieldArray({
    control,
    name: fieldName,
  });

  const watchedFields = /** @type {Array<{value: string}>} */ (
    useWatch({ control, name: fieldName }) || []
  );

  const { errors } = useFormState({ control });

  const errorMessage = _.get(errors, fieldName)?.message || "";
  const isError = !!errorMessage;

  const addChoice = () => {
    if (!newChoice.trim()) return;
    const isDuplicate = watchedFields.some(
      (f) => f?.value === newChoice.trim(),
    );
    if (isDuplicate) {
      enqueueSnackbar("This choice already exists", { variant: "warning" });
      return;
    }
    append({ value: newChoice.trim(), id: getRandomId() });
    setNewChoice("");
  };

  return (
    <Box
      sx={{ display: "flex", gap: theme.spacing(1), flexDirection: "column" }}
    >
      <Box
        sx={{
          display: "flex",
          gap: theme.spacing(0.5),
          flexDirection: "column",
        }}
      >
        <Typography variant="body2">
          {label || camelCaseToTitleCase(configKey)}
        </Typography>
        <HelperText text={resolvedHelperText} sx={{ fontSize: "12px" }} />
      </Box>
      <Box
        sx={{
          display: "flex",
          gap: theme.spacing(1),
          alignItems: "center",
          my: theme.spacing(0.5),
        }}
      >
        <TextField
          fullWidth
          size="small"
          {...(fieldLabel && { label: fieldLabel })}
          placeholder="Type a choice and press enter"
          sx={{
            "& .MuiInputBase-input": {
              padding: `${theme.spacing(0.75)} ${theme.spacing(1)}`,
            },
          }}
          value={newChoice}
          onChange={(e) => setNewChoice(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              addChoice();
            }
          }}
        />
        <Button
          variant="outlined"
          size="small"
          color="primary"
          disabled={!newChoice.trim()}
          onClick={() => {
            addChoice();
          }}
          sx={{
            whiteSpace: "nowrap",
            width: "30%",
            color: theme.palette.primary.main,
            borderColor: theme.palette.primary.main,
          }}
        >
          <Iconify
            icon="solar:add-linear"
            sx={{ mr: theme.spacing(0.625), width: "16px", height: "16px" }}
          />
          Add Choice
        </Button>
      </Box>
      {fields.length ? (
        <>
          <List dense>
            {fields.map((field, index) => (
              <ListItem
                key={field.id}
                sx={{
                  display: "flex",
                  justifyContent: "space-between",
                  border: "1px solid",
                  borderColor: theme.palette.divider,
                  borderRadius: theme.spacing(0.5),
                  p: theme.spacing(1),
                  mb:
                    index !== fields.length - 1
                      ? theme.spacing(2)
                      : theme.spacing(0),
                }}
              >
                <ListItemText primary={field.value} />
                <IconButton size="small" onClick={() => remove(index)}>
                  <SvgColor
                    src="/assets/icons/ic_delete.svg"
                    sx={{
                      width: 20,
                      height: 20,
                      color: theme.palette.text.disabled,
                    }}
                  />
                </IconButton>
              </ListItem>
            ))}
          </List>
          <Divider />
        </>
      ) : (
        <></>
      )}
      {!!isError && (
        <FormHelperText
          sx={{ paddingLeft: theme.spacing(1), marginTop: theme.spacing(0) }}
          error={!!isError}
        >
          {errorMessage}
        </FormHelperText>
      )}
    </Box>
  );
};

ChoicesInput.propTypes = {
  control: PropTypes.object,
  config: PropTypes.object,
  configKey: PropTypes.string,
  helperText: PropTypes.string,
  fieldPrefix: PropTypes.string,
  label: PropTypes.any,
  fieldLabel: PropTypes.string,
};

export default ChoicesInput;
