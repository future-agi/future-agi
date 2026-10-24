import StatusBadge from "src/components/status-badge/StatusBadge";
import React, { useState, useMemo, useCallback } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  Box,
  Typography,
  Button,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TextField,
  Skeleton,
  InputAdornment,
  Tab,
  Tabs,
  IconButton,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Tooltip,
} from "@mui/material";
import Section from "src/components/section";
import { enqueueSnackbar } from "notistack";
import Iconify from "src/components/iconify";
import SectionHeader from "../components/SectionHeader";
import PageErrorState from "../components/PageErrorState";
import { GATEWAY_ICONS } from "../constants/gatewayIcons";
import { useGatewayContext } from "../context/useGatewayContext";
import { getErrorMessage } from "../../settings/integrations/utils";

import {
  useWebhooks,
  useCreateWebhook,
  useUpdateWebhook,
  useDeleteWebhook,
  useTestWebhook,
} from "./hooks/useWebhooks";
import CreateEditWebhookDialog from "./CreateEditWebhookDialog";
import WebhookEventLog from "./WebhookEventLog";
import { formatDate } from "../utils/formatters";

const CHIP_ROLE = {
  success: "success",
  warning: "warning",
  error: "danger",
  info: "info",
  primary: "discovery",
  secondary: "discovery",
  default: "neutral",
};

const TAB_SLUGS = ["endpoints", "delivery"];

function tabSlugToIndex(slug) {
  const idx = TAB_SLUGS.indexOf(slug);
  return idx >= 0 ? idx : 0;
}

