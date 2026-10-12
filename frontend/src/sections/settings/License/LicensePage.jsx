import React, { useEffect, useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Button,
  Card,
  Chip,
  CircularProgress,
  Collapse,
  Divider,
  Stack,
  Typography,
} from "@mui/material";
import { useQueryClient } from "@tanstack/react-query";
import Iconify from "src/components/iconify";
import {
  SALES_EMAIL,
  SALES_MAILTO,
} from "src/components/feature-gate/enterprise-gate";
import { REASONS } from "src/components/oss-upgrade-gate/constants";
import { CAPABILITIES_QUERY_KEY } from "src/hooks/useCapabilities";
import { EDITION_QUERY_KEY, useEdition } from "src/hooks/useEdition";

const LICENSE_STATE_COLOR = {
  active: "success",
  trial_active: "success",
  grace: "warning",
  expired: "error",
  trial_expired: "error",
  invalid: "error",
};

// Existing reason copy for a licence that does not lift the Community rule.
const LICENSE_STATE_NOTE = {
  expired: REASONS.LICENSE_EXPIRED.note,
  trial_expired: REASONS.LICENSE_TRIAL_EXPIRED.note,
  invalid: REASONS.LICENSE_INVALID.note,
};

const LIMIT_LABELS = {
  organizations: { one: "organization", many: "organizations" },
  workspaces: { one: "workspace", many: "workspaces" },
  members: { one: "member", many: "members" },
};

const ENTERPRISE_ADDITIONS = [
  "Falcon AI",
  "Turing Models",
  "Protect",
  "Error Feed",
  "More than 3 members, organizations and workspaces",
];

const ACTIVATION_STEPS = [
  `Get a license from ${SALES_EMAIL}.`,
  "Set the matching authorized issuer PUBLIC key (EE_LICENSE_PUBLIC_KEY or EE_LICENSE_PUBLIC_KEYS) on every backend, worker and Temporal worker container when this release uses an external trust root. Never use a private signing key.",
  "Set EE_LICENSE_KEY on every backend, worker and Temporal worker container (.env for Docker Compose, the license Secret for Helm).",
  "Restart all of them: docker compose up -d, or helm upgrade and a rollout restart of the backend, worker and Temporal worker deployments.",
  "Finish restarting every service before creating more members, workspaces or organizations, then reload this page.",
];

function formatDate(value) {
  return value ? new Date(value).toLocaleDateString() : null;
}

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

function LicenseStateChip({ state }) {
  if (!state || state === "missing") return null;
  return (
    <Chip
      label={state.replace(/_/g, " ")}
      color={LICENSE_STATE_COLOR[state] || "default"}
      size="small"
      variant="outlined"
    />
  );
}

LicenseStateChip.propTypes = { state: PropTypes.string };

function overLimitResources(limits) {
  return Object.entries(limits || {})
    .filter(([, { limit, current }]) => limit != null && current > limit)
    .map(([resource]) => LIMIT_LABELS[resource]?.many || resource);
}

function ActivationPanel() {
  return (
    <Box
      sx={{
        mt: 2,
        p: 2,
        borderRadius: 1,
        border: "1px solid",
        borderColor: "divider",
      }}
    >
      <Typography variant="subtitle2" gutterBottom>
        Activate a license
      </Typography>
      <Stack component="ol" spacing={0.75} sx={{ pl: 2.5, m: 0 }}>
        {ACTIVATION_STEPS.map((step) => (
          <Typography component="li" variant="body2" key={step}>
            {step}
          </Typography>
        ))}
      </Stack>
    </Box>
  );
}

function CommunityCard({ data }) {
  const [showActivation, setShowActivation] = useState(false);
  const overLimit = overLimitResources(data.limits);

  return (
    <Card variant="outlined" sx={{ p: 3, borderRadius: 2, maxWidth: 720 }}>
      <Stack
        direction="row"
        justifyContent="space-between"
        alignItems="flex-start"
        mb={2}
      >
        <Box>
          <Typography variant="h6" fontWeight={700}>
            Community · Self-hosted
          </Typography>
          <Typography variant="body2" color="text.secondary">
            1 organization · 1 workspace · up to 3 members
          </Typography>
        </Box>
        <LicenseStateChip state={data.license?.state} />
      </Stack>

      {LICENSE_STATE_NOTE[data.license?.state] && (
        <Alert severity="info" sx={{ mb: 2 }}>
          {LICENSE_STATE_NOTE[data.license.state]}
        </Alert>
      )}

      {overLimit.length > 0 && (
        <Alert severity="warning" sx={{ mb: 2 }}>
          This install has more {overLimit.join(" and ")} than Community
          includes. Everything you have stays; creating more needs Enterprise.
        </Alert>
      )}

      <Stack spacing={1.5}>
        {Object.entries(data.limits || {}).map(([resource, entry]) => (
          <DetailRow
            key={resource}
            label={
              resource.charAt(0).toUpperCase() +
              (LIMIT_LABELS[resource]?.many || resource).slice(1)
            }
            value={`${entry.current} / ${entry.limit}`}
          />
        ))}
      </Stack>

      <Divider sx={{ my: 2.5 }} />

      <Typography variant="subtitle2" gutterBottom>
        Included
      </Typography>
      <Typography variant="body2" color="text.secondary" mb={2}>
        All other products, with no usage caps.
      </Typography>

      <Typography variant="subtitle2" gutterBottom>
        Enterprise adds
      </Typography>
      <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap mb={3}>
        {ENTERPRISE_ADDITIONS.map((item) => (
          <Chip key={item} label={item} size="small" variant="outlined" />
        ))}
      </Stack>

      <Stack direction="row" spacing={1}>
        <Button
          variant="contained"
          href={SALES_MAILTO}
          endIcon={<Iconify icon="solar:arrow-right-up-linear" />}
        >
          Contact sales
        </Button>
        <Button
          variant="outlined"
          color="inherit"
          onClick={() => setShowActivation((open) => !open)}
        >
          Activate license
        </Button>
      </Stack>
      <Collapse in={showActivation} unmountOnExit>
        <ActivationPanel />
      </Collapse>
    </Card>
  );
}

