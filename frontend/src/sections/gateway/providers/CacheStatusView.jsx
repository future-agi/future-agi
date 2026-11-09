import {
  moneySign,
  multiplyMoneyValues,
} from "src/sections/common/format/money";
import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Paper,
  Typography,
  Box,
  Stack,
  Grid,
  Button,
  Divider,
} from "@mui/material";
import GatewayAdminOnly from "../components/GatewayAdminOnly";
import { useGatewayAdmin } from "../hooks/useGatewayAdmin";
import { useAnalyticsOverview } from "../analytics/hooks/useAnalyticsOverview";
import { perRequest, val } from "../utils/analyticsHelpers";
import { costTotal, EMPTY } from "../utils/formatters";
import { useOrgConfig, useCreateOrgConfig } from "./hooks/useOrgConfig";
import OrgConfigEditor from "../../gateway/settings/OrgConfigEditor";
import { sectionEnforcement } from "../settings/sectionEnforcement";
import { CACHE_OFF_LINE } from "./providerTabs";

// Whether this gateway runs response caching for orgs, from its own
// org_sections_enforced (contract K2), never from anything else: null when it
// does (nothing to say).
function cacheEnforcementText(enforced) {
  if (!Array.isArray(enforced)) return "Enforcement unknown";
  if (enforced.includes("cache")) return null;
  return "Off on this gateway: saved, not applied (response caching is off on this gateway)";
}

// Providers > Cache: what the org's cache stores and applies, and the last
// week's cache figures. Configure cache opens the org config editor, which
// anyone can read and only an Owner or Admin can save (it gates its own
// Save & Activate); Enable cache is Owner or Admin only (useGatewayAdmin).
const CacheStatusView = ({ config, gatewayId }) => {
  if (sectionEnforcement({ data: config }, "cache") === "not_enforced") {
    return <Typography typography="s2">{CACHE_OFF_LINE}</Typography>;
  }
  return <ActiveCacheStatusView config={config} gatewayId={gatewayId} />;
};

