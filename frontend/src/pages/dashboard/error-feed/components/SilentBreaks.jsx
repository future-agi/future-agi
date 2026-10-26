import { useState } from "react";
import PropTypes from "prop-types";
import { useNavigate } from "react-router-dom";
import {
  Box,
  Button,
  Drawer,
  IconButton,
  Stack,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { useSilentBreaks } from "src/api/errorFeed/mockAdapter";
import {
  clickable,
  focusRing,
} from "src/sections/simulate-v2/components/clickable";

const ago = (m) =>
  m < 60
    ? `${m}m ago`
    : m < 1440
      ? `${Math.round(m / 60)}h ago`
      : `${Math.round(m / 1440)}d ago`;

/*
  Conversations that broke a requirement but never grouped into an issue —
  the feed shows them as clean. One line above the list; the list in a drawer.
*/
export default function SilentBreaks({ projectId }) {
  const rows = useSilentBreaks(projectId);
  const [open, setOpen] = useState(false);
  const navigate = useNavigate();
  if (!rows.length) return null;
  return (
    <>
      <Stack
        direction="row"
        alignItems="center"
        gap={1}
        sx={{
          px: 1.5,
          py: 0.75,
          borderRadius: "8px",
          border: "1px solid",
          borderColor: "divider",
          bgcolor: "background.paper",
          flexShrink: 0,
          minWidth: 0,
        }}
      >
        <Iconify
          icon="solar:shield-warning-linear"
          width={16}
          sx={{ color: "status.warning.fg", flexShrink: 0 }}
        />
        <Typography
          fontSize="13px"
          color="text.secondary"
          noWrap
          sx={{ minWidth: 0 }}
        >
          <Box
            component="span"
            sx={{
              fontWeight: 600,
              color: "text.primary",
              fontVariantNumeric: "tabular-nums",
            }}
          >
            {rows.length}
          </Box>
          {` conversation${rows.length === 1 ? "" : "s"} broke a requirement without becoming an issue`}
        </Typography>
        <Box sx={{ flex: 1 }} />
        <Button
          size="small"
          variant="outlined"
          onClick={() => setOpen(true)}
          sx={{ flexShrink: 0, color: "text.primary", borderColor: "divider" }}
        >
          Review
        </Button>
      </Stack>

      <Drawer
        anchor="right"
        open={open}
        onClose={() => setOpen(false)}
        PaperProps={{ sx: { width: { xs: "100%", sm: 520 } } }}
      >
        <Stack
          direction="row"
          alignItems="center"
          gap={1}
          sx={{
            px: 2.5,
            py: 1.75,
            borderBottom: "1px solid",
            borderColor: "divider",
          }}
        >
          <Box sx={{ minWidth: 0, flex: 1 }}>
            <Typography sx={{ typography: "m3", fontWeight: 600 }}>
              Broke a requirement, no issue
            </Typography>
            <Typography fontSize="12px" color="text.tertiary">
              Each broke something it was supposed to do but didn&apos;t group
              into an issue.
            </Typography>
          </Box>
          <IconButton
            size="small"
            aria-label="Close"
            onClick={() => setOpen(false)}
          >
            <Iconify icon="solar:close-linear" width={18} />
          </IconButton>
        </Stack>
        <Box sx={{ overflowY: "auto" }}>
          {rows.map((r) => (
            <Box
              key={r.id}
              sx={{
                px: 2.5,
                py: 1.25,
                borderBottom: "1px solid",
                borderColor: "divider",
              }}
            >
              <Stack
                direction="row"
                alignItems="center"
                gap={1}
                sx={{ minWidth: 0 }}
              >
                <Iconify
                  icon="solar:close-circle-linear"
                  width={16}
                  sx={{ color: "status.danger.fg", flexShrink: 0 }}
                />
                <Typography
                  fontSize="13px"
                  color="text.primary"
                  noWrap
                  sx={{ minWidth: 0, flex: 1 }}
                >
                  {r.requirement}
                </Typography>
                <Typography
                  fontSize="12px"
                  color="text.tertiary"
                  noWrap
                  sx={{ flexShrink: 0 }}
                >
                  {ago(r.minutesAgo)}
                </Typography>
              </Stack>
              <Typography
                fontSize="12px"
                color="text.tertiary"
                noWrap
                sx={{ pl: 3 }}
              >
                {`${r.failureType} · ${r.traceId} · close to `}
                <Box
                  component="span"
                  {...clickable(
                    () => navigate(`/dashboard/error-feed/${r.nearIssue}`),
                    { role: "link" },
                  )}
                  sx={{
                    ...focusRing,
                    color: "text.secondary",
                    fontWeight: 500,
                    cursor: "pointer",
                    "&:hover": { textDecoration: "underline" },
                  }}
                >
                  {r.nearIssue}
                </Box>
              </Typography>
            </Box>
          ))}
        </Box>
      </Drawer>
    </>
  );
}

SilentBreaks.propTypes = { projectId: PropTypes.string };
