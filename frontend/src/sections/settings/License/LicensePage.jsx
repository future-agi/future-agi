import PropTypes from "prop-types";
import {
  Box,
  Card,
  Typography,
  Chip,
  Stack,
  CircularProgress,
  Divider,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { useCapabilities } from "src/hooks/useCapabilities";

const LICENSE_STATE_COLOR = {
  active: "success",
  expired: "error",
  grace: "warning",
  suspended: "warning",
  revoked: "default",
};

const BAND_LABELS = {
  starter: "Starter",
  team: "Team",
  business: "Business",
  enterprise: "Enterprise",
  enterprise_plus: "Enterprise+",
};

function DetailRow({ label, value }) {
  return (
    <Stack direction="row" spacing={1} alignItems="baseline">
      <Typography
        variant="body2"
        color="text.secondary"
        sx={{ minWidth: 148, flexShrink: 0 }}
      >
        {label}
      </Typography>
      <Typography variant="body2">{value || "—"}</Typography>
    </Stack>
  );
}

DetailRow.propTypes = {
  label: PropTypes.string.isRequired,
  value: PropTypes.node,
};

export default function LicensePage() {
  const { data, isLoading, isError } = useCapabilities();

  if (isLoading) {
    return (
      <Box
        display="flex"
        justifyContent="center"
        alignItems="center"
        minHeight={360}
      >
        <CircularProgress />
      </Box>
    );
  }

  if (isError) {
    return (
      <Box
        display="flex"
        justifyContent="center"
        alignItems="center"
        minHeight={360}
      >
        <Typography color="text.secondary">
          Failed to load license information.
        </Typography>
      </Box>
    );
  }

  const { license, instance_id, license_state, features } = data || {};

  if (!license) {
    return (
      <Stack
        alignItems="center"
        justifyContent="center"
        spacing={1}
        minHeight={360}
      >
        <Iconify
          icon="mdi:license-outline"
          width={48}
          sx={{ color: "text.disabled" }}
        />
        <Typography variant="subtitle1" color="text.secondary">
          No license found
        </Typography>
        <Typography variant="body2" color="text.secondary">
          No active license is configured on this instance.
        </Typography>
      </Stack>
    );
  }

  const stateColor = LICENSE_STATE_COLOR[license_state] || "default";
  const expiresAt = license.expires_at ? new Date(license.expires_at) : null;
  const featureEntries = Object.entries(features || {});

  return (
    <Box>
      <Box mb={1}>
        <Typography variant="h5" fontWeight={700}>
          License
        </Typography>
        <Typography variant="body2" color="text.secondary">
          Enterprise license details for this deployment.
        </Typography>
      </Box>

      <Divider sx={{ my: 2 }} />

      <Card variant="outlined" sx={{ p: 3, borderRadius: 2, maxWidth: 720 }}>
        <Stack
          direction="row"
          justifyContent="space-between"
          alignItems="flex-start"
          mb={3}
        >
          <Box>
            <Typography variant="h6" fontWeight={700}>
              {BAND_LABELS[license.band] || license.band}
            </Typography>
            <Typography variant="body2" color="text.secondary">
              {license.issued_to}
            </Typography>
          </Box>
          <Chip
            label={license_state}
            color={stateColor}
            size="small"
            variant="outlined"
          />
        </Stack>

        <Stack spacing={1.5}>
          <DetailRow
            label="Instance ID"
            value={
              <Typography
                variant="body2"
                sx={{ fontFamily: "monospace", fontSize: "0.75rem" }}
              >
                {instance_id || "—"}
              </Typography>
            }
          />
          <DetailRow label="Issued to" value={license.issued_to} />
          <DetailRow
            label="Band"
            value={BAND_LABELS[license.band] || license.band}
          />
          <DetailRow label="License type" value={license.license_type} />
          <DetailRow
            label="Expires"
            value={expiresAt ? expiresAt.toLocaleDateString() : "—"}
          />
          <DetailRow
            label="Features"
            value={
              license.features_count != null
                ? String(license.features_count)
                : String(featureEntries.length)
            }
          />
        </Stack>

        {featureEntries.length > 0 && (
          <Box mt={3}>
            <Typography
              variant="caption"
              color="text.secondary"
              fontWeight={600}
              display="block"
              mb={1}
            >
              Included features
            </Typography>
            <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap>
              {featureEntries.map(([key, feat]) => (
                <Chip
                  key={key}
                  label={feat.display_name || key.replace(/_/g, " ")}
                  size="small"
                  variant="outlined"
                  color={feat.allowed ? "success" : "default"}
                />
              ))}
            </Stack>
          </Box>
        )}
      </Card>
    </Box>
  );
}
