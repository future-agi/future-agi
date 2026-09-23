import { useState } from "react";
import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import { Box, Button, Divider, IconButton, Popover, Stack, Tooltip, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { SECTIONS, getPanelMeta, panelSection, panelTitle } from "./panelRegistry";

/*
  Anchored to the toolbar's "Hidden (n)" button: just the widgets hidden in
  the current view, grouped by section in page order, each with Show.
  Reorder / remove / add already live on the page itself.
*/
export default function HiddenWidgetsPopover({ anchorEl, onClose, layout }) {
  const [confirmReset, setConfirmReset] = useState(false);
  const { hiddenIds, customWidgets, sectionOrder, sectionOverrides, show, removeCustomWidget, reset, getOverride } = layout;

  const findCustom = (id) => customWidgets.find((w) => w.id === id);
  const titleOf = (id) => getOverride(id)?.title || findCustom(id)?.title || panelTitle(id) || id;
  const sectionOf = (id) => sectionOverrides[id] || (getPanelMeta(id) ? panelSection(id) : "custom");

  const groups = sectionOrder
    .map((sid) => ({
      id: sid,
      label: SECTIONS.find((s) => s.id === sid)?.label || "Custom widgets",
      ids: hiddenIds.filter((id) => sectionOf(id) === sid),
    }))
    .filter((g) => g.ids.length > 0);

  const handleClose = () => { setConfirmReset(false); onClose(); };

  return (
    <Popover
      open={Boolean(anchorEl)}
      anchorEl={anchorEl}
      onClose={handleClose}
      anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
      transformOrigin={{ vertical: "top", horizontal: "right" }}
      slotProps={{
        paper: {
          sx: {
            width: 320, mt: 0.5, borderRadius: "4px",
            border: "1px solid", borderColor: "divider",
            boxShadow: "1px 1px 12px 10px rgba(0,0,0,0.04)",
          },
        },
      }}
    >
      <Box sx={{ px: 1.5, pt: 1.25, pb: 1 }}>
        <Typography sx={{ fontSize: 14, fontWeight: 600 }}>Hidden widgets</Typography>
        <Typography sx={{ fontSize: 12, color: "text.secondary" }}>
          {hiddenIds.length
            ? `${hiddenIds.length} hidden in this view`
            : "Nothing is hidden in this view."}
        </Typography>
      </Box>

      {groups.length > 0 && (
        <>
          <Divider />
          <Box sx={{ maxHeight: 360, overflowY: "auto", py: 0.5 }}>
            {groups.map((g) => (
              <Box key={g.id} sx={{ py: 0.5 }}>
                <Typography sx={{
                  px: 1.5, pb: 0.25, fontSize: 10.5, fontWeight: 700,
                  letterSpacing: 0.5, textTransform: "uppercase", color: "text.subtitle",
                }}>
                  {g.label}
                </Typography>
                {g.ids.map((id) => (
                  <Stack
                    key={id} direction="row" alignItems="center" spacing={1}
                    sx={{ px: 1.5, py: 0.5, "&:hover": { bgcolor: "action.hover" } }}
                  >
                    <Typography noWrap sx={{ flex: 1, minWidth: 0, fontSize: 13 }}>{titleOf(id)}</Typography>
                    {findCustom(id) && (
                      <Tooltip arrow title="Delete widget permanently">
                        <IconButton
                          size="small" onClick={() => removeCustomWidget(id)} aria-label="Delete widget"
                          sx={{ width: 24, height: 24, color: "text.subtitle", "&:hover": { color: "#DC2626" } }}
                        >
                          <Iconify icon="solar:trash-bin-trash-linear" width={14} />
                        </IconButton>
                      </Tooltip>
                    )}
                    <Button
                      size="small" variant="outlined" onClick={() => show(id)}
                      startIcon={<Iconify icon="solar:eye-linear" width={14} />}
                      sx={{
                        height: 24, px: 1, fontSize: 12, fontWeight: 500, textTransform: "none",
                        borderRadius: "4px", borderColor: "divider", color: "text.primary", flexShrink: 0,
                        "&:hover": { borderColor: (t) => alpha(t.palette.text.primary, 0.4), bgcolor: "transparent" },
                      }}
                    >
                      Show
                    </Button>
                  </Stack>
                ))}
              </Box>
            ))}
          </Box>
        </>
      )}

      <Divider />
      <Box sx={{ px: 1.5, py: 1 }}>
        {confirmReset ? (
          <Stack direction="row" alignItems="center" spacing={1}>
            <Typography sx={{ flex: 1, fontSize: 12, color: "text.secondary" }}>
              Reset this view to the default layout?
            </Typography>
            <Button size="small" onClick={() => setConfirmReset(false)} sx={{ textTransform: "none", fontSize: 12 }}>
              Cancel
            </Button>
            <Button
              size="small" color="error" variant="contained"
              onClick={() => { reset(); handleClose(); }}
              sx={{ textTransform: "none", fontSize: 12 }}
            >
              Reset
            </Button>
          </Stack>
        ) : (
          <Button
            size="small" onClick={() => setConfirmReset(true)}
            startIcon={<Iconify icon="solar:refresh-linear" width={14} />}
            sx={{ textTransform: "none", fontSize: 12, color: "text.secondary", px: 0.5 }}
          >
            Reset layout
          </Button>
        )}
      </Box>
    </Popover>
  );
}

HiddenWidgetsPopover.propTypes = {
  anchorEl: PropTypes.any,
  onClose: PropTypes.func.isRequired,
  layout: PropTypes.object.isRequired,
};
