import { Box, Button, Divider, IconButton, Typography } from "@mui/material";
import PropTypes from "prop-types";
import SideDrawer from "src/sections/simulate-v2/components/SideDrawer";
import PersonaDrawerScale from "./PersonaDrawerScale";
import React, { useEffect, useState } from "react";
import { normalizePersona } from "./personaApiShape";
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
  title,
  subtitle,
  addLabel,
  single,
  current,
  carried,
  onSelectionChange,
}) => {
  /* Single mode starts on the persona already in use, if it came from the
     library — so what's being swapped out is visible. A selection carried
     over (from before Create persona, plus what was created) wins. */
  const [selectedPersonas, setSelectedPersonas] = useState(
    () =>
      carried ??
      (single
        ? current?.libraryId != null
          ? [{ id: current.libraryId, name: current.name }]
          : []
        : preSelectedPersonas ?? []),
  );
  useEffect(() => {
    onSelectionChange?.(selectedPersonas);
  }, [selectedPersonas]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleToggleSelect = (persona, newValue) => {
    if (single) {
      if (newValue) setSelectedPersonas([persona]);
      return;
    }
    setSelectedPersonas((prev) => {
      if (newValue) {
        return prev.some((p) => p.id === persona.id)
          ? prev
          : [...prev, persona];
      }
      return prev.filter((p) => p.id !== persona.id);
    });
  };

  const picked = selectedPersonas[0];
  const isCurrent =
    single &&
    picked &&
    current?.libraryId != null &&
    String(picked.id) === String(current.libraryId);
  const footerNote = !single
    ? `Personas selected (${selectedPersonas?.length})`
    : !picked
      ? "Pick one persona — a scenario has one caller."
      : isCurrent
        ? `${picked.name} is already this scenario's caller.`
        : current?.name
          ? `Replaces ${current.name} with ${picked.name}`
          : `Uses ${picked.name}`;

  /* The list remounts on the next open, so there's nothing to clear here. */
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
      {/* A header row of its own, so the close button sits beside the title
         rather than floating over the list's toolbar (Create persona). */}
      <Box
        sx={{
          display: "flex",
          alignItems: "flex-start",
          gap: 2,
          px: 2,
          pt: 2,
          pb: 1.5,
          borderBottom: "1px solid",
          borderColor: "divider",
          flexShrink: 0,
        }}
      >
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Typography typography="m2" fontWeight={600}>
            {title}
          </Typography>
          {subtitle && (
            <Typography typography="s1" color="text.secondary">
              {subtitle}
            </Typography>
          )}
        </Box>
        <IconButton
          onClick={onClose}
          aria-label="Close"
          size="small"
          sx={{ color: "text.primary", mt: -0.25 }}
        >
          <Iconify icon="solar:close-linear" width={20} />
        </IconButton>
      </Box>
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
          singleSelect={!!single}
          currentId={single ? current?.libraryId ?? null : null}
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
          {footerNote}
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
          disabled={selectedPersonas?.length === 0 || isCurrent}
          onClick={handleAddPersonas}
        >
          {addLabel}
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
  title: PropTypes.node,
  subtitle: PropTypes.node,
  addLabel: PropTypes.node,
  single: PropTypes.bool,
  current: PropTypes.object,
  carried: PropTypes.array,
  onSelectionChange: PropTypes.func,
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
        onSuccess={(res) => {
          const saved = res?.data?.result ?? res?.data ?? null;
          if (saved?.id != null) onCreated?.(normalizePersona(saved));
          onCancel();
        }}
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
  title = "Add personas",
  subtitle = "Pick from Future AGI built personas and your team's own.",
  addLabel = "Add",
  single = false,
  current = null,
}) => {
  const [createEditOpen, setCreateEditOpen] = useState(false);
  /* The list unmounts while Create persona is open; keep its selection, and
     come back with the new persona picked — it's the one you meant. */
  const [carried, setCarried] = useState(null);

  const handleDrawerClose = () => {
    onClose();
    setCreateEditOpen(false);
    setCarried(null);
  };

  const handleCreateEditClose = () => {
    setCreateEditOpen(false);
  };

  return (
    <SideDrawer open={open} onClose={handleDrawerClose} width="auto">
      <PersonaDrawerScale>
        <Collapse
          in={createEditOpen && open}
          unmountOnExit
          orientation="horizontal"
        >
          <PersonaCreateContent
            onCancel={handleCreateEditClose}
            onCreated={(saved) =>
              setCarried((prev) =>
                single ? [saved] : [...(prev || []), saved],
              )
            }
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
            onAddPersonas={(picked) => {
              setCarried(null);
              onAddPersonas(picked);
            }}
            onCreatePersona={() => setCreateEditOpen(true)}
            preSelectedPersonas={preSelectedPersonas}
            title={title}
            subtitle={subtitle}
            addLabel={addLabel}
            single={single}
            current={current}
            carried={carried}
            onSelectionChange={setCarried}
          />
        </Collapse>
      </PersonaDrawerScale>
    </SideDrawer>
  );
};

PersonaDrawer.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  onAddPersonas: PropTypes.func,
  personaCreateEditType: PropTypes.string,
  lockedFilters: PropTypes.object,
  preSelectedPersonas: PropTypes.array,
  title: PropTypes.node,
  subtitle: PropTypes.node,
  addLabel: PropTypes.node,
  single: PropTypes.bool,
  current: PropTypes.object,
};

export default PersonaDrawer;
