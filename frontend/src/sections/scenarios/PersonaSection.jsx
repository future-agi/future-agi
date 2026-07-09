import { Box, Button, Typography } from "@mui/material";
import React, { useState } from "react";
import CreateScenarioHeader from "./CreateScenarioHeader";
import SvgColor from "src/components/svg-color";
import SwitchField from "src/components/Switch/SwitchField";
import PropTypes from "prop-types";
import { useFieldArray, useWatch, useFormState } from "react-hook-form";
import { ShowComponent } from "src/components/show";
import PersonaDrawer from "../persona/PersonaDrawer";
import GenericCard from "../persona/GenericCard/GenericCard";
import { extractTagsFromPersona } from "../persona/common";
import CustomTooltip from "src/components/tooltip";

const PersonaSection = ({ control, description = "" }) => {
  const [open, setOpen] = useState(false);
  const addPersonaAutomatically = useWatch({
    control,
    name: "addPersonaAutomatically",
  });
  const { fields, remove, replace } = useFieldArray({
    control,
    name: "personas",
    keyName: "fieldId",
  });
  const agentType = useWatch({
    control,
    name: "agentType",
  });
  const { errors } = useFormState({ control });
  const personaError =
    typeof errors?.personas?.message === "string"
      ? errors.personas.message
      : typeof errors?.personas?.root?.message === "string"
        ? errors.personas.root.message
        : "";
  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: "12px" }}>
      <CreateScenarioHeader
        title="Persona"
        description={
          description ??
          "Persona type defines user behavior, tone, and intent for your scenario tests"
        }
        docLink={{
          url: "https://docs.futureagi.com/docs/simulation/concepts/personas",
          sx: {
            color: "blue.500",
            fontWeight: "fontWeightMedium",
            fontSize: "13px",
            textDecoration: "underline",
          },
        }}
        rightSection={
          <ShowComponent condition={!addPersonaAutomatically}>
            <CustomTooltip
              show={!agentType}
              title="Please select an Agent Definition before choosing a Persona"
              size="small"
              type="black"
              arrow={true}
            >
              <span>
                <Button
                  variant="outlined"
                  color="primary"
                  disabled={!agentType}
                  sx={{ borderRadius: "4px" }}
                  size="small"
                  onClick={() => setOpen(true)}
                  startIcon={
                    <SvgColor src="/assets/icons/components/ic_add.svg" />
                  }
                >
                  Add persona
                </Button>
              </span>
            </CustomTooltip>
          </ShowComponent>
        }
      />
      <Box
        sx={{
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 0.5,
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        <Box sx={{ padding: "12px 16px" }}>
          <Typography typography="s1" fontWeight="fontWeightMedium">
            Add by default
          </Typography>
          <Typography
            typography="s2_1"
            fontWeight="fontWeightRegular"
            color="text.secondary"
          >
            Auto-adds all active personas to your scenarios
          </Typography>
        </Box>

        <CustomTooltip
          show={true}
          title="Enabling this will add all activated personas as default in your scenarios."
          placement="bottom"
          arrow
          size="small"
          type="black"
          slotProps={{
            tooltip: {
              sx: {
                maxWidth: "200px !important",
              },
            },
          }}
        >
          <Box>
            <SwitchField
              control={control}
              fieldName="addPersonaAutomatically"
              disableRipple
            />
          </Box>
        </CustomTooltip>
      </Box>
      <ShowComponent condition={!addPersonaAutomatically && !!personaError}>
        <Typography typography="s2_1" color="error.main">
          {personaError}
        </Typography>
      </ShowComponent>
      <ShowComponent condition={!addPersonaAutomatically}>
        <Box
          sx={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))",
            gap: "12px",
            width: "100%",
          }}
        >
          {fields.map((persona, index) => (
            <GenericCard
              key={persona.fieldId}
              isPrebuilt={persona.isDefault}
              title={persona.name}
              description={persona.description}
              tags={extractTagsFromPersona(persona)}
              titleIcon="persona"
              onRemove={() => remove(index)}
              viewOptions={{
                editable: false,
                selectable: false,
                isDrawer: false,
                simulationType: persona?.simulationType,
                removable: true,
              }}
            />
          ))}
        </Box>
      </ShowComponent>
      <PersonaDrawer
        open={open}
        onClose={() => setOpen(false)}
        onAddPersonas={(personas) => {
          setOpen(false);
          replace(personas);
        }}
        personaCreateEditType={agentType}
        preSelectedPersonas={fields}
      />
    </Box>
  );
};

PersonaSection.propTypes = {
  control: PropTypes.object,
  description: PropTypes.string,
};

export default PersonaSection;
