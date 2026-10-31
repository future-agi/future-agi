import EmptyState from "src/components/empty-state";
import ErrorState from "src/components/error-state";
import PageHeader from "src/components/page-header";
import PropTypes from "prop-types";
import { Box, Typography, Chip, Stack, Divider } from "@mui/material";
import Section from "src/components/section";
import { useCapabilities } from "src/hooks/useCapabilities";
import ContentSkeleton from "src/components/content-skeleton";

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
        <ContentSkeleton variant="panel" />
      </Box>
    );
  }

  if (isError) {
    return (
      <ErrorState
        title="Failed to load license information"
        sx={{ minHeight: 360 }}
      />
    );
  }

  const { license, instance_id, license_state, features } = data || {};

  if (!license) {
    return (
      <EmptyState
        icon="solar:document-text-linear"
        title="No license yet"
        description="No active license is configured on this instance."
        sx={{ pt: 6, pb: 6, minHeight: 360, justifyContent: "center" }}
      />
    );
  }

  const stateColor = LICENSE_STATE_COLOR[license_state] || "default";
  const expiresAt = license.expires_at ? new Date(license.expires_at) : null;
  const featureEntries = Object.entries(features || {});

  return (
    <Box>
      <PageHeader
        title="License"
        description="Enterprise license details for this deployment."
        sx={{ mb: 1 }}
      />

      <Divider sx={{ my: 2 }} />

      <Section variant="panel" sx={{ p: 3, maxWidth: 720 }}>
        <Stack
          direction="row"
          justifyContent="space-between"
          alignItems="flex-start"
          mb={3}
        >
          <Box>
            <Typography variant="h6" fontWeight={600}>
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
      </Section>
    </Box>
  );
}
