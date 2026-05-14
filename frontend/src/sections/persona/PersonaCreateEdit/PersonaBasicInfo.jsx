import { Box } from "@mui/material";
import React from "react";
import {
  CustomPersonaAccordion,
  CustomPersonaAccordionHeader,
  CustomPersonaAccordionContent,
} from "./PersonCustomComponents";
import SvgColor from "src/components/svg-color";
import { useFormContext } from "react-hook-form";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import { FormSearchSelectFieldControl } from "src/components/FromSearchSelectField";
import {
  AgeGroupOptions,
  GenderOptions,
  LocationOptions,
  ProfessionOptions,
} from "./common";
import PropTypes from "prop-types";
import { ShowComponent } from "src/components/show";

const PersonaBasicInfo = ({ viewOptions, multiple = true }) => {
  const showName = viewOptions?.name !== undefined ? viewOptions?.name : true;
  const showDescription =
    viewOptions?.description !== undefined ? viewOptions?.description : true;
  const { control } = useFormContext();
  return (
    <Box>
      <CustomPersonaAccordion disableGutters defaultExpanded>
        <CustomPersonaAccordionHeader
          expandIcon={
            <SvgColor src="/assets/icons/custom/lucide--chevron-down.svg" />
          }
        >
          Basic Information
        </CustomPersonaAccordionHeader>
        <CustomPersonaAccordionContent>
          <Box sx={{ display: "flex", flexDirection: "column", gap: 2 }}>
            <ShowComponent condition={showName}>
              <FormTextFieldV2
                control={control}
                fieldName="name"
                label="Persona name"
                required
                size="small"
                fullWidth
                placeholder="angry_customer"
              />
            </ShowComponent>
            <ShowComponent condition={showDescription}>
              <FormTextFieldV2
                control={control}
                fieldName="description"
                label="Description"
                required
                size="small"
                fullWidth
                placeholder="A customer who is angry about the product"
                multiline
                rows={2}
              />
            </ShowComponent>
            <Box
              sx={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 2 }}
            >
              <FormSearchSelectFieldControl
                control={control}
                fieldName="gender"
                label="Gender"
                fullWidth
                placeholder="Male"
                size="small"
                options={GenderOptions}
                multiple={multiple}
                checkbox={multiple}
                selectAll
              />
              <FormSearchSelectFieldControl
                control={control}
                fieldName="ageGroup"
                label="Age"
                fullWidth
                placeholder="25-32"
                size="small"
                options={AgeGroupOptions}
                multiple={multiple}
                checkbox={multiple}
                selectAll
              />
              <FormSearchSelectFieldControl
                control={control}
                fieldName="location"
                label="Location"
                fullWidth
                placeholder="United States"
                size="small"
                options={LocationOptions}
                multiple={multiple}
                checkbox={multiple}
                selectAll
              />
              <FormSearchSelectFieldControl
                control={control}
                fieldName="profession"
                label="Profession"
                fullWidth
                placeholder="Engineer"
                size="small"
                options={ProfessionOptions}
                multiple={multiple}
                checkbox={multiple}
                selectAll
              />
            </Box>
          </Box>
        </CustomPersonaAccordionContent>
      </CustomPersonaAccordion>
    </Box>
  );
};

PersonaBasicInfo.propTypes = {
  viewOptions: PropTypes.shape({
    name: PropTypes.bool,
    description: PropTypes.bool,
  }),
  multiple: PropTypes.bool,
};

export default PersonaBasicInfo;
