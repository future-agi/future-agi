/* eslint-disable react/prop-types */
import React, { useState, useMemo } from "react";
import {
  Drawer,
  Stack,
  Typography,
  IconButton,
  List,
  ListItem,
  Button,
  Box,
  Skeleton,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
} from "@mui/material";
import Iconify from "src/components/iconify";
import {
  useOrgConfigHistory,
  useActivateOrgConfig,
} from "../providers/hooks/useOrgConfig";
import { coveredDrawerSx, useDrawerCovered } from "src/hooks/use-drawer-stack";

const ConfigHistoryDrawer = ({ open, onClose }) => {
  const { data: history, isLoading } = useOrgConfigHistory();
  const activateMutation = useActivateOrgConfig();
  const [viewConfig, setViewConfig] = useState(null);

  const versions = useMemo(() => {
    if (!history) return [];
    const arr = Array.isArray(history) ? history : [];
    return [...arr].sort((a, b) => (b.version || 0) - (a.version || 0));
  }, [history]);

  const handleActivate = (id) => {
    activateMutation.mutate(id);
  };

  const covered = useDrawerCovered(open);

  return (
    <>
      <Drawer
        sx={coveredDrawerSx(covered)}
        anchor="right"
        open={open}
        onClose={onClose}
        PaperProps={{ sx: { width: 480, p: 2 } }}
      >
        <Stack
          direction="row"
          justifyContent="space-between"
          alignItems="center"
          mb={2}
        >
          <Typography variant="h6">Config History</Typography>
          <IconButton onClick={onClose}>
            <Iconify icon="solar:close-linear" width={20} />
          </IconButton>
        </Stack>

        {isLoading ? (
          <Stack spacing={1}>
            {[...Array(5)].map((_, i) => (
              <Skeleton key={i} variant="rounded" height={48} />
            ))}
          </Stack>
        ) : versions.length === 0 ? (
          <Typography
            variant="body2"
            color="text.secondary"
            textAlign="center"
            sx={{ mt: 4 }}
          >
            No config history yet.
          </Typography>
        ) : (
          <List disablePadding>
            {versions.map((cfg) => (
              <ListItem
                key={cfg.id}
                sx={{
                  borderBottom: 1,
                  borderColor: "border.subtle",
                  py: 1,
                  px: 1.5,
                  flexDirection: "column",
                  alignItems: "flex-start",
                }}
              >
                <Stack
                  direction="row"
                  justifyContent="space-between"
                  width="100%"
                  alignItems="center"
                >
                  <Stack direction="row" spacing={1} alignItems="center">
                    <Typography sx={{ fontSize: 13, fontWeight: 500 }}>
                      v{cfg.version}
                    </Typography>
                    {cfg.is_active && (
                      <Typography
                        component="span"
                        sx={{ fontSize: 12, color: "status.success.fg" }}
                      >
                        Active
                      </Typography>
                    )}
                  </Stack>
                  <Typography variant="caption" color="text.secondary">
                    {cfg.created_at
                      ? new Date(cfg.created_at).toLocaleDateString()
                      : ""}
                  </Typography>
                </Stack>
                {cfg.change_description && (
                  <Typography
                    variant="caption"
                    color="text.tertiary"
                    sx={{ mt: 0.25 }}
                  >
                    {cfg.change_description}
                  </Typography>
                )}
                <Stack direction="row" spacing={1} sx={{ mt: 0.5 }}>
                  <Button
                    size="small"
                    variant="text"
                    onClick={() => setViewConfig(cfg)}
                  >
                    View
                  </Button>
                  {!cfg.is_active && (
                    <Button
                      size="small"
                      variant="outlined"
                      onClick={() => handleActivate(cfg.id)}
                      disabled={activateMutation.isPending}
                    >
                      Activate
                    </Button>
                  )}
                </Stack>
              </ListItem>
            ))}
          </List>
        )}
      </Drawer>

      {/* View config dialog */}
      <Dialog
        open={Boolean(viewConfig)}
        onClose={() => setViewConfig(null)}
        maxWidth={false}
        PaperProps={{ sx: { maxWidth: 720 } }}
        fullWidth
      >
        <DialogTitle>Config v{viewConfig?.version}</DialogTitle>
        <DialogContent>
          <Box
            sx={{
              bgcolor: "action.hover",
              borderRadius: 1,
              p: 2,
              fontFamily: "monospace",
              fontSize: "0.75rem",
              whiteSpace: "pre-wrap",
              maxHeight: 400,
              overflow: "auto",
            }}
          >
            {JSON.stringify(
              {
                guardrails: viewConfig?.guardrails,
                alerting: viewConfig?.alerting,
                budgets: viewConfig?.budgets,
                routing: viewConfig?.routing,
                cache: viewConfig?.cache,
              },
              null,
              2,
            )}
          </Box>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setViewConfig(null)}>Close</Button>
        </DialogActions>
      </Dialog>
    </>
  );
};

export default ConfigHistoryDrawer;
