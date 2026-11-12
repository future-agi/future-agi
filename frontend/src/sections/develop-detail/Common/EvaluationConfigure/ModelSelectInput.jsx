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
  Typography,
} from "@mui/material";
import { camelCaseToTitleCase } from "src/utils/utils";
import { CREDENTIAL_MODE, readCredentialMode, getModelEligibility } from "src/sections/develop-detail/Common/modelAccess";

const ModelSelectInput = ({ control, configKey, config }) => {
  const [isApiConfigurationOpen, setApiConfigurationOpen] = useState(false);
  const fieldName = `config.config.${configKey}`;
  const [isOpen, setIsOpen] = useState(false);
  const anchorEl = useRef(null);
  const options =
    config?.configParamsOption?.[configKey] ||
    config?.config_params_option?.[configKey] ||
    [];

  // Managed-or-BYOK eligibility of this eval's judge model. BYOK and
  // pre-contract eval configs keep the original apiKeyAvailable gate.
  const managedStructure = readCredentialMode(config) === CREDENTIAL_MODE.MANAGED;
  // A structure-level managed verdict is not a per-model grant. Keep the
  // existing provider-key check until this surface has a per-option carrier.
  const eligibility = managedStructure
    ? { ...getModelEligibility(null, config?.apiKeyAvailable), needsProviderSetup: false }
    : getModelEligibility(config, config?.apiKeyAvailable);
  // COMPOSE-C3 cut: this menu has no per-option managed grant, so whenever its
  // options are blocked the credits line shows. That includes the shape the
  // develop_dataset emitter actually sends (apiKeyAvailable, no one-key
  // fields); selection itself stays fail-closed.
  const showCreditsCut = !eligibility.selectable;

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
                    disabled={!eligibility.selectable}
                    key={label}
                    onClick={() => {
                      if (eligibility.selectable) {
                        onChange?.(value);
                        setIsOpen(false);
                      } else if (eligibility.needsProviderSetup) {
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
                        // eslint-disable-next-line no-restricted-syntax -- ds: error state
                        color: "status.danger.fg",
                      },
                    }}
                  >
                    {label}
                    {eligibility.payerLabel && (
                      <Typography
                        component="span"
                        sx={{
                          ml: 1,
                          fontSize: "12px",
                          color: "text.secondary",
                        }}
                      >
                        {eligibility.payerLabel}
                      </Typography>
                    )}
                  </MenuItem>
                ))}
              </Popover>
              {showCreditsCut && (
                <FormHelperText>
                  Future AGI credits do not run a judge from this menu. Add
                  your own provider key, or use the evaluator model picker.
                </FormHelperText>
              )}
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
