import { formatExactMoney } from "src/sections/common/format/money";
import React, { useMemo, useState } from "react";
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

import {
  GatewayEmptyState,
  GatewayGrid,
  GatewayPageFrame,
} from "../../components/frame";
import { useGatewayAdmin } from "../../hooks/useGatewayAdmin";
import AutoRoutingTab from "../AutoRoutingTab";
import CodeSnippet from "../CodeSnippet";
import { useCatalogRows } from "../hooks/useCatalogRows";
import { modelPath } from "../modelLinks";
import { autoSnippets } from "../snippets";
import { formatLatency } from "../detail/modelFacts";
import { Mono, Section } from "../detail/Section";
import {
  candidatePageId,
  chosenShare,
  decisionsFromLogs,
  groupRejections,
} from "./autoDecisions";
import { decisionColumns } from "./decisionColumns";
import HowAutoChooses from "./HowAutoChooses";
import { AUTO_SCAN_LIMIT, useAutoDecisions } from "./useAutoDecisions";

const cell = {
  px: 1,
  "&:first-of-type": { pl: 0 },
  "&:last-of-type": { pr: 0 },
};
const pct = (share) => `${Math.round(share * 100)}%`;
const usd = (value) => formatExactMoney(value);

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
  return (
    <Section
      title="Candidates"
      note={`Request ${decision.requestId} · ${decision.tier || "unscored"} task · ${decision.preference || "balanced"} preference. ${
        considered != null
          ? `Top ${decision.ranked.length} of ${considered} considered.`
          : ""
      }`}
    >
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
                  {usd(r.est_cost_usd)}
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
  const scanned = logs.data?.results?.length || 0;
  const [selectedId, setSelectedId] = useState(null);
  const selected =
    decisions.find((d) => d.id === selectedId) || decisions[0] || null;
  const snippets = useMemo(() => autoSnippets(data.baseUrl), [data.baseUrl]);
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
    recent = <Skeleton variant="rounded" height={280} />;
  } else if (logs.error) {
    recent = (
      <GatewayEmptyState
        icon="routing"
        title="Couldn't load recent auto decisions"
        description={logs.error.message || "The request logs did not answer."}
        actionLabel="Retry"
        onAction={() => logs.refetch()}
      />
    );
  } else if (!decisions.length) {
    recent = (
      <GatewayEmptyState
        icon="routing"
        title="No auto-routed requests yet"
        description={`None of your latest ${scanned || AUTO_SCAN_LIMIT} requests was sent as model "auto". Send one (below) and its decision, with its candidates, shows up here.`}
      />
    );
  } else {
    recent = (
      <>
        <Section
          title="Recent decisions"
          note={`${decisions.length} of your latest ${scanned} requests were sent as model "auto". Newest first; select one to see its candidates.`}
        >
          <GatewayGrid
            rowData={decisions}
            columnDefs={decisionColumns}
            stateKey="gateway-auto-decisions"
            getRowId={(p) => p.data.id}
            height={280}
            onRowClicked={(e) => setSelectedId(e.data.id)}
            rowStyle={{ cursor: "pointer" }}
            getRowStyle={(p) =>
              p.data?.id === selected?.id
                ? { background: theme.palette.action.selected }
                : undefined
            }
          />
        </Section>
        <Box
          sx={{
            display: "grid",
            gap: 1.5,
            gridTemplateColumns: {
              xs: "minmax(0, 1fr)",
              md: "minmax(0, 2fr) minmax(0, 1fr)",
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
    >
      <HowAutoChooses managedOffered={data.status.data?.available === true} />
      {recent}
      <Typography
        typography="s1"
        fontWeight="fontWeightMedium"
        component="h2"
        sx={{ mt: 1 }}
      >
        Settings
      </Typography>
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
      <AutoRoutingTab
        gatewayId={data.gatewayId}
        canWrite={canWrite}
        catalogModels={data.catalog.data?.models}
        managedStatus={data.status.data}
      />
      <Section
        title="Call auto"
        note="The header x-agentcc-auto-preference (cost, balanced or quality) overrides the preference for one request."
      >
        <CodeSnippet snippets={snippets} ariaLabel="Auto example language" />
      </Section>
    </GatewayPageFrame>
  );
}
