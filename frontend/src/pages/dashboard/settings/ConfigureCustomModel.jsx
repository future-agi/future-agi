import { Box, Button, IconButton, Typography, useTheme } from "@mui/material";
import PropTypes from "prop-types";
import React, { useEffect, useMemo, useState } from "react";
import { useFieldArray } from "react-hook-form";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import Iconify from "src/components/iconify";
import SvgColor from "src/components/svg-color";

// A header value may be the model's key (x_api_key, Authorization), so it is
// hidden while typing with the settings show/hide button; the api_base row
// holds a URL and stays readable.
const CustomValueField = ({ secret, ...props }) => {
  const [shown, setShown] = useState(false);
  const label = "Custom Value";
  const secretProps = secret
    ? {
        type: shown ? "text" : "password",
        autoComplete: "new-password",
        InputProps: {
          endAdornment: (
            <IconButton
              onClick={() => setShown((value) => !value)}
              edge="end"
              aria-label={shown ? `Hide ${label}` : `Show ${label}`}
            >
              <Iconify
                icon={shown ? "solar:eye-bold" : "solar:eye-closed-bold"}
              />
            </IconButton>
          ),
        },
      }
    : {};
  return (
    <FormTextFieldV2
      {...props}
      label={label}
      placeholder="Enter custom value"
      {...secretProps}
    />
  );
};

CustomValueField.propTypes = {
  secret: PropTypes.bool,
};

const ConfigureCustomModel = ({
  control,
  fieldName,
  commonAttribute,
  clearOnFocus,
}) => {
  const theme = useTheme();
  const { fields, append, remove } = useFieldArray({
    control,
    name: fieldName,
  });

  useEffect(() => {
    if (fields.length === 0) {
      append({ key: "", value: "" });
    }
  }, [fields, append]);

  const dynamicFieldsWrapperStyles = useMemo(
    () => ({
      flex: 1,
      display: "flex",
      flexDirection: "column",
      gap: theme.spacing(1.5),
    }),
    [theme],
  );

  const CustomFields = useMemo(
    () => ({
      display: "flex",
      gap: theme.spacing(1.875),
      marginBottom: theme.spacing(1.25),
    }),
    [theme],
  );

  const addButtonStyles = useMemo(
    () => ({
      minWidth: theme.spacing(11.25),
      border: "1px solid",
      borderRadius: theme.spacing(1),
      borderColor: "text.disabled",
      padding: theme.spacing(0, 3),
      alignSelf: "flex-start",
    }),
    [theme],
  );

  return (
    <React.Fragment>
      <Box sx={dynamicFieldsWrapperStyles}>
        <Typography variant="s1" fontWeight={"fontWeightSemiBold"}>
          Add Custom Configuration
        </Typography>

        {/* Scrollable field section */}
        <Box
          sx={{
            maxHeight: "50vh",
            overflowY: "auto",
            pt: 1,
            display: "flex",
            flexDirection: "column",
            gap: 0.5,
            "&::-webkit-scrollbar": {
              width: "0px",
            },
          }}
        >
          {fields?.map((item, index) => (
            <Box key={item.id} sx={CustomFields}>
              <FormTextFieldV2
                {...commonAttribute}
                control={control}
                fieldName={`${fieldName}.${index}.key`}
                label="Custom Key"
                placeholder="Enter custom key"
                disabled={!!item?.disabled}
              />
              <CustomValueField
                {...commonAttribute}
                control={control}
                fieldName={`${fieldName}.${index}.value`}
                onFocus={clearOnFocus(`${fieldName}.${index}.value`)}
                secret={item?.key !== "api_base"}
              />
              {item?.key !== "api_base" ? (
                <IconButton
                  onClick={() => remove(index)}
                  aria-label={`Remove config ${index + 1}`}
                  sx={{ alignSelf: "flex-start" }}
                >
                  <SvgColor
                    src={`/assets/icons/components/ic_delete.svg`}
                    sx={{
                      width: 24,
                      height: 24,
                      color: "text.primary",
                    }}
                  />
                </IconButton>
              ) : (
                fields.length > 1 && <Box sx={{ width: "85px" }} />
              )}
            </Box>
          ))}
        </Box>

        {/* Add Button */}
        <Button
          sx={addButtonStyles}
          size="small"
          onClick={() => append({ key: "", value: "" })}
          startIcon={
            <SvgColor
              width={16}
              sx={{ color: "text.primary" }}
              src={"/assets/icons/ic_add.svg"}
            />
          }
        >
          <Typography
            variant="s2"
            color="text.primary"
            fontWeight="fontWeightMedium"
          >
            Add more configuration
          </Typography>
        </Button>
      </Box>
    </React.Fragment>
  );
};

ConfigureCustomModel.propTypes = {
  control: PropTypes.object,
  fieldName: PropTypes.string,
  commonAttribute: PropTypes.object,
  clearOnFocus: PropTypes.func,
};

export default ConfigureCustomModel;
