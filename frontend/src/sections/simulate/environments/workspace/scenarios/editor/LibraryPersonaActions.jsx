import PropTypes from "prop-types";
import { Suspense, lazy, useEffect, useState } from "react";
import { Box, Button, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { EDITOR_COPY } from "../scenarioEditor.constants";

// Loaded on first open: the persona list and form are heavy and pull in the
// app snackbar, which the editor otherwise never needs.
const PersonaDrawer = lazy(() => import("src/sections/persona/PersonaDrawer"));
const PersonaCreateEditDrawer = lazy(
  () =>
    import("src/sections/persona/PersonaCreateEdit/PersonaCreateEditDrawer"),
);

const outlinedSx = {
  color: "text.primary",
  borderColor: "divider",
  flexShrink: 0,
  typography: "s2",
  fontWeight: "fontWeightSemiBold",
};

// The way to and from the workspace persona library (Environments, Personas).
// "Use a library persona" opens the product's persona picker; the first pick
// fills this scenario's writable persona fields. "Save to library" opens the
// product's Create persona drawer. That drawer has no create-mode prefill
// (passing a persona turns it into an edit of that persona), so it opens
// empty.
export default function LibraryPersonaActions({
  canPick,
  canSave,
  personaType,
  note,
  onPick,
}) {
  const [picking, setPicking] = useState(false);
  const [saving, setSaving] = useState(false);
  // Once opened, a drawer stays mounted so it can animate closed.
  const [pickerLoaded, setPickerLoaded] = useState(false);
  const [creatorLoaded, setCreatorLoaded] = useState(false);
  // The create drawer asks for voice or chat when the environment doesn't say.
  const [createType, setCreateType] = useState(personaType);
  useEffect(() => setCreateType(personaType), [personaType]);

  if (!canPick && !canSave) return null;

  return (
    <Stack spacing={0.75}>
      <Stack
        direction="row"
        alignItems="center"
        spacing={1}
        flexWrap="wrap"
        rowGap={1}
      >
        <Box flex={1} />
        {canPick && (
          <Button
            size="small"
            variant="outlined"
            onClick={() => {
              setPickerLoaded(true);
              setPicking(true);
            }}
            startIcon={
              <Iconify icon="solar:users-group-rounded-linear" width={16} />
            }
            sx={outlinedSx}
          >
            {EDITOR_COPY.useLibrary}
          </Button>
        )}
        {canSave && (
          <Button
            size="small"
            variant="outlined"
            onClick={() => {
              setCreateType(personaType);
              setCreatorLoaded(true);
              setSaving(true);
            }}
            startIcon={<Iconify icon="solar:bookmark-linear" width={16} />}
            sx={outlinedSx}
          >
            {EDITOR_COPY.saveToLibrary}
          </Button>
        )}
      </Stack>
      <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
        {note || EDITOR_COPY.libraryNote}
      </Typography>

      <Suspense fallback={null}>
        {pickerLoaded && (
          <PersonaDrawer
            open={picking}
            onClose={() => setPicking(false)}
            personaCreateEditType={personaType}
            onAddPersonas={(picked) => {
              setPicking(false);
              if (picked?.length) onPick(picked[0]);
            }}
          />
        )}
        {creatorLoaded && (
          <PersonaCreateEditDrawer
            open={saving}
            onClose={() => setSaving(false)}
            personaCreateEditType={createType}
            updatePersonaType={setCreateType}
          />
        )}
      </Suspense>
    </Stack>
  );
}
LibraryPersonaActions.propTypes = {
  canPick: PropTypes.bool,
  canSave: PropTypes.bool,
  personaType: PropTypes.string,
  note: PropTypes.string,
  onPick: PropTypes.func.isRequired,
};
