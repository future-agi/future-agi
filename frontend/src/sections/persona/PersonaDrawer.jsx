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
import { Collapse } from "@mui/material";
import PersonaCreateEditForm from "./PersonaCreateEdit/PersonaCreateEditForm";

const PersonaListContent = ({
  personaCreateEditType,
  lockedFilters,
  onClose,
  onAddPersonas,
  onCreatePersona,
  preSelectedPersonas,
}) => {
  const [selectedPersonas, setSelectedPersonas] = useState(
    preSelectedPersonas ?? [],
  );

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

  const handleAddPersonas = () => {
    onAddPersonas(selectedPersonas);
    setSelectedPersonas([]);
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
        <Iconify icon="akar-icons:cross" />
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
          onToggleSelect={handleToggleSelect}
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
  preSelectedPersonas: PropTypes.array,
};

const PersonaCreateContent = ({ onCancel, type }) => {
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
        <Iconify icon="akar-icons:cross" />
      </IconButton>
      <PersonaCreateEditForm
        onCancel={onCancel}
        onSuccess={onCancel}
        type={type}
      />
    </Box>
  );
};

PersonaCreateContent.propTypes = {
  onCancel: PropTypes.func,
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

  const handleDrawerClose = () => {
    onClose();
    setCreateEditOpen(false);
  };

  const handleCreateEditClose = () => {
    setCreateEditOpen(false);
  };

  return (
    <Drawer anchor="right" open={open} onClose={handleDrawerClose}>
      <Collapse
        in={createEditOpen && open}
        unmountOnExit
        orientation="horizontal"
      >
        <PersonaCreateContent
          onCancel={handleCreateEditClose}
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
          preSelectedPersonas={preSelectedPersonas}
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
