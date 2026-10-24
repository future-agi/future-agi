import { Box, Divider, IconButton, Stack, Typography } from "@mui/material";
import PropTypes from "prop-types";
import SideDrawer from "src/sections/simulate-v2/components/SideDrawer";
import PersonaDrawerScale from "../PersonaDrawerScale";
import React from "react";
import PersonaCreateEditForm from "./PersonaCreateEditForm";
import Iconify from "src/components/iconify";
import { ShowComponent } from "src/components/show";
import { personaCreationTypes } from "../common";
import { normalizePersona } from "../personaApiShape";

const PersonaCreateEditDrawer = ({
  open,
  onClose,
  editPersona,
  personaCreateEditType,
  updatePersonaType,
  prefill,
  onSaved,
}) => {
  /* Hand the saved persona back (normalised) so a picker can select what was
     just created, or a scenario can link to it. */
  const handleOnSuccess = (res) => {
    const saved = res?.data?.result ?? res?.data ?? null;
    if (saved?.id != null) onSaved?.(normalizePersona(saved));
    onClose();
  };
  const handleClose = () => {
    onClose();
  };
  return (
    <SideDrawer open={open} onClose={onClose} width={640}>
      <PersonaDrawerScale>
        <Box sx={{ width: "100%", height: "100%", position: "relative" }}>
          <IconButton
            onClick={handleClose}
            sx={{
              position: "absolute",
              top: "12px",
              right: "12px",
              color: "text.primary",
            }}
          >
            <Iconify icon="solar:close-linear" />
          </IconButton>
          <ShowComponent condition={!personaCreateEditType}>
            <Stack padding={2} spacing={2}>
              <Box sx={{ display: "flex", flexDirection: "column" }}>
                <Typography variant="m2" fontWeight={"fontWeightSemiBold"}>
                  Create Persona
                </Typography>
                <Typography
                  color={"text.secondary"}
                  variant="s1"
                  fontWeight={"fontWeightRegular"}
                >
                  Choose your preferred agent type to create a persona
                </Typography>
              </Box>
              <Divider />

              <Stack spacing={2}>
                {personaCreationTypes.map((e, i) => {
                  return (
                    <Box
                      onClick={() => {
                        updatePersonaType(e?.value);
                      }}
                      key={i}
                      sx={{
                        display: "flex",
                        flexDirection: "row",
                        alignItems: "flex-start",
                        border: "1px solid",
                        borderColor: "divider",
                        padding: 1.75,
                        borderRadius: 1,
                        gap: 1.25,
                        cursor: "pointer",
                        transition:
                          "border-color .15s ease, background-color .15s ease",
                        "&:hover": {
                          borderColor: "text.disabled",
                          bgcolor: "action.hover",
                        },
                      }}
                    >
                      <Iconify
                        icon={e?.icon}
                        width={20}
                        sx={{ color: "text.primary", flexShrink: 0, mt: 0.25 }}
                      />
                      <Stack spacing={0.25}>
                        <Typography
                          variant="m3"
                          fontWeight={"fontWeightMedium"}
                        >
                          {e?.title}
                        </Typography>
                        <Typography
                          variant="s1"
                          fontWeight={"fontWeightRegular"}
                        >
                          {e?.description}
                        </Typography>
                      </Stack>
                    </Box>
                  );
                })}
              </Stack>
            </Stack>
          </ShowComponent>
          <ShowComponent condition={personaCreateEditType}>
            <PersonaCreateEditForm
              editPersona={editPersona}
              prefill={prefill}
              onSuccess={handleOnSuccess}
              onCancel={handleClose}
              type={personaCreateEditType}
            />
          </ShowComponent>
        </Box>
      </PersonaDrawerScale>
    </SideDrawer>
  );
};

PersonaCreateEditDrawer.propTypes = {
  prefill: PropTypes.object,
  onSaved: PropTypes.func,
  open: PropTypes.bool,
  onClose: PropTypes.func,
  editPersona: PropTypes.object,
  personaCreateEditType: PropTypes.string,
  updatePersonaType: PropTypes.func,
};

export default PersonaCreateEditDrawer;
