import {
  Box,
  Divider,
  Drawer,
  IconButton,
  Stack,
  Typography,
} from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import PersonaCreateEditForm from "./PersonaCreateEditForm";
import Iconify from "src/components/iconify";
import { coveredDrawerSx, useDrawerCovered } from "src/hooks/use-drawer-stack";
import { ShowComponent } from "src/components/show";
import { personaCreationTypes } from "../common";
import SvgColor from "src/components/svg-color";

const PersonaCreateEditDrawer = ({
  open,
  onClose,
  editPersona,
  personaCreateEditType,
  updatePersonaType,
}) => {
  const handleOnSuccess = () => {
    onClose();
  };
  const handleClose = () => {
    onClose();
  };
  const covered = useDrawerCovered(open);
  return (
    <Drawer
      anchor="right"
      open={open}
      onClose={onClose}
      sx={coveredDrawerSx(covered)}
    >
      <Box sx={{ width: "640px", height: "100vh", position: "relative" }}>
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
                    <SvgColor
                      src={e?.icon}
                      sx={{
                        width: 20,
                        color: "text.primary",
                        flexShrink: 0,
                        mt: 0.25,
                      }}
                    />
                    <Stack spacing={0.25}>
                      <Typography variant="m3" fontWeight={"fontWeightMedium"}>
                        {e?.title}
                      </Typography>
                      <Typography variant="s1" fontWeight={"fontWeightRegular"}>
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
            onSuccess={handleOnSuccess}
            onCancel={handleClose}
            type={personaCreateEditType}
          />
        </ShowComponent>
      </Box>
    </Drawer>
  );
};

PersonaCreateEditDrawer.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  editPersona: PropTypes.object,
  personaCreateEditType: PropTypes.string,
  updatePersonaType: PropTypes.func,
};

export default PersonaCreateEditDrawer;
