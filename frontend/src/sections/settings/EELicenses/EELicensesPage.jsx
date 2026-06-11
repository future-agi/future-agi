/**
 * EE License Management — purchase, view, download, revoke license keys.
 *
 * For self-hosted EE customers to manage their license keys.
 * Cloud users won't see this page (hidden by route guard).
 */

import { useState } from "react";
import PropTypes from "prop-types";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Box,
  Typography,
  Stack,
  Paper,
  Button,
  Chip,
  Divider,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  TextField,
  Select,
  MenuItem,
  FormControl,
  InputLabel,
  Skeleton,
  Alert,
  Tooltip,
} from "@mui/material";
import Iconify from "src/components/iconify";
import axios from "src/utils/axios";
import { enqueueSnackbar } from "notistack";
import { apiPath } from "src/api/contracts/api-surface";

const BAND_CONFIG = {
  team: {
    label: "Team",
    color: "#3b82f6",
    price: "$499/mo",
    icon: "mdi:account-group",
  },
  business: {
    label: "Business",
    color: "#8b5cf6",
    price: "$999/mo",
    icon: "mdi:office-building",
  },
  enterprise: {
    label: "Enterprise",
    color: "#f59e0b",
    price: "$2,499/mo",
    icon: "mdi:shield-star",
  },
  enterprise_plus: {
    label: "Enterprise+",
    color: "#ef4444",
    price: "$4,999/mo",
    icon: "mdi:rocket-launch",
  },
};

const STATUS_COLORS = {
  active: "success",
  expired: "error",
  revoked: "default",
  suspended: "warning",
};

// ── License Card ───────────────────────────────────────────────────────────

function LicenseCard({ license, onRevoke, onCopyKey }) {
  const band = BAND_CONFIG[license.band] || BAND_CONFIG.team;
  const isActive = license.status === "active";
  const expiresAt = license.expires_at ? new Date(license.expires_at) : null;
  const daysUntilExpiry = expiresAt
    ? Math.ceil((expiresAt - new Date()) / (1000 * 60 * 60 * 24))
    : null;

  return (
    <Paper variant="outlined" sx={{ p: 2.5, borderRadius: 2 }}>
      <Stack
        direction="row"
        justifyContent="space-between"
        alignItems="flex-start"
      >
        <Stack direction="row" spacing={2} alignItems="center">
          <Box
            sx={{
              width: 44,
              height: 44,
              borderRadius: 1.5,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              bgcolor: `${band.color}15`,
            }}
          >
            <Iconify icon={band.icon} width={24} sx={{ color: band.color }} />
          </Box>
          <Box>
            <Stack direction="row" spacing={1} alignItems="center">
              <Typography variant="subtitle1" fontWeight={700}>
                {band.label}
              </Typography>
              <Chip
                label={license.status}
                size="small"
                color={STATUS_COLORS[license.status] || "default"}
                variant="outlined"
              />
            </Stack>
            <Typography variant="caption" color="text.secondary">
              {license.customer_name || "—"}
            </Typography>
          </Box>
        </Stack>

        <Stack alignItems="flex-end" spacing={0.5}>
          <Typography variant="caption" color="text.secondary">
            Issued: {new Date(license.issued_at).toLocaleDateString()}
          </Typography>
          {expiresAt && (
            <Typography
              variant="caption"
              color={daysUntilExpiry <= 30 ? "warning.main" : "text.secondary"}
            >
              Expires: {expiresAt.toLocaleDateString()}
              {daysUntilExpiry <= 30 &&
                daysUntilExpiry > 0 &&
                ` (${daysUntilExpiry} days)`}
            </Typography>
          )}
        </Stack>
      </Stack>

      {/* Features */}
      <Box mt={2}>
        <Typography variant="caption" color="text.secondary" fontWeight={600}>
          Features included:
        </Typography>
        <Stack
          direction="row"
          spacing={0.5}
          mt={0.5}
          flexWrap="wrap"
          useFlexGap
        >
          {(license.features || []).map((f) => (
            <Chip
              key={f}
              label={f.replace(/_/g, " ")}
              size="small"
              variant="outlined"
            />
          ))}
        </Stack>
      </Box>

      {/* Limits */}
      <Stack direction="row" spacing={3} mt={2}>
        <Box>
          <Typography variant="caption" color="text.secondary">
            Traces/mo
          </Typography>
          <Typography variant="body2" fontWeight={600}>
            {license.max_traces_monthly === -1
              ? "Unlimited"
              : (license.max_traces_monthly || 0).toLocaleString()}
          </Typography>
        </Box>
        <Box>
          <Typography variant="caption" color="text.secondary">
            Gateway/mo
          </Typography>
          <Typography variant="body2" fontWeight={600}>
            {license.max_gateway_monthly === -1
              ? "Unlimited"
              : (license.max_gateway_monthly || 0).toLocaleString()}
          </Typography>
        </Box>
      </Stack>

      {/* Actions */}
      <Stack direction="row" spacing={1} mt={2}>
        {license.jwt_key && (
          <Tooltip title="Copy license key to clipboard">
            <Button
              size="small"
              variant="outlined"
              startIcon={<Iconify icon="mdi:content-copy" />}
              onClick={() => onCopyKey(license.jwt_key)}
            >
              Copy Key
            </Button>
          </Tooltip>
        )}
        {isActive && (
          <Button
            size="small"
            variant="outlined"
            color="error"
            startIcon={<Iconify icon="mdi:cancel" />}
            onClick={() => onRevoke(license.id)}
          >
            Revoke
          </Button>
        )}
      </Stack>
    </Paper>
  );
}

