import React, { useState, useMemo } from "react";
import {
  Box,
  Typography,
  Button,
  Stack,
  Chip,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Skeleton,
  Card,
  InputAdornment,
} from "@mui/material";
import { enqueueSnackbar } from "notistack";
import { useNavigate, useSearchParams } from "react-router-dom";
import Iconify from "src/components/iconify";
import SectionHeader from "../components/SectionHeader";
import PageErrorState from "../components/PageErrorState";
import GatewayOnboardingFocusPanel from "../components/GatewayOnboardingFocusPanel";
import { GATEWAY_ICONS } from "../constants/gatewayIcons";
import { useApiKeys, useSyncApiKeys } from "./hooks/useApiKeys";
import { useGatewayContext } from "../context/useGatewayContext";
import CreateKeyDialog from "./CreateKeyDialog";
import KeyDetailDrawer from "./KeyDetailDrawer";
import { formatDate } from "../utils/formatters";
import {
  appendGatewayOnboardingAttributionToHref,
  buildGatewayKeyCreatedPayload,
  GATEWAY_ONBOARDING_MODES,
  gatewaySetupQuickStartAttributionFromSearch,
  getGatewayOnboardingRouteParams,
} from "../gatewayOnboardingEvents";
import { useRecordActivationEvent } from "src/sections/onboarding-home/hooks/useRecordActivationEvent";

const STATUS_COLORS = {
  active: "success",
  revoked: "error",
  expired: "warning",
};

const STATUS_FILTERS = [
  { value: "", label: "All" },
  { value: "active", label: "Active" },
  { value: "revoked", label: "Revoked" },
  { value: "expired", label: "Expired" },
];

