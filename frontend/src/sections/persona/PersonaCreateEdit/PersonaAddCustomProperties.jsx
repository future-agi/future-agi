import { Box, Button, IconButton, Typography } from "@mui/material";
import React from "react";
import {
  CustomPersonaAccordion,
  CustomPersonaAccordionContent,
  CustomPersonaAccordionHeader,
} from "./PersonCustomComponents";
import SvgColor from "src/components/svg-color";
import { useFieldArray, useFormContext } from "react-hook-form";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import { getRandomId } from "src/utils/utils";
import Iconify from "src/components/iconify";

const PersonaAddCustomProperties = () => {
  const { control } = useFormContext();

  const { fields, append, remove } = useFieldArray({
    control,
    name: "customProperties",
  });

  return (
    <Box>
      <CustomPersonaAccordion disableGutters>
        <CustomPersonaAccordionHeader
          expandIcon={
            <SvgColor src="/assets/icons/custom/lucide--chevron-down.svg" />
          }
        >
          Add custom properties
        </CustomPersonaAccordionHeader>
        <CustomPersonaAccordionContent>
          <Box sx={{ display: "flex", flexDirection: "column", gap: 2 }}>
            <Typography variant="s1_2" color="text.secondary" fontWeight="fontWeightMedium">
              Define keys and values
            </Typography>
            {fields.map((field, index) => (
              <Box
                key={field.id}
                sx={{ display: "flex", gap: "12px", alignItems: "center" }}
              >
                <FormTextFieldV2
                  label="Property Name"
                  control={control}
                  fieldName={`customProperties.${index}.key`}
                  size="small"
                  fullWidth
                  placeholder="Enter property name"
                  required
                />
                <FormTextFieldV2
                  label="Value"
                  control={control}
                  fieldName={`customProperties.${index}.value`}
                  size="small"
                  fullWidth
                  placeholder="Enter value"
                  required
                />
                <IconButton
                  onClick={() => remove(index)}
                  sx={{
                    color: "text.primary",
                  }}
                >
                  <Iconify icon="akar-icons:cross" />
                </IconButton>
              </Box>
            ))}
            <Box>
              <Button
                variant="outlined"
                color="primary"
                size="small"
                sx={{ borderRadius: "4px" }}
                onClick={() =>
                  append({ key: "", value: "", id: getRandomId() })
                }
                startIcon={
                  <SvgColor src="/assets/icons/components/ic_add.svg" />
                }
              >
                Add
              </Button>
            </Box>
          </Box>
        </CustomPersonaAccordionContent>
      </CustomPersonaAccordion>
    </Box>
  );
};

export default PersonaAddCustomProperties;