CommunityCard.propTypes = { data: PropTypes.object.isRequired };

function EnterpriseCard({ data }) {
  const license = data.license;
  return (
    <Card variant="outlined" sx={{ p: 3, borderRadius: 2, maxWidth: 720 }}>
      <Stack
        direction="row"
        justifyContent="space-between"
        alignItems="flex-start"
        mb={3}
      >
        <Box>
          <Typography variant="h6" fontWeight={700}>
            Enterprise · Self-hosted
          </Typography>
          {license?.issued_to && (
            <Typography variant="body2" color="text.secondary">
              {license.issued_to}
            </Typography>
          )}
        </Box>
        <LicenseStateChip state={license?.state} />
      </Stack>

      {license?.state === "grace" && license.grace_ends_at && (
        <Alert severity="warning" sx={{ mb: 2 }}>
          Your license has expired and is in its grace period until{" "}
          {formatDate(license.grace_ends_at)}. Renew it with {SALES_EMAIL}.
        </Alert>
      )}

      {license && (
        <Stack spacing={1.5}>
          <DetailRow label="Issued to" value={license.issued_to} />
          <DetailRow label="License type" value={license.license_type} />
          <DetailRow label="Expires" value={formatDate(license.expires_at)} />
          {license.grace_ends_at && (
            <DetailRow
              label="Grace ends"
              value={formatDate(license.grace_ends_at)}
            />
          )}
          <DetailRow
            label="License ID"
            value={
              <Typography
                component="span"
                variant="body2"
                sx={{ fontFamily: "monospace" }}
              >
                {license.license_id_masked}
              </Typography>
            }
          />
          <DetailRow
            label="Key fingerprint"
            value={
              <Typography
                component="span"
                variant="body2"
                sx={{ fontFamily: "monospace" }}
              >
                {license.key_fingerprint}
              </Typography>
            }
          />
        </Stack>
      )}

      <Typography variant="body2" color="text.secondary" mt={3}>
        Renewals and help: {SALES_EMAIL}
      </Typography>
    </Card>
  );
}

EnterpriseCard.propTypes = { data: PropTypes.object.isRequired };

export default function LicensePage() {
  const queryClient = useQueryClient();
  const { data, isLoading, isError } = useEdition();

  // Activation is env + restart: refresh everything that reflects the
  // licence when the page opens and when the tab regains focus.
  useEffect(() => {
    const refresh = () => {
      queryClient.invalidateQueries({ queryKey: EDITION_QUERY_KEY });
      queryClient.invalidateQueries({ queryKey: CAPABILITIES_QUERY_KEY });
      queryClient.invalidateQueries({ queryKey: ["deployment-info"] });
    };
    refresh();
    window.addEventListener("focus", refresh);
    return () => window.removeEventListener("focus", refresh);
  }, [queryClient]);

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

  if (isError || !data) {
    return (
      <Box
        display="flex"
        justifyContent="center"
        alignItems="center"
        minHeight={360}
      >
        <Typography color="text.secondary">
          Failed to load plan and license information.
        </Typography>
      </Box>
    );
  }

  return (
    <Box>
      <Box mb={1}>
        <Typography variant="h5" fontWeight={700}>
          Plan &amp; License
        </Typography>
        <Typography variant="body2" color="text.secondary">
          Your edition, what it includes, and its license.
        </Typography>
      </Box>

      <Divider sx={{ my: 2 }} />

      {data.edition === "enterprise" && <EnterpriseCard data={data} />}
      {data.edition === "community" && <CommunityCard data={data} />}
      {data.edition === "cloud" && (
        <Typography color="text.secondary">
          This deployment is Future AGI Cloud: plans are under Billing.
        </Typography>
      )}
    </Box>
  );
}
