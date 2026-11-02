import { useState } from "react";
import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import {
  Box,
  Button,
  Divider,
  IconButton,
  Popover,
  Stack,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { ConfirmDialog } from "src/components/custom-dialog";
import { SECTIONS } from "./panelRegistry";

// Anchored to the toolbar's "Hidden (N)" button: the widgets hidden in this
// view, grouped by section in page order, each with Show. Reset layout closes
// the popover and asks before it puts the view back to the default.
export default function HiddenWidgetsPopover({
  anchorEl,
  onClose,
  hidden,
  sectionOrder,
  onShow,
  onRemove,
  onReset,
}) {
  const [confirmReset, setConfirmReset] = useState(false);
  const groups = sectionOrder
    .map((sectionId) => ({
      id: sectionId,
      label:
        SECTIONS.find((section) => section.id === sectionId)?.label ||
        "Custom widgets",
      items: hidden.filter((item) => item.section === sectionId),
    }))
    .filter((group) => group.items.length);

  return (
    <>
      <Popover
        open={Boolean(anchorEl)}
        anchorEl={anchorEl}
        onClose={onClose}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
        slotProps={{
          paper: {
            sx: {
              width: 320,
              mt: 0.5,
              borderRadius: 1,
              border: "1px solid",
              borderColor: "divider",
            },
          },
        }}
      >
        <Box sx={{ px: 1.5, pt: 1.25, pb: 1 }}>
          <Typography typography="s1" sx={{ fontWeight: 600 }}>
            Hidden widgets
          </Typography>
          <Typography typography="s2" sx={{ color: "text.secondary" }}>
            {hidden.length
              ? `${hidden.length} hidden in this view`
              : "Nothing is hidden in this view."}
          </Typography>
        </Box>
        {groups.length > 0 && (
          <>
            <Divider />
            <Box sx={{ maxHeight: 360, overflowY: "auto", py: 0.5 }}>
              {groups.map((group) => (
                <Box key={group.id} sx={{ py: 0.5 }}>
                  <Typography
                    typography="s2"
                    sx={{ px: 1.5, pb: 0.25, fontWeight: 600 }}
                    color="text.subtitle"
                  >
                    {group.label}
                  </Typography>
                  {group.items.map((item) => (
                    <Stack
                      key={item.id}
                      direction="row"
                      alignItems="center"
                      spacing={1}
                      sx={{
                        px: 1.5,
                        py: 0.5,
                        "&:hover": { bgcolor: "action.hover" },
                      }}
                    >
                      <Typography
                        noWrap
                        typography="s1"
                        sx={{ flex: 1, minWidth: 0 }}
                      >
                        {item.title}
                      </Typography>
                      {item.custom && (
                        <CustomTooltip
                          show
                          arrow
                          size="small"
                          title="Delete widget permanently"
                        >
                          <IconButton
                            size="small"
                            aria-label={`Delete ${item.title}`}
                            onClick={() => onRemove(item.id)}
                            sx={{
                              color: "text.subtitle",
                              "&:hover": { color: "error.main" },
                            }}
                          >
                            <Iconify
                              icon="solar:trash-bin-trash-linear"
                              width={14}
                            />
                          </IconButton>
                        </CustomTooltip>
                      )}
                      <Button
                        size="small"
                        variant="outlined"
                        onClick={() => onShow(item.id)}
                        startIcon={
                          <Iconify icon="solar:eye-linear" width={14} />
                        }
                        sx={{
                          flexShrink: 0,
                          borderColor: "divider",
                          color: "text.primary",
                          "&:hover": {
                            bgcolor: "transparent",
                            borderColor: (theme) =>
                              alpha(theme.palette.text.primary, 0.4),
                          },
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
          <Button
            size="small"
            onClick={() => {
              setConfirmReset(true);
              onClose();
            }}
            startIcon={<Iconify icon="solar:refresh-linear" width={14} />}
            sx={{ color: "text.secondary" }}
          >
            Reset layout
          </Button>
        </Box>
      </Popover>
      <ConfirmDialog
        open={confirmReset}
        onClose={() => setConfirmReset(false)}
        title="Reset layout"
        content="Reset this view to the default layout?"
        action={
          <Button
            size="small"
            variant="contained"
            color="error"
            onClick={() => {
              setConfirmReset(false);
              onReset();
            }}
          >
            Reset
          </Button>
        }
      />
    </>
  );
}
HiddenWidgetsPopover.propTypes = {
  anchorEl: PropTypes.any,
  onClose: PropTypes.func.isRequired,
  hidden: PropTypes.arrayOf(
    PropTypes.shape({
      id: PropTypes.string.isRequired,
      title: PropTypes.string.isRequired,
      section: PropTypes.string.isRequired,
      custom: PropTypes.bool,
    }),
  ).isRequired,
  sectionOrder: PropTypes.arrayOf(PropTypes.string).isRequired,
  onShow: PropTypes.func.isRequired,
  onRemove: PropTypes.func.isRequired,
  onReset: PropTypes.func.isRequired,
};
