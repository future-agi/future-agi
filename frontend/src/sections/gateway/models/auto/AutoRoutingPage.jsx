import { formatExactMoney } from "src/sections/common/format/money";
import React, { useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Button,
  Link,
  Skeleton,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Typography,
} from "@mui/material";
import { useTheme } from "@mui/material/styles";

import { RouterLink } from "src/routes/components";
import { useResponsive } from "src/hooks/use-responsive";

import {
  GatewayEmptyState,
  GatewayGrid,
  GatewayPageFrame,
} from "../../components/frame";
import { useGatewayAdmin } from "../../hooks/useGatewayAdmin";
import { formatDateTime } from "../../utils/formatters";
import { exactLogTime, localZoneName } from "../../logs/logDates";
import {
  BASE_URL_UNAVAILABLE,
  snippetBaseUrl,
} from "../../utils/snippetBaseUrl";
import AutoRoutingTab from "../AutoRoutingTab";
import CodeSnippet from "../CodeSnippet";
import { useCatalogRows } from "../hooks/useCatalogRows";
import { modelPath } from "../modelLinks";
import { autoSnippets } from "../snippets";
import { formatLatency } from "../detail/modelFacts";
import { CopyButton, Mono, Section } from "../detail/Section";
import {
  candidatePageId,
  chosenShare,
  decisionsFromLogs,
  decisionLogsPath,
  decisionOutcome,
  gatewayPriceEvidence,
  groupRejections,
} from "./autoDecisions";
import { AutoNote, AutoSection } from "./AutoSection";
import { decisionColumnsFor } from "./decisionColumns";
import HowAutoChooses from "./HowAutoChooses";
import { AUTO_SCAN_LIMIT, useAutoDecisions } from "./useAutoDecisions";

const cell = {
  px: 1,
  "&:first-of-type": { pl: 0 },
  "&:last-of-type": { pr: 0 },
};
const pct = (share) => `${Math.round(share * 100)}%`;
// The decisions grid is as tall as its rows (40 px each, under a 40 px
// header, plus its border and room for a sideways scrollbar), up to six rows;
// more rows scroll inside it.
const decisionsGridHeight = (rows) => Math.min(280, 40 + rows * 40 + 18);
// An empty or failed decisions read: the shared empty state, compact and
// outlined, so it does not open a large blank gap in the page.
const COMPACT_EMPTY_SX = {
  py: 2,
  flex: "none",
  border: 1,
  borderColor: "divider",
  borderRadius: 1,
};
// An estimate in a ranking is read at a glance: under a cent to 4
// significant digits ($0.0003021, not the float $0.00030209999999999997 the
// ranking carries), a cent and up to 2 decimals, always marked "≈". A
// request's own cost is exact (formatExactMoney of the log's decimal string).
const SUB_CENT_USD = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumSignificantDigits: 4,
});
const CENTS_USD = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});
const usd = (value, { estimate = false } = {}) => {
  const n = value == null || value === "" ? NaN : Number(value);
  if (!Number.isFinite(n)) return "—";
  const text =
    n !== 0 && Math.abs(n) < 0.01
      ? SUB_CENT_USD.format(n)
      : CENTS_USD.format(n);
  return estimate ? `≈ ${text}` : text;
};

function ChosenMostOften({ decisions }) {
  const share = chosenShare(decisions);
  return (
    <Section
      title="Chosen most often"
      note={`Across the last ${decisions.length} auto-routed requests.`}
    >
      <Box sx={{ display: "grid", gap: 1 }}>
        {share.slice(0, 6).map((s, i) => (
          <Box
            key={s.model}
            sx={{ display: "flex", alignItems: "center", gap: 1, minWidth: 0 }}
          >
            <Typography
              typography="s2"
              color="text.secondary"
              sx={{ width: 16 }}
            >
              {i + 1}.
            </Typography>
            <Box sx={{ flex: 1, minWidth: 0 }}>
              <Mono>{s.model}</Mono>
              <Box
                aria-hidden
                sx={{
                  mt: 0.5,
                  height: 4,
                  borderRadius: 1,
                  bgcolor: "action.hover",
                  overflow: "hidden",
                }}
              >
                <Box
                  sx={{
                    height: "100%",
                    width: pct(s.share),
                    bgcolor: "primary.main",
                  }}
                />
              </Box>
            </Box>
            <Typography
              typography="s2"
              sx={{ fontVariantNumeric: "tabular-nums", flexShrink: 0 }}
            >
              {s.count} · {pct(s.share)}
            </Typography>
          </Box>
        ))}
      </Box>
    </Section>
  );
}