const WebhookManagementSection = () => {
  const { tab: tabSlug } = useParams();
  const navigate = useNavigate();
  const tab = tabSlugToIndex(tabSlug);

  const handleTabChange = useCallback(
    (_, newIndex) => {
      if (newIndex === 0) {
        navigate("/dashboard/gateway/webhooks", { replace: true });
      } else {
        navigate(`/dashboard/gateway/webhooks/${TAB_SLUGS[newIndex]}`, {
          replace: true,
        });
      }
    },
    [navigate],
  );

  const [searchQuery, setSearchQuery] = useState("");
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editWebhook, setEditWebhook] = useState(null);
  const [deleteTarget, setDeleteTarget] = useState(null);
  const [selectedWebhookId, setSelectedWebhookId] = useState(null);

  const { gatewayId: _gatewayId } = useGatewayContext();
  const { data: webhooks, isLoading, error, refetch } = useWebhooks();
  const createMutation = useCreateWebhook();
  const updateMutation = useUpdateWebhook();
  const deleteMutation = useDeleteWebhook();
  const testMutation = useTestWebhook();

  const filteredWebhooks = useMemo(() => {
    if (!webhooks) return [];
    if (!searchQuery.trim()) return webhooks;
    const q = searchQuery.toLowerCase();
    return webhooks.filter(
      (w) =>
        w.name?.toLowerCase().includes(q) || w.url?.toLowerCase().includes(q),
    );
  }, [webhooks, searchQuery]);

  const handleCreate = (payload) => {
    createMutation.mutate(payload, {
      onSuccess: () => {
        enqueueSnackbar("Webhook created", { variant: "success" });
        setDialogOpen(false);
      },
      onError: (err) => {
        enqueueSnackbar(getErrorMessage(err, "Failed to create webhook"), {
          variant: "error",
        });
      },
    });
  };

  const handleUpdate = (payload) => {
    updateMutation.mutate(payload, {
      onSuccess: () => {
        enqueueSnackbar("Webhook updated", { variant: "success" });
        setEditWebhook(null);
      },
      onError: (err) => {
        enqueueSnackbar(getErrorMessage(err, "Failed to update webhook"), {
          variant: "error",
        });
      },
    });
  };

  const handleDelete = () => {
    if (!deleteTarget) return;
    deleteMutation.mutate(deleteTarget.id, {
      onSuccess: () => {
        enqueueSnackbar("Webhook deleted", { variant: "success" });
        setDeleteTarget(null);
      },
      onError: () =>
        enqueueSnackbar("Failed to delete webhook", { variant: "error" }),
    });
  };

  const handleTest = (webhookId) => {
    testMutation.mutate(webhookId, {
      onSuccess: (result) => {
        const code = result?.status_code;
        if (code && code >= 200 && code < 300) {
          enqueueSnackbar(`Test successful (${code})`, { variant: "success" });
        } else {
          enqueueSnackbar(`Test returned status ${code || "unknown"}`, {
            variant: "warning",
          });
        }
      },
      onError: () =>
        enqueueSnackbar("Test webhook failed", { variant: "error" }),
    });
  };

  if (isLoading) {
    return (
      <Box p={3}>
        <Stack direction="row" justifyContent="space-between" mb={3}>
          <Skeleton width={200} height={40} />
          <Skeleton width={120} height={36} variant="rounded" />
        </Stack>
        <Section variant="panel">
          {[...Array(4)].map((_, i) => (
            <Stack
              key={i}
              direction="row"
              spacing={2}
              sx={{
                px: 2,
                py: 1.5,
                borderBottom: "1px solid",
                borderColor: "divider",
              }}
            >
              <Skeleton width="25%" height={20} />
              <Skeleton width="35%" height={20} />
              <Skeleton width="15%" height={20} />
              <Skeleton width="15%" height={20} />
            </Stack>
          ))}
        </Section>
      </Box>
    );
  }

  if (error) {
    return (
      <Box p={3}>
        <PageErrorState
          message={`Failed to load webhooks: ${error.message}`}
          onRetry={refetch}
        />
      </Box>
    );
  }

  return (
    <Box p={3}>
      <SectionHeader
        sx={{ mb: 1.5 }}
        icon={GATEWAY_ICONS.webhooks}
        title="Webhooks"
        subtitle="Configure webhook endpoints for event notifications"
        actions={[
          {
            label: "Create Webhook",
            variant: "contained",
            size: "small",
            icon: "solar:add-linear",
            onClick: () => setDialogOpen(true),
          },
        ]}
      />

      <Tabs
        value={tab}
        onChange={handleTabChange}
        sx={{ mb: 1.5, borderBottom: 1, borderColor: "divider" }}
      >
        <Tab label={`Endpoints (${webhooks?.length || 0})`} />
        <Tab label="Delivery Log" />
      </Tabs>

      {tab === 0 && (
        <>
          <TextField
            placeholder="Search by name or URL..."
            size="small"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            sx={{ width: 300, mb: 2 }}
            InputProps={{
              startAdornment: (
                <InputAdornment position="start">
                  <Iconify icon="solar:magnifier-linear" width={16} />
                </InputAdornment>
              ),
            }}
          />

          {filteredWebhooks.length === 0 ? (
            <Box
              display="flex"
              flexDirection="column"
              alignItems="center"
              py={8}
            >
              <Iconify
                icon="solar:link-circle-linear"
                width={48}
                sx={{ color: "text.disabled", mb: 2 }}
              />
              <Typography variant="h6" color="text.secondary" mb={1}>
                {webhooks?.length === 0
                  ? "No webhooks yet"
                  : "No webhooks match your search"}
              </Typography>
              {webhooks?.length === 0 && (
                <Button
                  variant="outlined"
                  startIcon={<Iconify icon="solar:add-linear" width={20} />}
                  onClick={() => setDialogOpen(true)}
                >
                  Create Your First Webhook
                </Button>
              )}
            </Box>
          ) : (
            <>
              <TableContainer>
                <Table size="small">
                  <TableHead>
                    <TableRow>
                      <TableCell>Name</TableCell>
                      <TableCell>URL</TableCell>
                      <TableCell>Events</TableCell>
                      <TableCell>Status</TableCell>
                      <TableCell>Created</TableCell>
                      <TableCell
                        sx={{ fontVariantNumeric: "tabular-nums" }}
                        align="right"
                      >
                        Actions
                      </TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {filteredWebhooks.map((wh) => (
                      <TableRow key={wh.id} hover>
                        <TableCell>
                          <Typography variant="body2" fontWeight={500}>
                            {wh.name}
                          </Typography>
                        </TableCell>
                        <TableCell>
                          <Typography
                            variant="body2"
                            sx={{ fontFamily: "monospace", maxWidth: 300 }}
                            noWrap
                            title={wh.url}
                          >
                            {wh.url}
                          </Typography>
                        </TableCell>
                        <TableCell>
                          <Typography variant="body2" color="text.secondary">
                            {(wh.events || []).join(" \u00B7 ") || "\u2014"}
                          </Typography>
                        </TableCell>
                        <TableCell>
                          <StatusBadge
                            status={wh.is_active ? "Active" : "Inactive"}
                            label={String(wh.is_active ? "Active" : "Inactive")}
                            role={
                              CHIP_ROLE[wh.is_active ? "success" : "default"]
                            }
                          />
                        </TableCell>
                        <TableCell>
                          <Typography variant="body2" color="text.secondary">
                            {formatDate(wh.created_at)}
                          </Typography>
                        </TableCell>
                        <TableCell
                          sx={{ fontVariantNumeric: "tabular-nums" }}
                          align="right"
                        >
                          <Stack
                            direction="row"
                            spacing={0.5}
                            justifyContent="flex-end"
                          >
                            <Tooltip title="Test webhook">
                              <IconButton
                                className="row-hover-action"
                                size="small"
                                onClick={() => handleTest(wh.id)}
                                disabled={testMutation.isPending}
                              >
                                <Iconify
                                  icon="solar:play-circle-linear"
                                  width={20}
                                />
                              </IconButton>
                            </Tooltip>
                            <Tooltip title="View events">
                              <IconButton
                                className="row-hover-action"
                                size="small"
                                onClick={() => {
                                  setSelectedWebhookId(wh.id);
                                  navigate(
                                    "/dashboard/gateway/webhooks/delivery",
                                    { replace: true },
                                  );
                                }}
                              >
                                <Iconify icon="solar:list-linear" width={20} />
                              </IconButton>
                            </Tooltip>
                            <Tooltip title="Edit">
                              <IconButton
                                className="row-hover-action"
                                size="small"
                                onClick={() => setEditWebhook(wh)}
                              >
                                <Iconify icon="solar:pen-linear" width={20} />
                              </IconButton>
                            </Tooltip>
                            <Tooltip title="Delete">
                              <IconButton
                                className="row-hover-action"
                                size="small"
                                color="error"
                                onClick={() => setDeleteTarget(wh)}
                              >
                                <Iconify
                                  icon="solar:trash-bin-trash-linear"
                                  width={20}
                                />
                              </IconButton>
                            </Tooltip>
                          </Stack>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </TableContainer>
              <Typography variant="body2" color="text.secondary" mt={2}>
                Showing {filteredWebhooks.length} webhook
                {filteredWebhooks.length !== 1 ? "s" : ""}
              </Typography>
            </>
          )}
        </>
      )}

      {tab === 1 && <WebhookEventLog webhookId={selectedWebhookId} />}

      {/* Create dialog */}
      <CreateEditWebhookDialog
        open={dialogOpen}
        onClose={() => setDialogOpen(false)}
        onSubmit={handleCreate}
        isPending={createMutation.isPending}
      />

      {/* Edit dialog */}
      <CreateEditWebhookDialog
        open={Boolean(editWebhook)}
        onClose={() => setEditWebhook(null)}
        onSubmit={handleUpdate}
        webhook={editWebhook}
        isPending={updateMutation.isPending}
      />

      {/* Delete confirmation */}
      <Dialog
        open={Boolean(deleteTarget)}
        onClose={() => setDeleteTarget(null)}
        maxWidth={false}
        PaperProps={{ sx: { maxWidth: 560 } }}
      >
        <DialogTitle>Delete Webhook</DialogTitle>
        <DialogContent>
          <Typography>
            Are you sure you want to delete{" "}
            <strong>{deleteTarget?.name}</strong>? This action cannot be undone.
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDeleteTarget(null)}>Cancel</Button>
          <Button
            variant="contained"
            color="error"
            onClick={handleDelete}
            disabled={deleteMutation.isPending}
          >
            {deleteMutation.isPending ? "Deleting..." : "Delete"}
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
};

export default WebhookManagementSection;