LicenseCard.propTypes = {
  license: PropTypes.shape({
    id: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
    band: PropTypes.string,
    status: PropTypes.string,
    expires_at: PropTypes.string,
    customer_name: PropTypes.string,
    issued_at: PropTypes.string,
    features: PropTypes.arrayOf(PropTypes.string),
    max_traces_monthly: PropTypes.number,
    max_gateway_monthly: PropTypes.number,
    jwt_key: PropTypes.string,
  }).isRequired,
  onRevoke: PropTypes.func.isRequired,
  onCopyKey: PropTypes.func.isRequired,
};

// ── Main Page ──────────────────────────────────────────────────────────────

export default function EELicensesPage() {
  const [createOpen, setCreateOpen] = useState(false);
  const [newLicense, setNewLicense] = useState({
    band: "team",
    customer_name: "",
    billing_interval: "monthly",
  });
  const [generatedKey, setGeneratedKey] = useState(null);

  const queryClient = useQueryClient();

  const { data: licenses, isLoading } = useQuery({
    queryKey: ["ee-licenses"],
    queryFn: () => axios.get(apiPath("/usage/ee/licenses/")),
    select: (res) => res.data?.result?.licenses || [],
  });

  const createMutation = useMutation({
    mutationFn: (data) => axios.post(apiPath("/usage/ee/licenses/"), data),
    onSuccess: (res) => {
      const result = res.data?.result;
      if (result?.jwt_key) {
        setGeneratedKey(result.jwt_key);
      }
      queryClient.invalidateQueries({ queryKey: ["ee-licenses"] });
      setCreateOpen(false);
      enqueueSnackbar("License created!", { variant: "success" });
    },
    onError: () =>
      enqueueSnackbar("Failed to create license", { variant: "error" }),
  });

  const revokeMutation = useMutation({
    mutationFn: (id) =>
      axios.post(
        apiPath("/usage/ee/licenses/{grant_id}/revoke/", { grant_id: id }),
        {},
      ),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["ee-licenses"] });
      enqueueSnackbar("License revoked", { variant: "info" });
    },
  });

  const handleCopyKey = (key) => {
    navigator.clipboard.writeText(key);
    enqueueSnackbar("License key copied to clipboard", { variant: "success" });
  };

  if (isLoading) {
    return (
      <Box>
        <Skeleton variant="text" width={250} height={36} />
        <Skeleton variant="rounded" height={180} sx={{ mt: 2 }} />
      </Box>
    );
  }

  return (
    <Box>
      <Stack
        direction="row"
        justifyContent="space-between"
        alignItems="center"
        mb={1}
      >
        <Box>
          <Typography variant="h5" fontWeight={700}>
            EE Licenses
          </Typography>
          <Typography variant="body2" color="text.secondary">
            Manage enterprise license keys for self-hosted deployments.
          </Typography>
        </Box>
        <Button
          variant="contained"
          startIcon={<Iconify icon="mdi:key-plus" />}
          onClick={() => setCreateOpen(true)}
        >
          Generate License
        </Button>
      </Stack>

      <Divider sx={{ my: 2 }} />

      {/* Generated key alert */}
      {generatedKey && (
        <Alert
          severity="success"
          sx={{ mb: 2 }}
          action={
            <Button size="small" onClick={() => handleCopyKey(generatedKey)}>
              Copy
            </Button>
          }
          onClose={() => setGeneratedKey(null)}
        >
          <Typography variant="subtitle2" mb={0.5}>
            License key generated!
          </Typography>
          <Typography
            variant="caption"
            sx={{ wordBreak: "break-all", fontFamily: "monospace" }}
          >
            {generatedKey.substring(0, 60)}...
          </Typography>
          <Typography
            variant="caption"
            display="block"
            color="warning.main"
            mt={0.5}
          >
            Copy this key now — it won&apos;t be shown again. Set it as
            EE_LICENSE_KEY in your .env file.
          </Typography>
        </Alert>
      )}

      {/* License list */}
      {!licenses || licenses.length === 0 ? (
        <Paper
          variant="outlined"
          sx={{
            p: 4,
            textAlign: "center",
            borderStyle: "dashed",
            borderRadius: 2,
          }}
        >
          <Iconify
            icon="mdi:key-outline"
            width={48}
            sx={{ color: "text.disabled", mb: 1 }}
          />
          <Typography variant="subtitle1" color="text.secondary">
            No licenses yet
          </Typography>
          <Typography variant="body2" color="text.secondary" mb={2}>
            Generate a license key to unlock EE features on your self-hosted
            instance.
          </Typography>
          <Button
            variant="outlined"
            startIcon={<Iconify icon="mdi:key-plus" />}
            onClick={() => setCreateOpen(true)}
          >
            Generate Your First License
          </Button>
        </Paper>
      ) : (
        <Stack spacing={2}>
          {licenses.map((lic) => (
            <LicenseCard
              key={lic.id}
              license={lic}
              onRevoke={(id) => revokeMutation.mutate(id)}
              onCopyKey={handleCopyKey}
            />
          ))}
        </Stack>
      )}

      {/* Create License Dialog */}
      <Dialog
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        maxWidth="sm"
        fullWidth
      >
        <DialogTitle>Generate EE License</DialogTitle>
        <DialogContent>
          <Stack spacing={2.5} mt={1}>
            <TextField
              label="Customer / Company Name"
              fullWidth
              size="small"
              value={newLicense.customer_name}
              onChange={(e) =>
                setNewLicense({ ...newLicense, customer_name: e.target.value })
              }
              placeholder="e.g., Acme Corp"
            />
            <FormControl fullWidth size="small">
              <InputLabel>License Band</InputLabel>
              <Select
                value={newLicense.band}
                label="License Band"
                onChange={(e) =>
                  setNewLicense({ ...newLicense, band: e.target.value })
                }
              >
                {Object.entries(BAND_CONFIG).map(([key, cfg]) => (
                  <MenuItem key={key} value={key}>
                    {cfg.label} — {cfg.price}
                  </MenuItem>
                ))}
              </Select>
            </FormControl>
            <FormControl fullWidth size="small">
              <InputLabel>Billing Interval</InputLabel>
              <Select
                value={newLicense.billing_interval}
                label="Billing Interval"
                onChange={(e) =>
                  setNewLicense({
                    ...newLicense,
                    billing_interval: e.target.value,
                  })
                }
              >
                <MenuItem value="monthly">Monthly</MenuItem>
                <MenuItem value="yearly">Annual (save ~17%)</MenuItem>
              </Select>
            </FormControl>

            {/* Band details */}
            {newLicense.band && (
              <Paper
                variant="outlined"
                sx={{ p: 2, borderRadius: 1.5, bgcolor: "action.hover" }}
              >
                <Typography
                  variant="caption"
                  color="text.secondary"
                  fontWeight={600}
                >
                  {BAND_CONFIG[newLicense.band]?.label} includes:
                </Typography>
                <Typography variant="body2" mt={0.5}>
                  {newLicense.band === "team" &&
                    "KB, review workflow, extended retention, 5M traces/mo"}
                  {newLicense.band === "business" &&
                    "Everything in Team + audit logs, SCIM, voice sim, 25M traces/mo"}
                  {newLicense.band === "enterprise" &&
                    "Everything in Business + optimization, custom brand, 100M traces/mo"}
                  {newLicense.band === "enterprise_plus" &&
                    "Everything unlimited + dedicated support"}
                </Typography>
              </Paper>
            )}
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setCreateOpen(false)}>Cancel</Button>
          <Button
            variant="contained"
            onClick={() => createMutation.mutate(newLicense)}
            disabled={!newLicense.customer_name || createMutation.isPending}
          >
            {createMutation.isPending
              ? "Generating..."
              : "Generate License Key"}
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}