ChosenMostOften.propTypes = { decisions: PropTypes.array.isRequired };

// A ranked candidate's model, linked to its model page (Overview, Try it,
// API) where it has one.
function CandidateModel({ model, href }) {
  if (!href) return <Mono>{model}</Mono>;
  return (
    <Link component={RouterLink} href={href} underline="hover">
      <Mono>{model}</Mono>
    </Link>
  );
}

CandidateModel.propTypes = {
  model: PropTypes.string,
  href: PropTypes.string,
};

function Candidates({ decision, pageHref }) {
  if (!decision) return null;
  const considered = decision.considered;
  const requestHref = decisionLogsPath(decision);
  return (
    <Section
      title="Candidates"
      note={`Request ${decision.requestId} · ${decision.tier || "unscored"} task · ${decision.preference || "balanced"} preference. ${
        considered != null
          ? `Top ${decision.ranked.length} of ${considered} considered.`
          : ""
      }`}
    >
      <Box sx={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 1 }}>
        <Typography typography="s2" color={decision.isError || decision.statusCode >= 400 ? "error.main" : "text.secondary"}>
          {decisionOutcome(decision)} · Provider: {decision.provider || "not reported"} · {formatLatency(decision.latencyMs)} · {formatExactMoney(decision.cost)}
        </Typography>
        {requestHref && (
          <Button component={RouterLink} href={requestHref} size="small" variant="outlined">
            Open request in Logs
          </Button>
        )}
      </Box>
      <Box sx={{ overflowX: "auto" }}>
        <Table size="small" aria-label="Ranked candidates">
          <TableHead>
            <TableRow>
              <TableCell sx={cell}>#</TableCell>
              <TableCell sx={cell}>Model</TableCell>
              <TableCell sx={cell} align="right">
                Tier fit
              </TableCell>
              <TableCell sx={cell} align="right">
                Score
              </TableCell>
              <TableCell sx={cell} align="right">
                Est. cost
              </TableCell>
              <TableCell sx={cell} align="right">
                Latency
              </TableCell>
              <TableCell sx={cell} align="right">
                Success
              </TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {decision.ranked.map((r, i) => (
              <TableRow key={`${r.model}-${i}`}>
                <TableCell sx={cell}>{i + 1}</TableCell>
                <TableCell sx={cell}>
                  <CandidateModel model={r.model} href={pageHref(r)} />
                  {i === 0 && (
                    <Typography
                      component="span"
                      typography="s3"
                      color="primary.main"
                      sx={{ ml: 1 }}
                    >
                      chosen
                    </Typography>
                  )}
                  {decision.fallbacks.includes(r.model) && (
                    <Typography
                      component="span"
                      typography="s3"
                      color="text.secondary"
                      sx={{ ml: 1 }}
                    >
                      fallback
                    </Typography>
                  )}
                </TableCell>
                <TableCell sx={cell} align="right">
                  {r.tier ?? "—"}
                </TableCell>
                <TableCell sx={cell} align="right">
                  {r.score ?? "—"}
                </TableCell>
                <TableCell sx={cell} align="right">
                  {usd(r.est_cost_usd, { estimate: true })}
                </TableCell>
                <TableCell sx={cell} align="right">
                  {r.latency_ms != null ? formatLatency(r.latency_ms) : "—"}
                </TableCell>
                <TableCell sx={cell} align="right">
                  {r.success_rate != null ? pct(r.success_rate) : "—"}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Box>
      {decision.rejected.length > 0 && (
        <Box sx={{ overflowX: "auto" }}>
          <Typography typography="s2" color="text.secondary" mb={0.5}>
            Set aside ({decision.rejected.length} shown)
          </Typography>
          <Table size="small" aria-label="Set aside">
            <TableBody>
              {groupRejections(decision.rejected).map((g) => (
                <TableRow key={g.reason || "none"}>
                  <TableCell
                    sx={{ ...cell, verticalAlign: "top", width: "40%" }}
                  >
                    <Typography typography="s2">{g.text}</Typography>
                    <Typography typography="s3" color="text.secondary">
                      {g.models.length === 1
                        ? "1 model"
                        : `${g.models.length} models`}
                    </Typography>
                  </TableCell>
                  <TableCell sx={{ ...cell, overflowWrap: "anywhere" }}>
                    <Mono>{g.models.join(", ")}</Mono>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Box>
      )}
    </Section>
  );
}

Candidates.propTypes = {
  decision: PropTypes.object,
  // The model page of a ranked candidate, or null when it has none.
  pageHref: PropTypes.func.isRequired,
};

// The model name to send, in the page header: `auto` and a copy button.
function AutoModelName() {
  return (
    <Box
      sx={{
        display: "inline-flex",
        alignItems: "center",
        gap: 0.5,
        pl: 1.25,
        pr: 0.25,
        py: 0.25,
        border: 1,
        borderColor: "divider",
        borderRadius: 1,
      }}
    >
      <Mono sx={{ typography: "s1" }}>auto</Mono>
      <CopyButton text="auto" label='Copy "auto"' />
    </Box>
  );
}

export default function AutoRoutingPage() {
  const theme = useTheme();
  // Saving auto routing writes the org's gateway config: Owner or Admin only
  // (useGatewayAdmin, the backend's is_gateway_admin).
  const { canEdit: canWrite } = useGatewayAdmin();
  const data = useCatalogRows();
  const logs = useAutoDecisions();
  const decisions = useMemo(
    () => decisionsFromLogs(logs.data?.results),
    [logs.data],
  );
  // What recent decisions show the gateway did about each model's price,
  // for the candidate list's own-model lines.
  const priceEvidence = useMemo(
    () => gatewayPriceEvidence(decisions),
    [decisions],
  );
  // The decision columns run past the grid's edge at 1440 px and narrower:
  // Time stays pinned in view (not on a phone, where a pinned column eats
  // the little room there is).
  const phone = useResponsive("down", "md");
  const narrowGrid = !phone;
  const columns = useMemo(
    () => decisionColumnsFor({ narrow: narrowGrid }),
    [narrowGrid],
  );
  const scanned = logs.data?.results?.length || 0;
  const callSection = useRef(null);
  const callHeading = useRef(null);
  // The empty state's way to the example request: Call auto, at the end of
  // the page. Scrolls (smoothly unless the reader asked for reduced motion)
  // and moves focus to its heading.
  const showExample = () => {
    const reduce = window.matchMedia?.(
      "(prefers-reduced-motion: reduce)",
    )?.matches;
    callSection.current?.scrollIntoView?.({
      behavior: reduce ? "auto" : "smooth",
      block: "start",
    });
    callHeading.current?.focus({ preventScroll: true });
  };
  const [selectedId, setSelectedId] = useState(null);
  const selected =
    decisions.find((d) => d.id === selectedId) || decisions[0] || null;
  // The configured public URL on a hosted deployment, the placeholder on a
  // confirmed self-hosted one, nothing until the mode and hosted URL are usable.
  const endpoint = snippetBaseUrl(data.baseUrl, {
    hosted: data.hosted,
    deploymentModeConfirmed: data.deploymentModeConfirmed,
  });
  const baseUrl = endpoint.url;
  const snippets = useMemo(
    () => (baseUrl ? autoSnippets(baseUrl) : []),
    [baseUrl],
  );
  // Which candidates have a model page: the catalog's rows (where managed
  // models are offered), and the models the org's providers serve.
  const providerList = data.providers.data?.providers;
  const pageHref = useMemo(() => {
    const catalogIds = new Set((data.rows || []).map((row) => row.id));
    return (candidate) => {
      const id = candidatePageId(candidate, {
        providers: providerList,
        catalogIds,
      });
      return id ? modelPath(id) : null;
    };
  }, [data.rows, providerList]);
  // Without the managed catalog the candidate field can offer only the
  // org's providers' models: say so once, with Retry.
  const catalogMissing =
    (Boolean(data.status.error) && !data.status.data) ||
    (data.offered && Boolean(data.catalog.error) && !data.catalog.data);
  const retryCatalog = () => {
    data.status.refetch();
    if (data.status.data) data.catalog.refetch();
  };

  let recent;
  if (logs.isLoading) {
    recent = <Skeleton variant="rounded" height={200} />;
  } else if (logs.error && !logs.data) {
    recent = (
      <GatewayEmptyState
        icon="routing"
        title="Couldn't load recent auto decisions"
        titleComponent="h3"
        description={logs.error.message || "The request logs did not answer."}
        actionLabel="Retry"
        onAction={() => logs.refetch()}
        sx={COMPACT_EMPTY_SX}
      />
    );
  } else if (!decisions.length) {
    recent = (
      <GatewayEmptyState
        icon="routing"
        title="No auto-routed requests yet"
        titleComponent="h3"
        description={`None of your latest ${scanned || AUTO_SCAN_LIMIT} requests was sent as model "auto". Send one (below) and its decision, with its candidates, shows up here.`}
        actionLabel="See the example request"
        onAction={showExample}
        sx={COMPACT_EMPTY_SX}
      />
    );
  } else {
    recent = (
      <>
        <AutoNote>
          {`${decisions.length} of your latest ${scanned} requests were sent as model "auto". Newest first. Times are in ${localZoneName()}. Select one to see its candidates.`}
        </AutoNote>
        <GatewayGrid
          rowData={decisions}
          columnDefs={columns}
          stateKey="gateway-auto-decisions"
          getRowId={(p) => p.data.id}
          height={decisionsGridHeight(decisions.length)}
          onRowClicked={(e) => setSelectedId(e.data.id)}
          // A layout saved at another width never decides the pin.
          onGridReady={(e) =>
            e.api.applyColumnState({
              state: [{ colId: "at", pinned: narrowGrid ? "left" : null }],
            })
          }
          scrollCue
          rowStyle={{ cursor: "pointer" }}
          getRowStyle={(p) =>
            p.data?.id === selected?.id
              ? { background: theme.palette.action.selected }
              : undefined
          }
        />
        <Box
          sx={{
            display: "grid",
            gap: 1.5,
            gridTemplateColumns: {
              xs: "minmax(0, 1fr)",
              lg: "minmax(0, 2fr) minmax(0, 1fr)",
            },
            alignItems: "start",
          }}
        >
          <Candidates decision={selected} pageHref={pageHref} />
          <ChosenMostOften decisions={decisions} />
        </Box>
      </>
    );
  }

  return (
    <GatewayPageFrame
      title="Auto routing"
      description='Send model "auto" and the gateway picks a model for each request. Here is what it does, what it chose, and from which candidates.'
      actions={<AutoModelName />}
    >
      <Box
        sx={{ display: "flex", flexDirection: "column", gap: 3, minWidth: 0 }}
      >
        {catalogMissing && (
          <Alert
            severity="warning"
            action={
              <Button color="inherit" size="small" onClick={retryCatalog}>
                Retry
              </Button>
            }
          >
            Couldn&apos;t load the Future AGI catalogue, so the candidate list
            below shows your providers&apos; models only.
          </Alert>
        )}
        {/* The policy beside how auto uses it from lg; stacked below. */}
        <Box
          sx={{
            display: "grid",
            gap: 3,
            alignItems: "start",
            gridTemplateColumns: {
              xs: "minmax(0, 1fr)",
              lg: "minmax(0, 2fr) minmax(0, 1fr)",
            },
          }}
        >
          <AutoRoutingTab
            gatewayId={data.gatewayId}
            canWrite={canWrite}
            catalogModels={data.catalog.data?.models}
            managedStatus={data.status.data}
            priceEvidence={priceEvidence}
          />
          <HowAutoChooses
            managedOffered={data.status.data?.available === true}
          />
        </Box>
        <AutoSection
          title="Recent decisions"
          note={
            <>
              Bounded sample: latest {AUTO_SCAN_LIMIT} requests only, not the
              full auto-routing history.
              {logs.dataUpdatedAt > 0 && (
                <>
                  {" "}
                  Sample last loaded{" "}
                  <time
                    dateTime={new Date(logs.dataUpdatedAt).toISOString()}
                    title={exactLogTime(logs.dataUpdatedAt)}
                  >
                    {formatDateTime(logs.dataUpdatedAt)}
                  </time>{" "}
                  {localZoneName()}.
                </>
              )}
            </>
          }
          actions={
            <Button
              size="small"
              variant="outlined"
              disabled={logs.isFetching}
              onClick={() => logs.refetch()}
            >
              {logs.isFetching ? "Refreshing decisions…" : "Refresh decisions"}
            </Button>
          }
        >
          {logs.error && logs.data && (
            <Alert severity="warning">
              Could not refresh decisions. Showing the last loaded sample; use
              Refresh decisions to try again.
            </Alert>
          )}
          {recent}
        </AutoSection>
        <AutoSection
          title="Call auto"
          note="The header x-agentcc-auto-preference (cost, balanced or quality) overrides the preference for one request."
          sectionRef={callSection}
          headingRef={callHeading}
        >
          {endpoint.kind !== "configured" && (
            // A note, not an error: it is not announced as an alert.
            <Alert
              severity="info"
              role="status"
              action={
                !data.deploymentModeConfirmed && data.retryDeploymentMode ? (
                  <Button
                    color="inherit"
                    size="small"
                    onClick={() => data.retryDeploymentMode()}
                  >
                    Retry deployment mode
                  </Button>
                ) : undefined
              }
            >
              {BASE_URL_UNAVAILABLE}
            </Alert>
          )}
          {snippets.length > 0 && (
            <CodeSnippet
              snippets={snippets}
              ariaLabel="Auto example language"
            />
          )}
        </AutoSection>
      </Box>
    </GatewayPageFrame>
  );
}
