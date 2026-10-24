import PropTypes from "prop-types";
import React, { useRef, useState } from "react";
import ConfigureKeys from "../ConfigureKeys/ConfigureKeys";
import { Controller } from "react-hook-form";
import _ from "lodash";
import {
  FormControl,
  FormHelperText,
  InputLabel,
  MenuItem,
  Popover,
  Select,
} from "@mui/material";
import { camelCaseToTitleCase } from "src/utils/utils";

const ModelSelectInput = ({ control, configKey, config }) => {
  const [isApiConfigurationOpen, setApiConfigurationOpen] = useState(false);
  const fieldName = `config.config.${configKey}`;
  const [isOpen, setIsOpen] = useState(false);
  const anchorEl = useRef(null);
  const options =
    config?.configParamsOption?.[configKey] ||
    config?.config_params_option?.[configKey] ||
    [];

  const handleOpen = () => {
    setIsOpen(true);
  };

  const handleClose = () => {
    setIsOpen(false);
  };

  const selectOptions = options.map((option) => ({
    label: option,
    value: option,
  }));

  return (
    <>
      <ConfigureKeys
        open={isApiConfigurationOpen}
        onClose={() => setApiConfigurationOpen(false)}
      />
      <Controller
        render={({
          field: { onChange, value: selectValue },
          formState: { errors },
        }) => {
          const errorMessage = _.get(errors, `${fieldName}.message`);
          const isError = !!errorMessage;
          const selectedOption = selectOptions?.find(
            (o) => o.value === selectValue,
          );
          return (
            <FormControl error={isError} fullWidth size="small">
              <InputLabel>{camelCaseToTitleCase(configKey)}</InputLabel>
              <Select
                ref={anchorEl}
                value={selectValue || ""}
                error={isError}
                onOpen={handleOpen}
                onClose={handleClose}
                open={isOpen}
                label={camelCaseToTitleCase(configKey)}
                MenuProps={{
                  PaperProps: {
                    sx: {
                      display: "none",
                    },
                  },
                }}
              >
                <MenuItem value={selectedOption?.value || ""}>
                  {selectedOption?.label}
                </MenuItem>
              </Select>
              <Popover
                open={isOpen}
                onClose={handleClose}
                anchorEl={anchorEl?.current}
                anchorOrigin={{
                  vertical: "bottom",
                  horizontal: "right",
                }}
                transformOrigin={{
                  vertical: "top",
                  horizontal: "right",
                }}
                PaperProps={{
                  sx: {
                    width: anchorEl?.current?.clientWidth,
                    maxHeight: 300,
                  },
                }}
              >
                {selectOptions.map(({ label, value }) => (
                  <MenuItem
                    disabled={!config?.apiKeyAvailable}
                    key={label}
                    onClick={() => {
                      if (config?.apiKeyAvailable) {
                        onChange?.(value);
                        setIsOpen(false);
                      } else {
                        setApiConfigurationOpen(true);
                      }
                    }}
                    sx={{
                      pointerEvents: "all",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      "&.Mui-disabled": {
                        pointerEvents: "auto",
                        cursor: "pointer",
                        opacity: 1,
                        // eslint-disable-next-line no-restricted-syntax -- ds: error state of unavailable option
                        color: "status.danger.fg",
                      },
                    }}
                  >
                    {label}
                  </MenuItem>
                ))}
              </Popover>
              {isError && <FormHelperText>{errorMessage}</FormHelperText>}
            </FormControl>
          );
        }}
        control={control}
        name={fieldName}
        defaultValue={options[0] || ""}
      />
    </>
  );
};

ModelSelectInput.propTypes = {
  control: PropTypes.object,
  fieldConfig: PropTypes.object,
  config: PropTypes.object,
  configKey: PropTypes.string,
};

export default ModelSelectInput;
