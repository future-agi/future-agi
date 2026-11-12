import React from "react";
import PropTypes from "prop-types";
import { Box, Chip, Link, Tooltip, Typography } from "@mui/material";

import { RouterLink } from "src/routes/components";
import { paths } from "src/routes/paths";

import { formatDate } from "../../utils/formatters";
import {
  CAPABILITIES,
  formatPerMillion,
  formatTokenCount,
} from "../catalog";
import { ACCESS, plainDescription, typeEndpoint } from "../catalogRows";
import { MANAGED_MODE_LABEL, OWN_KEY_LABEL } from "../../utils/providerLabel";
import ModelHealth from "./ModelHealth";
import { pricingColumns } from "./pricingColumns";
import { modelRoutes, perRequestPrices, pricingRows } from "./modelFacts";
import { FACT_CELL, FactGrid, FactText, Mono, Section } from "./Section";
import { GATEWAY_MONO } from "../../utils/fonts";
import { hasInclusivePrice } from "../inclusivePricing";
import { InclusivePricingTable } from "../InclusivePriceViews";

const DASH = "—";
const list = (value) =>
  Array.isArray(value) && value.length ? value.join(", ") : DASH;

function Fact({ label, children }) {
  return (
    <Box sx={{ minWidth: 0 }}>
      <Typography typography="s2" color="text.secondary" component="div">
        {label}
      </Typography>
      <Typography
        typography="s1"
        component="div"
        sx={{ overflowWrap: "anywhere" }}
      >
        {children}
      </Typography>
    </Box>
  );
}

Fact.propTypes = {
  label: PropTypes.string.isRequired,
  children: PropTypes.node,
};

function Facts({ row }) {
  const m = row.model;
  return (
    <Box
      sx={{
        display: "grid",
        gap: 2,
        gridTemplateColumns: {
          xs: "repeat(2, minmax(0, 1fr))",
          md: "repeat(6, minmax(0, 1fr))",
        },
        border: 1,
        borderColor: "divider",
        borderRadius: 1,
        p: 2,
      }}
    >
      <Fact label="Context window">
        {row.context ? `${formatTokenCount(row.context)} tokens` : DASH}
      </Fact>
      <Fact label="Max output">
        {row.maxOutput ? `${formatTokenCount(row.maxOutput)} tokens` : DASH}
      </Fact>
      <Fact label="Input">{list(m.input_modalities)}</Fact>
      <Fact label="Output">{list(m.output_modalities)}</Fact>
      <Fact label="Released">
        {row.released
          ? formatDate(new Date(row.released * 1000).toISOString())
          : DASH}
      </Fact>
      <Fact label="Endpoint">
        <Mono>POST /v1{typeEndpoint(row.type)}</Mono>
      </Fact>
    </Box>
  );
}

Facts.propTypes = { row: PropTypes.object.isRequired };

// One price per row: on Future AGI credits, what a request is charged; for a
// model only an own key reaches, the provider's price. Never both, never the
// margin between them.
function Pricing({ row, markupPercent }) {
  const sold = row.access === ACCESS.MANAGED || row.access === ACCESS.BOTH;
  if (sold && hasInclusivePrice(row.model)) {
    return (
      <Section title="Pricing">
        <InclusivePricingTable
          model={row.model}
          markupPercent={markupPercent}
        />
      </Section>
    );
  }
  const rows = pricingRows(row.model, { sold, markupPercent });
  const perRequest = perRequestPrices(row.model, { sold, markupPercent });
  const hasPrice = rows.length > 0;
  return (
    <Section
      title="Pricing"
      note={
        sold
          ? "What a request on Future AGI credits is charged. With your own key, your provider bills you directly."
          : row.accessReason
      }
    >
      {hasPrice ? (
        <FactGrid
          rowData={rows.map((r) => ({ ...r, key: r.label }))}
          columnDefs={pricingColumns(sold)}
        />
      ) : (
        <Typography typography="s2" color="text.secondary">
          {row.model.state === "blocked"
            ? "Pricing is unavailable for this model."
            : "The catalog lists no per-token price for this model."}
        </Typography>
      )}
      {perRequest.map((p) => (
        <Typography key={p.label} typography="s2" color="text.secondary">
          {p.label}: {formatPerMillion(sold ? p.managed : p.provider)}
        </Typography>
      ))}
    </Section>
  );
}

Pricing.propTypes = {
  row: PropTypes.object.isRequired,
  markupPercent: PropTypes.number,
};

// Two-line cells wrap and the row grows to fit them. AG Grid's cells do not
// wrap, so each cell's content opts in and may shrink to its column.
const WRAP_CELL = { lineHeight: 1.3, minWidth: 0, whiteSpace: "normal" };
const wrapping = {
  autoHeight: true,
  cellStyle: { ...FACT_CELL, paddingTop: 8, paddingBottom: 8 },
};

