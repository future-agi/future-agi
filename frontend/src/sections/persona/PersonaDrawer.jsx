import {
  Box,
  Button,
  Divider,
  Drawer,
  IconButton,
  Typography,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useState } from "react";
import PersonaListView from "./PersonaListView";
import Iconify from "src/components/iconify";
import { coveredDrawerSx, useDrawerCovered } from "src/hooks/use-drawer-stack";
import { Collapse } from "@mui/material";
import PersonaCreateEditForm from "./PersonaCreateEdit/PersonaCreateEditForm";
import { normalizePersona } from "./personaApiShape";

const PersonaListContent = ({
  personaCreateEditType,
  lockedFilters,
  onClose,
  onAddPersonas,
  onCreatePersona,
  selectedPersonas,
  onToggleSelect,
}) => {
  const handleAddPersonas = () => {
    onAddPersonas(selectedPersonas);
  };

  return (
    <Box
      sx={{
        width: "96vw",
        display: "flex",
        flexDirection: "column",
        height: "100vh",
        position: "relative",
      }}
    >
      <IconButton
        onClick={onClose}
        sx={{
          position: "absolute",
          top: "12px",
          right: "12px",
          color: "text.primary",
        }}
      >
        <Iconify icon="solar:close-linear" width={20} />
      </IconButton>
      <Box
        sx={{
          flex: 1,
          minHeight: 0,
          p: 2,
          display: "flex",
          flexDirection: "column",
        }}
      >
        <PersonaListView
          onCreatePersona={onCreatePersona}
          selectedPersonas={selectedPersonas}
          onToggleSelect={onToggleSelect}
          isSelectable
          personaCreateEditType={personaCreateEditType}
          lockedFilters={lockedFilters}
        />
      </Box>
      <Divider flexItem orientation="horizontal" />
      <Box
        sx={{
          padding: 2,
          display: "flex",
          alignItems: "center",
          gap: "12px",
          justifyContent: "flex-end",
        }}
      >
        <Typography
          typography="s1"
          fontWeight="fontWeightMedium"
          sx={{ paddingX: 2 }}
        >
          Personas selected ({selectedPersonas?.length})
        </Typography>
        <Button
          variant="outlined"
          onClick={onClose}
          sx={{ minWidth: "160px" }}
          size="small"
        >
          Cancel
        </Button>

        <Button
          variant="contained"
          color="primary"
          sx={{ minWidth: "160px" }}
          size="small"
          disabled={selectedPersonas?.length === 0}
          onClick={handleAddPersonas}
        >
          Add
        </Button>
      </Box>
    </Box>
  );
};

PersonaListContent.propTypes = {
  personaCreateEditType: PropTypes.string,
  lockedFilters: PropTypes.object,
  onClose: PropTypes.func,
  onAddPersonas: PropTypes.func,
  onCreatePersona: PropTypes.func,
  selectedPersonas: PropTypes.array,
  onToggleSelect: PropTypes.func,
};

const PersonaCreateContent = ({ onCancel, onCreated, type }) => {
  return (
    <Box sx={{ width: "700px", height: "100vh" }}>
      <IconButton
        onClick={onCancel}
        sx={{
          position: "absolute",
          top: "12px",
          right: "12px",
          color: "text.primary",
        }}
      >
        <Iconify icon="solar:close-linear" />
      </IconButton>
      <PersonaCreateEditForm
        onCancel={onCancel}
        onSuccess={onCreated}
        type={type}
      />
    </Box>
  );
};

PersonaCreateContent.propTypes = {
  onCancel: PropTypes.func,
  onCreated: PropTypes.func,
  type: PropTypes.string,
};

const PersonaDrawer = ({
  open,
  onClose,
  onAddPersonas,
  personaCreateEditType,
  lockedFilters = null,
  preSelectedPersonas = [],
}) => {
  const [createEditOpen, setCreateEditOpen] = useState(false);
  // Kept here, not in the list panel: the list unmounts while the inline
  // create form is open, and the selection has to survive that round trip.
  const [selectedPersonas, setSelectedPersonas] = useState(
    preSelectedPersonas ?? [],
  );
  const [wasOpen, setWasOpen] = useState(open);

  // Each time the drawer opens, start from the personas already chosen.
  if (open !== wasOpen) {
    setWasOpen(open);
    if (open) setSelectedPersonas(preSelectedPersonas ?? []);
  }

  const handleToggleSelect = (persona, newValue) => {
    setSelectedPersonas((prev) => {
      if (newValue) {
        return prev.some((p) => p.id === persona.id)
          ? prev
          : [...prev, persona];
      }
      return prev.filter((p) => p.id !== persona.id);
    });
  };

  // The create form passes the POST response; select the new persona so the
  // user can add it straight away.
  const handlePersonaCreated = (response) => {
    const created = response?.data?.result;
    if (created?.id) handleToggleSelect(normalizePersona(created), true);
    setCreateEditOpen(false);
  };

  const handleDrawerClose = () => {
    onClose();
    setCreateEditOpen(false);
  };

  const handleCreateEditClose = () => {
    setCreateEditOpen(false);
  };

  const covered = useDrawerCovered(open);

  return (
    <Drawer
      anchor="right"
      open={open}
      onClose={handleDrawerClose}
      sx={coveredDrawerSx(covered)}
    >
      <Collapse
        in={createEditOpen && open}
        unmountOnExit
        orientation="horizontal"
      >
        <PersonaCreateContent
          onCancel={handleCreateEditClose}
          onCreated={handlePersonaCreated}
          type={personaCreateEditType}
        />
      </Collapse>
      <Collapse
        in={!createEditOpen && open}
        unmountOnExit
        orientation="horizontal"
      >
        <PersonaListContent
          personaCreateEditType={personaCreateEditType}
          lockedFilters={lockedFilters}
          onClose={handleDrawerClose}
          onAddPersonas={onAddPersonas}
          onCreatePersona={() => setCreateEditOpen(true)}
          selectedPersonas={selectedPersonas}
          onToggleSelect={handleToggleSelect}
        />
      </Collapse>
    </Drawer>
  );
};

PersonaDrawer.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  onAddPersonas: PropTypes.func,
  personaCreateEditType: PropTypes.string,
  lockedFilters: PropTypes.object,
  preSelectedPersonas: PropTypes.array,
};

export default PersonaDrawer;
