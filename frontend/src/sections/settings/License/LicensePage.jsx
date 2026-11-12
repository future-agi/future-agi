import EmptyState from "src/components/empty-state";
import ErrorState from "src/components/error-state";
import PageHeader from "src/components/page-header";
import PropTypes from "prop-types";
import { Box, Button, Typography, Chip, Stack, Divider } from "@mui/material";
import Section from "src/components/section";
import { useCapabilities } from "src/hooks/useCapabilities";
import ContentSkeleton from "src/components/content-skeleton";
import { CONTACT_URL } from "src/components/oss-upgrade-gate/constants";

// How a self-hosted install with no license adds one, by image. The upgrade
// cards send an admin here, so this page says where the key goes and where to
// get one. Cloud has no license to add and keeps the plain empty state. An
// open-source image has no Enterprise code, so a key alone does nothing there.
// The sentence before these steps (NO_LICENSE_ADDS) already says there is no
// license, so the steps do not say it again.
const NO_LICENSE_HOW_TO_ADD = {
  self_hosted_ee_image:
    "To add one, set EE_LICENSE_KEY on this deployment to the key we sent you, then restart it.",
  oss_image:
    "This instance runs the open-source image, so a license key alone does not turn on Enterprise features. Switch this deployment to the Enterprise Edition image, set EE_LICENSE_KEY to the key we sent you, then restart it.",
};

// What a license adds, by image, said first under the page header. An
// open-source image needs the Enterprise Edition image as well; other
// deployments (Cloud) have no license to add and keep their own sentence.
const NO_LICENSE_ADDS = {
  self_hosted_ee_image:
    "There is no license on this deployment; a license turns on its Enterprise features.",
  oss_image:
    "There is no license on this deployment; with the Enterprise Edition, a license turns on its Enterprise features.",
};

// The page header, the same with and without a license.
function LicenseHeader() {
  return (
    <>
      <PageHeader
        title="License"
        description="Enterprise license details for this deployment."
        sx={{ mb: 1 }}
      />

      <Divider sx={{ my: 2 }} />
    </>
  );
}

const CONFIG_REFERENCE_URL =
  "https://docs.futureagi.com/docs/self-hosting/configuration/reference";

// The license states the server sends (tfc/licensing/types.py LicenseState),
// named in words. A state not listed here shows no chip, never the raw value.
const LICENSE_STATE_CHIP = {
  active: { label: "Active", color: "success" },
  grace: { label: "Grace period", color: "warning" },
  expired: { label: "Expired", color: "error" },
  invalid: { label: "Invalid", color: "error" },
  trial_active: { label: "Trial", color: "info" },
  trial_expired: { label: "Trial expired", color: "error" },
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
  const { data, isLoading, isError, isRefetching, refetch } = useCapabilities();

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
        role="alert"
        title="Failed to load license information"
        detail="Check your connection and retry."
        onRetry={() => refetch()}
        retrying={isRefetching}
        sx={{ minHeight: 360 }}
      />
    );
  }

  const { license, instance_id, license_state, features, deployment_flavor } =
    data || {};

  if (!license) {
    const howToAdd = NO_LICENSE_HOW_TO_ADD[deployment_flavor];
    const adds = NO_LICENSE_ADDS[deployment_flavor];
    return (
      <Box>
        <LicenseHeader />
        <EmptyState
          icon="solar:document-text-linear"
          title="No license"
          description={
            adds && howToAdd ? (
              <>
                <span>{adds}</span> <span>{howToAdd}</span>
              </>
            ) : (
              howToAdd || "No active license is configured on this instance."
            )
          }
          action={
            howToAdd ? (
              <Button
                size="small"
                variant="contained"
                color="primary"
                href={CONTACT_URL}
                target="_blank"
                rel="noopener noreferrer"
              >
                Talk to us about a license
              </Button>
            ) : undefined
          }
          docsHref={howToAdd ? CONFIG_REFERENCE_URL : undefined}
          docsLabel="Configuration reference"
          sx={{ pt: 6, pb: 6, minHeight: 360, justifyContent: "center" }}
        />
      </Box>
    );
  }

  const stateChip = LICENSE_STATE_CHIP[license_state];
  const expiresAt = license.expires_at ? new Date(license.expires_at) : null;
  // Licensed features only: the hosted Omega products and their parts
  // (`omega_*`) are decided by rollout and readiness, never by a license, and a
  // product or part that is not open must not be named here.
  const featureEntries = Object.entries(features || {}).filter(
    ([key]) => !key.startsWith("omega_"),
  );

  return (
    <Box>
      <LicenseHeader />

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
          {stateChip && (
            <Chip
              label={stateChip.label}
              color={stateChip.color}
              size="small"
              variant="outlined"
            />
          )}
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