function routeColumns(row) {
  const endpoints = row.model?.endpoints_count ?? 0;
  return [
    {
      field: "label",
      headerName: "Route",
      flex: 1.3,
      minWidth: 150,
      ...wrapping,
      cellRenderer: ({ data }) => (
        <Box sx={WRAP_CELL}>
          <FactText>{data.label}</FactText>
          <FactText secondary>
            {data.managed
              ? `${endpoints} upstream endpoint${endpoints === 1 ? "" : "s"}`
              : OWN_KEY_LABEL}
          </FactText>
        </Box>
      ),
    },
    {
      field: "sendAs",
      headerName: "Send model",
      flex: 1.3,
      minWidth: 150,
      ...wrapping,
      // An id has no spaces: it breaks anywhere, inside its own column.
      cellRenderer: ({ value }) => (
        <Box sx={WRAP_CELL}>
          <Mono>{value}</Mono>
        </Box>
      ),
    },
    {
      field: "billing",
      headerName: "Billing",
      flex: 1.3,
      minWidth: 150,
      ...wrapping,
      valueGetter: ({ data }) =>
        data.managed
          ? `${MANAGED_MODE_LABEL}, at the listed price`
          : `${OWN_KEY_LABEL}, billed by your provider`,
      cellRenderer: ({ data, value }) => (
        <Box sx={WRAP_CELL}>
          <FactText>{value}</FactText>
          {!data.on && (
            <FactText sx={{ color: "warning.main" }}>
              {data.managed ? "Off for your organization" : data.statusReason || "Not synced to the gateway"}
            </FactText>
          )}
        </Box>
      ),
    },
  ];
}

function Routes({ row, routes }) {
  return (
    <Section
      title="Routes"
      note="How this gateway reaches the model for your organization."
    >
      {routes.length ? (
        <FactGrid
          rowData={routes}
          columnDefs={routeColumns(row)}
        />
      ) : (
        <Typography typography="s2" color="text.secondary">
          {row.accessReason} To call it, add a provider that serves it with your
          own key under{" "}
          <Link component={RouterLink} href={paths.dashboard.gateway.providers}>
            Providers
          </Link>
          .
        </Typography>
      )}
    </Section>
  );
}

Routes.propTypes = {
  row: PropTypes.object.isRequired,
  routes: PropTypes.array.isRequired,
};

function Capabilities({ row }) {
  const params = Array.isArray(row.model.supported_parameters)
    ? row.model.supported_parameters
    : [];
  const caps = CAPABILITIES.filter((c) => row.capabilities.includes(c.key));
  return (
    <Section title="Capabilities">
      {caps.length ? (
        <Box sx={{ display: "flex", flexWrap: "wrap", gap: 0.75 }}>
          {caps.map((c) => (
            <Tooltip key={c.key} title={c.description} arrow>
              <Chip size="small" variant="outlined" label={c.label} />
            </Tooltip>
          ))}
        </Box>
      ) : (
        <Typography typography="s2" color="text.secondary">
          No tools, structured output, image input or reasoning controls listed.
        </Typography>
      )}
      {params.length > 0 && (
        <Box>
          <Typography typography="s2" color="text.secondary" mb={0.5}>
            Supported request parameters
          </Typography>
          <Box sx={{ display: "flex", flexWrap: "wrap", gap: 0.5 }}>
            {params.map((p) => (
              <Chip
                key={p}
                size="small"
                variant="outlined"
                label={p}
                sx={{ fontFamily: GATEWAY_MONO }}
              />
            ))}
          </Box>
        </Box>
      )}
    </Section>
  );
}

Capabilities.propTypes = { row: PropTypes.object.isRequired };

// The overview of a model only the organization's own providers serve
// (orgServed.js): the catalog knows nothing about it, so the page shows how
// the gateway reaches it and its live health.
export function OrgServedOverview({ row }) {
  const routes = modelRoutes(row);
  return (
    <Box
      sx={{
        display: "grid",
        gap: 1.5,
        gridTemplateColumns: {
          xs: "minmax(0, 1fr)",
          md: "repeat(2, minmax(0, 1fr))",
        },
        alignItems: "start",
      }}
    >
      <Routes row={row} routes={routes} />
      <ModelHealth routes={routes} modelId={row.id} />
    </Box>
  );
}

OrgServedOverview.propTypes = { row: PropTypes.object.isRequired };

export default function ModelOverview({ row, markupPercent }) {
  const routes = modelRoutes(row);
  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
      {row.model.description && (
        <Typography
          typography="s1"
          color="text.secondary"
          sx={{ whiteSpace: "pre-line", maxWidth: 960 }}
        >
          {plainDescription(row.model.description)}
        </Typography>
      )}
      <Facts row={row} />
      <Box
        sx={{
          display: "grid",
          gap: 1.5,
          gridTemplateColumns: {
            xs: "minmax(0, 1fr)",
            md: "repeat(2, minmax(0, 1fr))",
          },
          alignItems: "start",
        }}
      >
        <Box
          sx={{
            display: "flex",
            flexDirection: "column",
            gap: 1.5,
            minWidth: 0,
          }}
        >
          <Pricing row={row} markupPercent={markupPercent} />
          <Capabilities row={row} />
        </Box>
        <Box
          sx={{
            display: "flex",
            flexDirection: "column",
            gap: 1.5,
            minWidth: 0,
          }}
        >
          <Routes row={row} routes={routes} />
          <ModelHealth routes={routes} modelId={row.id} />
        </Box>
      </Box>
    </Box>
  );
}

ModelOverview.propTypes = {
  row: PropTypes.object.isRequired,
  markupPercent: PropTypes.number,
};
