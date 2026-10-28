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
  /* By the issue each one resembles — the one to open and compare. */
  const groups = Object.values(
    rows.reduce((acc, r) => {
      const g = (acc[r.nearIssue] ||= {
        issue: r.nearIssue,
        name: r.nearIssueName,
        requirement: r.requirement,
        latest: r.minutesAgo,
        rows: [],
      });
      g.rows.push(r);
      g.latest = Math.min(g.latest, r.minutesAgo);
      return acc;
    }, {}),
  ).sort((a, b) => b.rows.length - a.rows.length || a.latest - b.latest);
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
          {` conversation${rows.length === 1 ? "" : "s"} broke a requirement but didn't join an issue, so no fix covers ${rows.length === 1 ? "it" : "them"}`}
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
              Each looks like an existing issue but wasn&apos;t close enough to
              join it. A fix only covers its issue&apos;s conversations — open
              the issue to check whether these are the same failure.
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
          {groups.map((g) => (
            <Box
              key={g.issue}
              sx={{
                px: 2.5,
                py: 1.5,
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
                <Box sx={{ minWidth: 0, flex: 1 }}>
                  <Typography
                    fontSize="13px"
                    fontWeight={500}
                    color="text.primary"
                    noWrap
                  >
                    {`${g.issue} · ${g.name || g.requirement}`}
                  </Typography>
                  <Typography fontSize="12px" color="text.tertiary" noWrap>
                    {`${g.rows.length} conversation${g.rows.length === 1 ? "" : "s"} · broke "${g.requirement}" · latest ${ago(g.latest)}`}
                  </Typography>
                </Box>
                <Button
                  size="small"
                  variant="outlined"
                  onClick={() => {
                    setOpen(false);
                    navigate(`/dashboard/error-feed/${g.issue}`);
                  }}
                  sx={{
                    flexShrink: 0,
                    color: "text.primary",
                    borderColor: "divider",
                  }}
                >
                  Open issue
                </Button>
              </Stack>
            </Box>
          ))}
        </Box>
      </Drawer>
    </>
  );
}

SilentBreaks.propTypes = { projectId: PropTypes.string };
