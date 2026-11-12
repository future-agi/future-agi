/* eslint-disable react/prop-types */
import React, { useState, useMemo, useId } from "react";
import {
  Alert,
  Stack,
  Drawer,
  IconButton,
  Typography,
  List,
  ListItem,
  Button,
  Box,
  Skeleton,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  TextField,
  MenuItem,
} from "@mui/material";
import { useQueryClient } from "@tanstack/react-query";
import {
  useOrgConfigHistory,
  useActivateOrgConfig,
} from "../providers/hooks/useOrgConfig";
import { refreshGatewayConfig } from "../providers/hooks/useGatewayConfig";
import { reportConfigWarnings } from "../hooks/useConfigPatch";
import ConfigDiffView from "./ConfigDiffView";
import ConfirmDialog from "../components/ConfirmDialog";
import GatewayAdminOnly from "../components/GatewayAdminOnly";
import { coveredDrawerSx, useDrawerCovered } from "src/hooks/use-drawer-stack";
import { CopyButton, Glyph } from "./ui";
import { useGatewayAdmin } from "../hooks/useGatewayAdmin";
import { maskSecrets } from "./maskSecrets";
import { readPolicy } from "../budgets/admission/admissionPolicy";
import {
  RESTORE_CAPS_OFF_MESSAGE,
  RESTORE_STOPS_CAPS_MESSAGE,
} from "../budgets/admission/capsWording";
import { GATEWAY_MONO } from "../utils/fonts";

function VersionConfigContents({ config }) {
  const [section, setSection] = useState("");
  const [search, setSearch] = useState("");
  // Mask the entire response before searching, displaying or copying it.
  const masked = useMemo(() => maskSecrets(config), [config]);
  const fields = Object.keys(masked).sort();
  const needle = search.trim().toLowerCase();
  const shown = Object.fromEntries(
    Object.entries(masked).filter(
      ([key, value]) =>
        (!section || key === section) &&
        (!needle ||
          `${key} ${JSON.stringify(value)}`.toLowerCase().includes(needle)),
    ),
  );
  const json = JSON.stringify(shown, null, 2);
  return (
    <Stack spacing={2} sx={{ pt: 1 }}>
      <Typography variant="body2" color="text.secondary">
        Read-only returned configuration. Credential fields are masked in both
        this view and copied JSON.
      </Typography>
      <TextField
        select
        size="small"
        label="Section or field"
        value={section}
        onChange={(event) => setSection(event.target.value)}
      >
        <MenuItem value="">All returned fields</MenuItem>
        {fields.map((field) => (
          <MenuItem key={field} value={field}>
            {field}
          </MenuItem>
        ))}
      </TextField>
      <TextField
        size="small"
        label="Search returned configuration"
        value={search}
        onChange={(event) => setSearch(event.target.value)}
      />
      <Stack direction="row" alignItems="center" justifyContent="space-between">
        <Typography variant="body2">
          {Object.keys(shown).length} returned fields shown
        </Typography>
        <CopyButton text={json} label="Copy shown JSON" />
      </Stack>
      {Object.keys(shown).length === 0 ? (
        <Typography variant="body2">
          No matching fields in this view.
        </Typography>
      ) : (
        <Box
          component="pre"
          data-testid="history-config-json"
          sx={{
            m: 0,
            bgcolor: "action.hover",
            borderRadius: 1,
            p: 2,
            fontFamily: GATEWAY_MONO,
            fontSize: "0.75rem",
            whiteSpace: "pre-wrap",
            overflowWrap: "anywhere",
            maxHeight: 400,
            overflow: "auto",
          }}
        >
          {json}
        </Box>
      )}
    </Stack>
  );
}

