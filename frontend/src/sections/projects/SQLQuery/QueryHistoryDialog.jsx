import React, { useId } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Dialog,
  DialogContent,
  IconButton,
  List,
  ListItemButton,
  Stack,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { relativeTime } from "src/utils/format-time";
import { MetaLine, StatePill } from "./queryUi";

export default function QueryHistoryDialog({
  open,
  onClose,
  history,
  onSelect,
}) {
  const titleId = useId();
  return (
    <Dialog
      open={open}
      aria-labelledby={titleId}
      onClose={onClose}
      fullWidth
      maxWidth="sm"
      PaperProps={{ sx: { borderRadius: 1, p: 2, gap: 1.5 } }}
    >
      <Stack direction="row" alignItems="flex-start" gap={1}>
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Typography
            component="h2"
            id={titleId}
            sx={{ typography: "m3", fontWeight: "fontWeightSemiBold" }}
          >
            Recent queries
          </Typography>
          <Typography sx={{ typography: "s1", color: "text.secondary" }}>
            Your last 20 runs in this project. Select one to open it in the
            editor.
          </Typography>
        </Box>
        <IconButton
          aria-label="Close"
          onClick={onClose}
          sx={{ color: "text.primary", mt: -0.5, mr: -0.5 }}
        >
          <Iconify icon="akar-icons:cross" />
        </IconButton>
      </Stack>
      <DialogContent sx={{ p: 0 }}>
        {history.length ? (
          <List
            disablePadding
            aria-label="Recent queries"
            sx={{ border: 1, borderColor: "divider", borderRadius: 1 }}
          >
            {history.map((item, index) => (
              <ListItemButton
                key={item.id}
                onClick={() => onSelect(item.sql)}
                sx={{
                  display: "block",
                  px: 1.5,
                  py: 1.25,
                  borderTop: index ? 1 : 0,
                  borderColor: "divider",
                  "&:hover .history-open": { opacity: 1 },
                }}
              >
                <Stack
                  direction="row"
                  alignItems="center"
                  gap={1}
                  sx={{ mb: 0.75 }}
                >
                  <StatePill state={item.state}>{item.state}</StatePill>
                  <MetaLine
                    parts={[
                      relativeTime(item.created_at),
                      item.expired && "results expired",
                    ]}
                    title={new Date(item.created_at).toLocaleString()}
                    sx={{ flex: 1 }}
                  />
                  <Typography
                    className="history-open"
                    sx={{
                      typography: "s3",
                      fontWeight: "fontWeightSemiBold",
                      color: "text.secondary",
                      opacity: 0,
                      transition: "opacity 120ms",
                    }}
                  >
                    Open in editor
                  </Typography>
                </Stack>
                <Typography
                  title={item.sql}
                  sx={{
                    typography: "s2",
                    fontFamily: "monospace",
                    color: "text.primary",
                    whiteSpace: "pre-wrap",
                    overflowWrap: "anywhere",
                    display: "-webkit-box",
                    WebkitLineClamp: 3,
                    WebkitBoxOrient: "vertical",
                    overflow: "hidden",
                  }}
                >
                  {item.sql}
                </Typography>
                {item.error && (
                  <Typography
                    sx={{ typography: "s3", color: "error.main", mt: 0.5 }}
                  >
                    {item.error}
                  </Typography>
                )}
              </ListItemButton>
            ))}
          </List>
        ) : (
          <Box sx={{ py: 4, textAlign: "center" }}>
            <Typography
              sx={{ typography: "s2", fontWeight: "fontWeightSemiBold" }}
            >
              No queries yet
            </Typography>
            <Typography sx={{ typography: "s3", color: "text.secondary" }}>
              Queries you run in this project appear here.
            </Typography>
          </Box>
        )}
      </DialogContent>
    </Dialog>
  );
}

QueryHistoryDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  onSelect: PropTypes.func.isRequired,
  history: PropTypes.arrayOf(
    PropTypes.shape({
      id: PropTypes.string.isRequired,
      sql: PropTypes.string.isRequired,
      state: PropTypes.string.isRequired,
      created_at: PropTypes.string.isRequired,
      expired: PropTypes.bool,
      error: PropTypes.string,
    }),
  ).isRequired,
};