const KeyManagementSection = () => {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [statusFilter, setStatusFilter] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedKeyId, setSelectedKeyId] = useState(null);
  const [createDialogOpen, setCreateDialogOpen] = useState(false);
  const { gatewayId } = useGatewayContext();
  const { mutate: recordActivationEvent } = useRecordActivationEvent();

  const { data: keys, isLoading, error, refetch } = useApiKeys(gatewayId);
  const syncMutation = useSyncApiKeys();
  const onboardingHref = useMemo(
    () => (href) =>
      appendGatewayOnboardingAttributionToHref(href, searchParams),
    [searchParams],
  );
  const gatewayQuickStartAttribution = useMemo(
    () => gatewaySetupQuickStartAttributionFromSearch(searchParams),
    [searchParams],
  );
  const onboardingParams = getGatewayOnboardingRouteParams(searchParams);
  const showOnboardingFocus =
    onboardingParams.isOnboarding &&
    (!onboardingParams.mode ||
      onboardingParams.mode === GATEWAY_ONBOARDING_MODES.CREATE_KEY);

  // Client-side filters
  const filteredKeys = useMemo(() => {
    if (!keys) return [];
    let result = keys;
    if (statusFilter) {
      result = result.filter((k) => k.status === statusFilter);
    }
    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase();
      result = result.filter(
        (k) =>
          k.name?.toLowerCase().includes(q) ||
          k.owner?.toLowerCase().includes(q) ||
          k.keyPrefix?.toLowerCase().includes(q),
      );
    }
    return result;
  }, [keys, statusFilter, searchQuery]);

  const handleSync = () => {
    if (!gatewayId) return;
    syncMutation.mutate(gatewayId, {
      onSuccess: (result) => {
        enqueueSnackbar(`Synced ${result?.synced ?? 0} keys from gateway`, {
          variant: "success",
        });
      },
      onError: () => {
        enqueueSnackbar("Failed to sync keys from gateway", {
          variant: "error",
        });
      },
    });
  };

  const handleKeyCreated = (key, context) => {
    if (!showOnboardingFocus) return;

    recordActivationEvent(
      buildGatewayKeyCreatedPayload({
        ...context,
        key,
        quickStartAttribution: gatewayQuickStartAttribution,
      }),
    );
  };

  const handleKeyDone = (key) => {
    if (!showOnboardingFocus || !key) return;

    navigate(
      onboardingHref(
        "/dashboard/gateway?source=onboarding&onboarding=test-request&journey_step=run_gateway_request&tour_anchor=gateway_request_button",
      ),
    );
  };

  if (isLoading) {
    return (
      <Box p={3}>
        <Stack direction="row" justifyContent="space-between" mb={3}>
          <Skeleton width={200} height={40} />
          <Skeleton width={120} height={36} variant="rounded" />
        </Stack>
        <Skeleton width="100%" height={48} variant="rounded" sx={{ mb: 1 }} />
        <Card>
          {[...Array(5)].map((_, i) => (
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
              <Skeleton width="15%" height={20} />
              <Skeleton width="15%" height={20} />
              <Skeleton width="20%" height={20} />
              <Skeleton width="15%" height={20} />
            </Stack>
          ))}
        </Card>
      </Box>
    );
  }

  if (error) {
    return (
      <Box p={3}>
        <PageErrorState
          message={`Failed to load API keys: ${error.message}`}
          onRetry={refetch}
        />
      </Box>
    );
  }

  return (
    <Box p={3}>
      {/* Header */}
      <SectionHeader
        icon={GATEWAY_ICONS.keys}
        title="API Keys"
        subtitle="Create and manage API keys for gateway access"
        actions={[
          {
            label: syncMutation.isPending ? "Syncing..." : "Sync",
            variant: "outlined",
            size: "small",
            icon: "mdi:sync",
            onClick: handleSync,
            disabled: syncMutation.isPending,
          },
          {
            label: "Create Key",
            variant: "contained",
            size: "small",
            icon: "mdi:plus",
            onClick: () => setCreateDialogOpen(true),
          },
        ]}
      />

      <GatewayOnboardingFocusPanel
        currentStep="API key"
        description="Create one key for the first routed request, then return to the gateway overview to send traffic."
        hidden={!showOnboardingFocus}
        primaryAction={{
          label: "Create Key",
          onClick: () => setCreateDialogOpen(true),
        }}
        secondaryAction={{
          label: "Open request step",
          onClick: () =>
            navigate(
              onboardingHref("/dashboard/gateway?onboarding=test-request"),
            ),
        }}
        singleActionFocus={showOnboardingFocus}
        steps={[
          { label: "Provider", complete: true },
          {
            label: "API key",
            complete: Array.isArray(keys) && keys.length > 0,
          },
          { label: "Request", complete: false },
        ]}
        title="Create the gateway API key"
        tourAnchor={onboardingParams.tourAnchor}
      />

      {/* Filters */}
      <Stack direction="row" spacing={2} alignItems="center" mb={3}>
        <ToggleButtonGroup
          value={statusFilter}
          exclusive
          onChange={(_, val) => val !== null && setStatusFilter(val)}
          size="small"
        >
          {STATUS_FILTERS.map((f) => (
            <ToggleButton
              key={f.value}
              value={f.value}
              sx={{
                px: 1.5,
                py: 0.25,
                textTransform: "none",
                fontSize: "0.8125rem",
              }}
            >
              {f.label}
            </ToggleButton>
          ))}
        </ToggleButtonGroup>

        <TextField
          placeholder="Search by name or owner..."
          size="small"
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          sx={{ width: 280 }}
          InputProps={{
            startAdornment: (
              <InputAdornment position="start">
                <Iconify icon="eva:search-outline" width={18} />
              </InputAdornment>
            ),
          }}
        />
      </Stack>

      {/* Table */}
      {filteredKeys.length === 0 ? (
        <Box
          display="flex"
          flexDirection="column"
          alignItems="center"
          justifyContent="center"
          py={8}
        >
          <Iconify
            icon="mdi:key-outline"
            width={48}
            sx={{ color: "text.disabled", mb: 2 }}
          />
          <Typography variant="h6" color="text.secondary" mb={1}>
            {keys?.length === 0
              ? "No API keys yet"
              : "No keys match your filters"}
          </Typography>
          {keys?.length === 0 && (
            <Button
              variant="outlined"
              startIcon={<Iconify icon="mdi:plus" width={20} />}
              onClick={() => setCreateDialogOpen(true)}
            >
              Create Your First Key
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
                  <TableCell>Key</TableCell>
                  <TableCell>Status</TableCell>
                  <TableCell>Owner</TableCell>
                  <TableCell>Models</TableCell>
                  <TableCell>Created</TableCell>
                  <TableCell>Last Used</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {filteredKeys.map((key) => (
                  <TableRow
                    key={key.id}
                    hover
                    sx={{ cursor: "pointer" }}
                    onClick={() => setSelectedKeyId(key.id)}
                  >
                    <TableCell>
                      <Typography variant="body2" fontWeight={500}>
                        {key.name}
                      </Typography>
                    </TableCell>
                    <TableCell>
                      <Typography
                        variant="body2"
                        sx={{
                          fontFamily: "monospace",
                          color: "text.secondary",
                        }}
                      >
                        {key.keyPrefix ? `${key.keyPrefix}****` : "\u2014"}
                      </Typography>
                    </TableCell>
                    <TableCell>
                      <Chip
                        label={key.status}
                        color={STATUS_COLORS[key.status] || "default"}
                        size="small"
                        variant="outlined"
                      />
                    </TableCell>
                    <TableCell>
                      <Typography variant="body2" color="text.secondary">
                        {key.owner || "\u2014"}
                      </Typography>
                    </TableCell>
                    <TableCell>
                      <Typography
                        variant="body2"
                        color="text.secondary"
                        noWrap
                        sx={{ maxWidth: 200 }}
                      >
                        {key.allowedModels?.length > 0
                          ? key.allowedModels.join(", ")
                          : "All"}
                      </Typography>
                    </TableCell>
                    <TableCell>
                      <Typography variant="body2" color="text.secondary">
                        {formatDate(key.created_at)}
                      </Typography>
                    </TableCell>
                    <TableCell>
                      <Typography variant="body2" color="text.secondary">
                        {formatDate(key.last_used_at)}
                      </Typography>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>

          <Typography variant="body2" color="text.secondary" mt={2}>
            Showing {filteredKeys.length} key
            {filteredKeys.length !== 1 ? "s" : ""}
          </Typography>
        </>
      )}

      {/* Create dialog */}
      <CreateKeyDialog
        open={createDialogOpen}
        onClose={() => setCreateDialogOpen(false)}
        gatewayId={gatewayId}
        onDone={handleKeyDone}
        onKeyCreated={handleKeyCreated}
      />

      {/* Detail drawer */}
      <KeyDetailDrawer
        keyId={selectedKeyId}
        open={Boolean(selectedKeyId)}
        onClose={() => setSelectedKeyId(null)}
        gatewayId={gatewayId}
      />
    </Box>
  );
};

export default KeyManagementSection;