const ActiveCacheStatusView = ({ config, gatewayId }) => {
  const { canEdit } = useGatewayAdmin();
  const enforcementText = cacheEnforcementText(config?.org_sections_enforced);
  const cache = config?.cache;
  const [editorOpen, setEditorOpen] = useState(false);

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

  const { data: loaded, isError: overviewFailed } = useAnalyticsOverview({
    start: weekAgo,
    end: now,
    gatewayId,
  });
  // A failed read gives no figures: each one reads a dash, never a zero.
  const overview = overviewFailed ? undefined : loaded;

  // Compute cache hits from hit rate and total requests
  // A week with no requests has no hit rate (a dash, not 0.0%).
  const totalRequests = val(overview?.total_requests);
  const cacheHitRate = perRequest(totalRequests, val(overview?.cache_hit_rate));
  const totalCost = val(overview?.total_cost);

  let cacheHits = val(overview?.cache_hits);
  if (cacheHits == null && cacheHitRate != null && totalRequests != null) {
    cacheHits = Math.round(
      (Number(cacheHitRate) / 100) * Number(totalRequests),
    );
  } else if (cacheHits == null && totalRequests != null) {
    // No requests: no hits either, a true 0.
    if (Number(totalRequests) === 0) cacheHits = 0;
  }

  // Estimate savings: cache hits * avg cost per non-cached request. A request
  // the gateway found no price for counts 0 in the total, so with some the
  // estimate is a floor and with all of them it is unknown. The API counts
  // unpriced requests over the whole week, so the note does too.
  let estSavings = val(overview?.cacheSavings);
  let savingsDivisor = 1;
  // A supplied unknown is authoritative. Only an absent field permits an
  // estimate; retain its exact numerator/count instead of rounding division.
  const hits = Number(cacheHits);
  const requests = Number(totalRequests);
  if (
    overview &&
    !Object.prototype.hasOwnProperty.call(overview, "cacheSavings") &&
    Number.isSafeInteger(hits) &&
    hits > 0 &&
    Number.isSafeInteger(requests) &&
    requests > 0
  ) {
    savingsDivisor = requests > hits ? requests - hits : requests;
    estSavings = multiplyMoneyValues(totalCost, hits);
  }
  const savingsDisplay = costTotal(estSavings, {
    unpriced: overview?.total_cost?.unpriced_requests,
    requests: totalRequests,
    of: "estimate",
  });
  const showSavingsDivisor =
    savingsDivisor > 1 &&
    savingsDisplay.text !== EMPTY &&
    moneySign(estSavings) !== 0;

  const servedFromCache = Number(cacheHits) > 0;

  return (
    <Stack spacing={2}>
      {/* Cache config + configure button */}
      <Paper
        variant="outlined"
        component="section"
        data-testid="cache-configuration"
        sx={{ p: 2, borderRadius: 1 }}
      >
        <Stack
          direction="row"
          justifyContent="space-between"
          alignItems="center"
          flexWrap="wrap"
          gap={1}
          mb={1.5}
        >
          <Typography
            component="h2"
            typography="m3"
            fontWeight="fontWeightMedium"
          >
            Cache configuration
          </Typography>
          <Button
            variant="outlined"
            size="small"
            onClick={() => setEditorOpen(true)}
          >
            Configure cache
          </Button>
        </Stack>
        <Typography typography="s2" color="text.secondary" mb={1.5}>
          {overviewFailed
            ? "The cache figures for the last 7 days could not be loaded."
            : servedFromCache
              ? `The gateway served ${Number(cacheHits).toLocaleString()} responses from cache in the last 7 days.`
              : "No response was served from cache in the last 7 days. Enabled and TTL apply only where this gateway runs its cache."}
        </Typography>
        {!canEdit && (
          <Typography typography="s2" color="text.secondary" mb={1.5}>
            Only an organization owner or admin can change the cache.
          </Typography>
        )}
        {/* Only what the gateway applies for an org's cache: on or off and
            the default TTL. Backend, max entries, semantic and edge
            settings are never read for an org, so they are not shown. */}
        <Stack spacing={1}>
          <InfoLine label="Enabled" value={cache?.enabled ? "Yes" : "No"} />
          <InfoLine
            label="Default TTL"
            value={cache?.defaultTtl ?? cache?.default_ttl ?? EMPTY}
          />
          {enforcementText && (
            <InfoLine label="On this gateway" value={enforcementText} />
          )}
        </Stack>

        {!cache?.enabled && (
          <>
            <Divider sx={{ my: 2 }} />
            <Stack alignItems="flex-start" spacing={1}>
              <Typography typography="s1" color="text.secondary">
                Cache is not enabled. Turn it on to answer repeated requests
                from cache where this gateway runs one.
              </Typography>
              <GatewayAdminOnly allowed={canEdit}>
                <Button
                  variant="outlined"
                  size="small"
                  onClick={() => setEditorOpen(true)}
                  disabled={!canEdit}
                >
                  Enable cache
                </Button>
              </GatewayAdminOnly>
            </Stack>
          </>
        )}
      </Paper>

      {/* Cache analytics */}
      <Paper variant="outlined" sx={{ p: 2, borderRadius: 1 }}>
        <Typography
          component="h2"
          typography="m3"
          fontWeight="fontWeightMedium"
          mb={2}
        >
          Cache performance (last 7 days)
        </Typography>
        <Grid container spacing={3}>
          <Grid item xs={6} sm={3}>
            <StatBox
              label="Cache hit rate"
              value={
                cacheHitRate != null
                  ? `${Number(cacheHitRate).toFixed(1)}%`
                  : "\u2014"
              }
            />
          </Grid>
          <Grid item xs={6} sm={3}>
            <StatBox
              label="Total requests"
              value={
                totalRequests != null
                  ? Number(totalRequests).toLocaleString()
                  : "\u2014"
              }
            />
          </Grid>
          <Grid item xs={6} sm={3}>
            <StatBox
              label="Cache hits"
              value={
                cacheHits != null
                  ? Number(cacheHits).toLocaleString()
                  : "\u2014"
              }
            />
          </Grid>
          <Grid item xs={6} sm={3}>
            <StatBox
              label="Est. savings"
              value={
                <Box component="span" title={savingsDisplay.title ?? undefined}>
                  {savingsDisplay.text}
                  {showSavingsDivisor ? ` / ${savingsDivisor}` : ""}
                </Box>
              }
            />
          </Grid>
        </Grid>
      </Paper>

      {/* Org Config Editor Dialog — opens to Cache tab */}
      <OrgConfigEditor
        open={editorOpen}
        onClose={() => setEditorOpen(false)}
        onSave={handleSave}
        initialConfig={activeConfig || null}
        isSaving={createMutation.isPending}
        defaultTab="cache"
      />
    </Stack>
  );
};

const StatBox = ({ label, value }) => (
  <Box>
    <Typography typography="s2" color="text.secondary">
      {label}
    </Typography>
    <Typography typography="m2" fontWeight="fontWeightMedium">
      {value}
    </Typography>
  </Box>
);

StatBox.propTypes = {
  label: PropTypes.string.isRequired,
  value: PropTypes.node,
};

// An org's cache backend, size, semantic and edge settings are never
// read by the gateway; only enabled and TTL are (the card's one note).
const InfoLine = ({ label, value }) => (
  <Stack
    direction="row"
    spacing={2}
    alignItems="center"
    flexWrap="wrap"
    rowGap={0.5}
  >
    <Typography typography="s1" color="text.secondary" sx={{ minWidth: 140 }}>
      {label}
    </Typography>
    <Typography typography="s1">{String(value)}</Typography>
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

ActiveCacheStatusView.propTypes = {
  config: PropTypes.object,
  gatewayId: PropTypes.string,
};

export default CacheStatusView;
