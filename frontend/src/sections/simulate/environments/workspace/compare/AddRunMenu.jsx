import PropTypes from "prop-types";
import { useState } from "react";
import {
  Box,
  Button,
  Checkbox,
  Menu,
  MenuItem,
  Stack,
  Typography,
} from "@mui/material";
import SoonChip from "../../components/SoonChip";
import { RunLetter } from "./RunPills";
import { agentText, passText } from "./compareFormat";

const HEADING_SX = {
  px: 2,
  pt: 1,
  pb: 0.5,
  typography: "s3",
  fontWeight: "fontWeightSemiBold",
  color: "text.subtitle",
};

/**
 * Picks more runs to compare: every run of this environment not already in
 * the comparison, ticked and added in one go. Runs from other environments
 * aren't in the API yet, so their section says so.
 */
export default function AddRunMenu({ anchorEl, onClose, runs, capped, onAdd }) {
  const [picked, setPicked] = useState([]);
  const close = () => {
    setPicked([]);
    onClose();
  };
  const toggle = (id) =>
    setPicked((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id],
    );

  return (
    <Menu
      open={!!anchorEl}
      anchorEl={anchorEl}
      onClose={close}
      slotProps={{ paper: { sx: { maxHeight: 480, minWidth: 340 } } }}
    >
      {runs.length === 0 && (
        <Typography sx={{ ...HEADING_SX, fontWeight: "fontWeightRegular" }}>
          Every run of this environment is already here.
        </Typography>
      )}
      {runs.map((run) => {
        const on = picked.includes(run.id);
        return (
          <MenuItem
            key={run.id}
            selected={on}
            onClick={() => toggle(run.id)}
            sx={{ typography: "s2", gap: 0.5, py: 0.75 }}
          >
            <Checkbox
              size="small"
              checked={on}
              tabIndex={-1}
              sx={{ p: 0.5, mr: 0.5, pointerEvents: "none" }}
            />
            <RunLetter letter={run.letter} color={run.color} />
            <Box component="span" sx={{ flex: 1, minWidth: 0, ml: 1 }}>
              {`${run.label} · ${agentText(run)}`}
            </Box>
            <Box
              component="span"
              sx={{
                color: "text.subtitle",
                fontVariantNumeric: "tabular-nums",
                whiteSpace: "nowrap",
              }}
            >
              {passText(run)}
            </Box>
          </MenuItem>
        );
      })}
      {capped && (
        <Typography sx={{ ...HEADING_SX, fontWeight: "fontWeightRegular" }}>
          Showing the latest {runs.length} runs.
        </Typography>
      )}
      <Stack
        direction="row"
        alignItems="center"
        spacing={1}
        sx={{ px: 2, pt: 1, pb: 0.5 }}
      >
        <Typography sx={{ ...HEADING_SX, p: 0, flex: 1 }}>
          Other environments
        </Typography>
        <SoonChip
          label="Other environments"
          what="Runs from other environments"
          plural
          size="tiny"
        />
      </Stack>
      <Box
        sx={{
          position: "sticky",
          bottom: 0,
          borderTop: "1px solid",
          borderColor: "divider",
          bgcolor: "background.paper",
          px: 1.5,
          py: 1,
          mt: 0.5,
          display: "flex",
          alignItems: "center",
          gap: 1,
        }}
      >
        <Typography sx={{ typography: "s3", color: "text.subtitle", flex: 1 }}>
          {picked.length ? `${picked.length} selected` : "Pick runs to add"}
        </Typography>
        <Button size="small" onClick={close} sx={{ color: "text.secondary" }}>
          Cancel
        </Button>
        <Button
          size="small"
          variant="contained"
          color="primary"
          disabled={!picked.length}
          onClick={() => {
            onAdd(picked);
            close();
          }}
        >
          {picked.length ? `Add ${picked.length}` : "Add"}
        </Button>
      </Box>
    </Menu>
  );
}
AddRunMenu.propTypes = {
  anchorEl: PropTypes.any,
  onClose: PropTypes.func.isRequired,
  runs: PropTypes.array.isRequired,
  capped: PropTypes.bool,
  onAdd: PropTypes.func.isRequired,
};