const ConfigHistoryDrawer = ({ open, onClose, onActivated }) => {
  const queryClient = useQueryClient();
  const {
    data: history,
    isLoading,
    isPending,
    isFetching,
    isError,
    error,
    refetch,
  } = useOrgConfigHistory();
  const readFailed = isError || Boolean(error);
  const hasHistory = Array.isArray(history);
  const loading = isLoading || isPending || (!hasHistory && !readFailed);
  const activateMutation = useActivateOrgConfig();
  // Activating a version pushes it: Owner or Admin only (useGatewayAdmin).
  const { canEdit } = useGatewayAdmin();
  const [viewConfig, setViewConfig] = useState(null);
  // The version whose Activate was clicked: its diff is shown first.
  const [pendingVersion, setPendingVersion] = useState(null);

  const versions = useMemo(() => {
    if (!history) return [];
    const arr = Array.isArray(history) ? history : [];
    return [...arr].sort((a, b) => (b.version || 0) - (a.version || 0));
  }, [history]);

  const activeVersion = versions.find((cfg) => cfg.is_active) || null;
  // Daily caps can't be turned on in this release: over caps off, a version
  // with caps on is restored with caps off (the server saves it as a new
  // version), by the person's explicit choice here.
  const capsOn = (cfg) => Boolean(cfg) && readPolicy(cfg).enabled;
  const restoresCapsOff =
    Boolean(pendingVersion) && capsOn(pendingVersion) && !capsOn(activeVersion);
  const stopsCaps =
    Boolean(pendingVersion) && !capsOn(pendingVersion) && capsOn(activeVersion);

  // useActivateOrgConfig re-reads the org config and its history; the gateway
  // config it pushed is re-read here.
  const handleConfirmActivate = () => {
    activateMutation.mutate(pendingVersion.id, {
      onSuccess: (result) => {
        setPendingVersion(null);
        onActivated?.(result);
        reportConfigWarnings(result?.warnings);
        return refreshGatewayConfig(queryClient);
      },
    });
  };

  const covered = useDrawerCovered(open);
  const titleId = useId();

  return (
    <>
      <Drawer
        open={open}
        onClose={onClose}
        anchor="right"
        sx={coveredDrawerSx(covered)}
        PaperProps={{
          role: "dialog",
          "aria-labelledby": titleId,
          sx: {
            width: { xs: "100%", sm: 480 },
            maxWidth: "100%",
            display: "flex",
            flexDirection: "column",
            backgroundColor: "background.paper",
          },
        }}
      >
        <Stack
          direction="row"
          alignItems="center"
          justifyContent="space-between"
          sx={{ px: 2, py: 1.5, borderBottom: 1, borderColor: "divider" }}
        >
          <Typography
            id={titleId}
            component="h2"
            typography="m3"
            fontWeight="fontWeightMedium"
          >
            Config History
          </Typography>
          <IconButton aria-label="Close" size="small" onClick={onClose}>
            <Glyph name="close" />
          </IconButton>
        </Stack>
        <Box sx={{ flex: 1, minHeight: 0, overflow: "auto", p: 2 }}>
          {readFailed && (
            <Alert
              severity="warning"
              sx={{ mb: 2 }}
              action={
                <Button
                  size="small"
                  onClick={() => refetch()}
                  disabled={isFetching}
                >
                  Retry
                </Button>
              }
            >
              Could not load history.
              {hasHistory &&
                " Showing the last loaded versions; this history may be stale."}
            </Alert>
          )}
          {isFetching && hasHistory && (
            <Typography role="status" variant="body2" color="text.secondary">
              Refreshing history…
            </Typography>
          )}
          {loading && !hasHistory ? (
            <Stack spacing={1}>
              <Typography role="status" variant="body2" color="text.secondary">
                Loading history…
              </Typography>
              {[...Array(5)].map((_, i) => (
                <Skeleton key={i} variant="rounded" height={48} />
              ))}
            </Stack>
          ) : versions.length === 0 && hasHistory && !readFailed && !loading ? (
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
                      <GatewayAdminOnly allowed={canEdit}>
                        <Button
                          size="small"
                          variant="outlined"
                          onClick={() => setPendingVersion(cfg)}
                          disabled={activateMutation.isPending || !canEdit}
                        >
                          Activate
                        </Button>
                      </GatewayAdminOnly>
                    )}
                  </Stack>
                </ListItem>
              ))}
            </List>
          )}
        </Box>
      </Drawer>

      <ConfigDiffView
        open={Boolean(pendingVersion) && !restoresCapsOff}
        target={pendingVersion}
        active={activeVersion}
        notice={stopsCaps ? RESTORE_STOPS_CAPS_MESSAGE : null}
        onConfirm={handleConfirmActivate}
        onCancel={() => setPendingVersion(null)}
        isActivating={activateMutation.isPending}
      />

      <ConfirmDialog
        open={restoresCapsOff}
        onClose={() => setPendingVersion(null)}
        onConfirm={handleConfirmActivate}
        isLoading={activateMutation.isPending}
        title={`Restore version ${pendingVersion?.version ?? ""}`}
        message={RESTORE_CAPS_OFF_MESSAGE}
        confirmLabel="Restore with caps off"
        confirmColor="primary"
      />

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
          {viewConfig && (
            <VersionConfigContents key={viewConfig.id} config={viewConfig} />
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setViewConfig(null)}>Close</Button>
        </DialogActions>
      </Dialog>
    </>
  );
};

export default ConfigHistoryDrawer;
