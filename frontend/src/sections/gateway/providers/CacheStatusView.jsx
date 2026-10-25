import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Card,
  CardContent,
  Typography,
  Box,
  Stack,
  Grid,
  Button,
  Divider,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { useAnalyticsOverview } from "../analytics/hooks/useAnalyticsOverview";
import { val } from "../utils/analyticsHelpers";
import { useOrgConfig, useCreateOrgConfig } from "./hooks/useOrgConfig";
import OrgConfigEditor from "../../gateway/settings/OrgConfigEditor";

// Whether this gateway runs response caching for orgs, from its own
// org_sections_enforced, never from anything else: null when it
// does (nothing to say).
function cacheEnforcementText(enforced) {
  if (!Array.isArray(enforced)) return "Enforcement unknown";
  if (enforced.includes("cache")) return null;
  return "Off on this gateway: saved, not applied (response caching is off on this gateway)";
}

const CacheStatusView = ({ config, gatewayId }) => {
  const cache = config?.cache;
  const [editorOpen, setEditorOpen] = useState(false);
  const [editorTab] = useState(3); // Cache tab index

  const { data: activeConfig } = useOrgConfig();
  const createMutation = useCreateOrgConfig();

  const handleSave = (configData) => {
    createMutation.mutate(configData, {
      onSuccess: () => setEditorOpen(false),
    });
  };

  // Get cache hit rate from analytics
  const now = useMemo(() => new Date().toISOString(), []);
  const weekAgo = useMemo(() => {
    const d = new Date();
    d.setDate(d.getDate() - 7);
    return d.toISOString();
  }, []);

  const { data: overview } = useAnalyticsOverview({
    start: weekAgo,
    end: now,
    gatewayId,
  });

  // Compute cache hits from hit rate and total requests
  const cacheHitRate = val(overview?.cache_hit_rate);
  const totalRequests = val(overview?.total_requests);
  const totalCost = val(overview?.total_cost);

  let cacheHits = val(overview?.cache_hits);
  if (cacheHits == null && cacheHitRate != null && totalRequests != null) {
    cacheHits = Math.round(
      (Number(cacheHitRate) / 100) * Number(totalRequests),
    );
  }

  // Estimate savings: cache hits * avg cost per non-cached request
  let estSavings = val(overview?.cacheSavings);
  if (
    estSavings == null &&
    cacheHits > 0 &&
    totalCost != null &&
    totalRequests > 0
  ) {
    const nonCacheRequests = Number(totalRequests) - cacheHits;
    const avgCostPerRequest =
      nonCacheRequests > 0
        ? Number(totalCost) / nonCacheRequests
        : Number(totalCost) / Number(totalRequests);
    estSavings = cacheHits * avgCostPerRequest;
  }

  return (
    <Stack spacing={2}>
      {/* Cache config + configure button */}
      <Card>
        <CardContent>
          <Stack
            direction="row"
            justifyContent="space-between"
            alignItems="center"
            mb={1.5}
          >
            <Typography variant="h6">Cache Configuration</Typography>
            <Button
              variant="contained"
              size="small"
              startIcon={<Iconify icon="mdi:cog" width={18} />}
              onClick={() => setEditorOpen(true)}
            >
              Configure Cache
            </Button>
          </Stack>
          {/* Only what the gateway applies for an org's cache: on or off and
              the default TTL. Backend, max entries, semantic and
              edge settings are never read for an org, so they are not
              shown. */}
          <Stack spacing={1}>
            <InfoLine label="Enabled" value={cache?.enabled ? "Yes" : "No"} />
            <InfoLine
              label="Default TTL"
              value={cache?.defaultTtl ?? cache?.default_ttl ?? "\u2014"}
            />
            {cacheEnforcementText(config?.org_sections_enforced) && (
              <InfoLine
                label="On This Gateway"
                value={cacheEnforcementText(config?.org_sections_enforced)}
              />
            )}
          </Stack>

          {!cache?.enabled && (
            <>
              <Divider sx={{ my: 2 }} />
              <Stack alignItems="center" spacing={1} sx={{ py: 2 }}>
                <Iconify
                  icon="mdi:cached"
                  width={40}
                  sx={{ color: "text.disabled" }}
                />
                <Typography
                  variant="body2"
                  color="text.secondary"
                  textAlign="center"
                >
                  Cache is not enabled. Configure cache to reduce latency and
                  costs by caching repeated LLM responses.
                </Typography>
                <Button
                  variant="outlined"
                  size="small"
                  onClick={() => setEditorOpen(true)}
                  startIcon={<Iconify icon="mdi:plus" width={18} />}
                >
                  Enable Cache
                </Button>
              </Stack>
            </>
          )}
        </CardContent>
      </Card>

      {/* Cache analytics */}
      <Card>
        <CardContent>
          <Typography variant="h6" mb={2}>
            Cache Performance (Last 7 days)
          </Typography>
          <Grid container spacing={3}>
            <Grid item xs={6} sm={3}>
              <StatBox
                label="Cache Hit Rate"
                value={
                  cacheHitRate != null
                    ? `${Number(cacheHitRate).toFixed(1)}%`
                    : "\u2014"
                }
              />
            </Grid>
            <Grid item xs={6} sm={3}>
              <StatBox
                label="Total Requests"
                value={Number(totalRequests ?? 0).toLocaleString()}
              />
            </Grid>
            <Grid item xs={6} sm={3}>
              <StatBox
                label="Cache Hits"
                value={
                  cacheHits != null
                    ? Number(cacheHits).toLocaleString()
                    : "\u2014"
                }
              />
            </Grid>
            <Grid item xs={6} sm={3}>
              <StatBox
                label="Est. Savings"
                value={
                  estSavings != null
                    ? `$${Number(estSavings).toFixed(2)}`
                    : "\u2014"
                }
              />
            </Grid>
          </Grid>
        </CardContent>
      </Card>

      {/* Org Config Editor Dialog — opens to Cache tab */}
      <OrgConfigEditor
        open={editorOpen}
        onClose={() => setEditorOpen(false)}
        onSave={handleSave}
        initialConfig={activeConfig || null}
        isSaving={createMutation.isPending}
        defaultTab={editorTab}
      />
    </Stack>
  );
};

const StatBox = ({ label, value }) => (
  <Box textAlign="center">
    <Typography variant="caption" color="text.secondary">
      {label}
    </Typography>
    <Typography variant="h5" fontWeight={600}>
      {value}
    </Typography>
  </Box>
);

StatBox.propTypes = {
  label: PropTypes.string.isRequired,
  value: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
};

const InfoLine = ({ label, value }) => (
  <Stack direction="row" spacing={2}>
    <Typography variant="body2" color="text.secondary" sx={{ minWidth: 140 }}>
      {label}
    </Typography>
    <Typography variant="body2">{String(value)}</Typography>
  </Stack>
);

InfoLine.propTypes = {
  label: PropTypes.string.isRequired,
  value: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
};

CacheStatusView.propTypes = {
  config: PropTypes.object,
  gatewayId: PropTypes.string,
};

export default CacheStatusView;
