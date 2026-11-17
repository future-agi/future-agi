// The dock's one `More ▾` (R2-SPEC §0.6 budget): the quick-test toggle and
// Start over. L7c g1: no `Limits: max iterations` (the cap did not drive the
// tries, cost or estimate) and no `Copy as API call` (the capability contract a: the
// printed POST .../experiments/v2/<id>/runs/ is not a real API route).
import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Button,
  Menu,
  MenuItem,
  Stack,
  Switch,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { MAX_QUICK_TEST_ROWS } from "./iterateModel";
import { useIterateStore } from "./iterateStore";

// `onCopyApi` is accepted and ignored until IterateDock drops the prop (g4).
const DockMoreMenu = ({ dock }) => {
  const [anchor, setAnchor] = useState(null);
  const { parent, draft, patch, canEdit, quickTest } = dock;
  return (
    <>
      <Button
        size="small"
        color="inherit"
        endIcon={<Iconify icon="mdi:chevron-down" width={16} />}
        onClick={(e) => setAnchor(e.currentTarget)}
        sx={{ typography: "s2", color: "text.secondary", minWidth: 0 }}
      >
        More
      </Button>
      <Menu
        anchorEl={anchor}
        open={Boolean(anchor)}
        onClose={() => setAnchor(null)}
        anchorOrigin={{ vertical: "top", horizontal: "right" }}
        transformOrigin={{ vertical: "bottom", horizontal: "right" }}
        slotProps={{ paper: { sx: { minWidth: 300 } } }}
      >
        <MenuItem
          disabled={!canEdit}
          onClick={() => patch({ quickTest: !quickTest })}
          sx={{ gap: 1 }}
        >
          <Switch size="small" checked={quickTest} tabIndex={-1} />
          <Stack>
            <Typography typography="s2">
              Quick test (not listed on the leaderboard)
            </Typography>
            <Typography typography="s3" color="text.secondary">
              {`Up to ${MAX_QUICK_TEST_ROWS} rows, 1 trial`}
            </Typography>
          </Stack>
        </MenuItem>
        {/* v2.1 g4 (action:dock:start-over): any unrun draft (model, params,
            tools, limits, node prompts) can start over; resetDraft resets all. */}
        {draft.ran || (dock.changes ?? []).length ? (
          // P3: the draft is kept across opens; this is the way back.
          <MenuItem
            sx={{ typography: "s2", gap: 1 }}
            data-testid="iterate-start-over"
            onClick={() => {
              setAnchor(null);
              useIterateStore.getState().resetDraft(parent.run_id);
            }}
          >
            <Iconify icon="mdi:restore" width={16} />
            {`Start over from ${dock.label}'s config`}
          </MenuItem>
        ) : null}
      </Menu>
    </>
  );
};

DockMoreMenu.propTypes = {
  dock: PropTypes.object.isRequired,
  onCopyApi: PropTypes.func,
};

export default DockMoreMenu;
