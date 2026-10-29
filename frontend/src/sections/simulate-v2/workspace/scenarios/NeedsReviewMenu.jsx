import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Stack, Typography, Button, Popover } from "@mui/material";
import Iconify from "src/components/iconify";

/*
  The notices that used to stack above the scenario table — the change
  review (Outdated / Re-check), proposals waiting for approval, scenarios
  improvements wrote, the quality review — as one quiet toolbar button.
  It opens a list with one compact row per notice and that notice's own
  actions, so nothing is lost and nothing stacks over the table.

  `notices`: [{ id, icon, title, sub, attention, actions: [{ label, onClick,
  primary, disabled }] }]. `attention` counts toward the button's number;
  a notice without it (a quality review that's ready) is listed but quiet.
  Every action closes the list first, then runs.
*/
export default function NeedsReviewMenu({ notices, compact = false }) {
  const [anchor, setAnchor] = useState(null);
  if (!notices.length) return null;
  const count = notices.filter((n) => n.attention).length;
  return (
    <>
      <Button
        size="small"
        variant="outlined"
        onClick={(e) => setAnchor(e.currentTarget)}
        startIcon={
          <Iconify
            icon={count ? "solar:eye-linear" : "solar:shield-check-linear"}
            width={14}
          />
        }
        sx={{
          flexShrink: 0,
          whiteSpace: "nowrap",
          color: "text.primary",
          borderColor: anchor ? "text.primary" : "divider",
          bgcolor: anchor ? "action.selected" : "transparent",
        }}
      >
        {compact
          ? count
            ? "Review"
            : "Quality"
          : count
            ? "Needs review"
            : "Quality review"}
        {count > 0 && (
          <Box
            component="span"
            sx={{
              ml: 0.75,
              px: 0.625,
              minWidth: 18,
              height: 18,
              borderRadius: "4px",
              display: "inline-flex",
              alignItems: "center",
              justifyContent: "center",
              typography: "s3",
              fontWeight: 600,
              fontVariantNumeric: "tabular-nums",
              color: "text.secondary",
              bgcolor: "action.selected",
            }}
          >
            {count}
          </Box>
        )}
      </Button>
      <Popover
        open={!!anchor}
        anchorEl={anchor}
        onClose={() => setAnchor(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
        transformOrigin={{ vertical: "top", horizontal: "left" }}
        slotProps={{
          paper: {
            sx: {
              width: 460,
              maxWidth: "calc(100vw - 32px)",
              mt: 0.5,
              py: 0.5,
            },
          },
        }}
      >
        <Stack
          divider={
            <Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />
          }
        >
          {notices.map((n) => (
            <Stack
              key={n.id}
              direction="row"
              alignItems="center"
              spacing={1.25}
              sx={{ px: 1.75, py: 1 }}
            >
              <Iconify
                icon={n.icon}
                width={16}
                sx={{
                  color: "text.tertiary",
                  flexShrink: 0,
                  alignSelf: "flex-start",
                  mt: "2px",
                }}
              />
              <Box flex={1} minWidth={0}>
                <Typography
                  component="div"
                  sx={{ typography: "s2", fontWeight: 600 }}
                >
                  {n.title}
                </Typography>
                {n.sub && (
                  <Typography
                    component="div"
                    sx={{
                      typography: "s3",
                      color: "text.secondary",
                      mt: 0.125,
                    }}
                  >
                    {n.sub}
                  </Typography>
                )}
              </Box>
              <Stack direction="row" spacing={0.75} sx={{ flexShrink: 0 }}>
                {n.actions.map((a) => (
                  <Button
                    key={a.label}
                    size="small"
                    variant={a.primary ? "contained" : "outlined"}
                    color={a.primary ? "primary" : "inherit"}
                    disabled={a.disabled}
                    onClick={() => {
                      setAnchor(null);
                      a.onClick();
                    }}
                    sx={{
                      whiteSpace: "nowrap",
                      ...(!a.primary && {
                        color: "text.primary",
                        borderColor: "divider",
                      }),
                    }}
                  >
                    {a.label}
                  </Button>
                ))}
              </Stack>
            </Stack>
          ))}
        </Stack>
      </Popover>
    </>
  );
}
NeedsReviewMenu.propTypes = {
  compact: PropTypes.bool,
  notices: PropTypes.array.isRequired,
};
