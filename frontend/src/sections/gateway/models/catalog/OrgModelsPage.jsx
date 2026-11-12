import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Button,
  Link,
  MenuItem,
  Skeleton,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import { useNavigate } from "react-router-dom";

import { RouterLink } from "src/routes/components";
import { paths } from "src/routes/paths";

import {
  GatewayEmptyState,
  GatewayFilterBar,
  GatewayGrid,
  GatewayPageFrame,
  GatewayStatusChip,
} from "../../components/frame";
import { useGatewayContext } from "../../context/useGatewayContext";
import { useGatewayAdmin } from "../../hooks/useGatewayAdmin";
import {
  providerReadState,
  useModelProviders,
  useModelReadScope,
} from "../hooks/useCatalogRows";
import { modelPath } from "../modelLinks";
import { orgModelPageId } from "../orgServed";

const CENTRED = { display: "flex", alignItems: "center" };
const DEFAULT_COL_DEF = { cellStyle: CENTRED };
const providerId = (provider) => String(provider?.id || provider?.name || "");
const providerName = (provider) =>
  provider?.display_name || provider?.name || providerId(provider);
const listOf = (value) => (Array.isArray(value) ? value : []);

function ModelCell({ data }) {
  return (
    <Box sx={{ minWidth: 0, lineHeight: 1.4 }}>
      <Link
        component={RouterLink}
        href={modelPath(data.id)}
        state={{ gatewayModelOrigin: "your" }}
        typography="s1"
        underline="hover"
        noWrap
      >
        {data.name}
      </Link>
      <Typography typography="s2" color="text.secondary" title={data.id} noWrap>
        {data.id}
      </Typography>
    </Box>
  );
}
ModelCell.propTypes = { data: PropTypes.object.isRequired };

function StatusCell({ data }) {
  return (
    <Tooltip
      title={
        data.statusReason ||
        (data.pushed
          ? "The provider is configured. A successful request has not been verified."
          : "This provider has not been synced to the gateway. Open Providers to check its configuration.")
      }
    >
      <Box component="span" sx={{ display: "inline-flex" }}>
        <GatewayStatusChip
          tone={
            data.providerAccessState === "ready" && data.pushed
              ? "neutral"
              : "warning"
          }
          label={data.status}
        />
      </Box>
    </Tooltip>
  );
}
StatusCell.propTypes = { data: PropTypes.object.isRequired };

function ActionCell({ data }) {
  return (
    <Box
      role="group"
      aria-label={`Open ${data.id}`}
      sx={{ display: "flex", alignItems: "center", gap: 1.5 }}
    >
      {[
        ["overview", "Overview"],
        ["try", "Try it"],
        ["api", "API"],
      ].map(([tab, label]) => (
        <Link
          key={tab}
          component={RouterLink}
          href={modelPath(data.id, tab)}
          state={{ gatewayModelOrigin: "your" }}
          typography="s2"
          underline="hover"
        >
          {label}
        </Link>
      ))}
    </Box>
  );
}
ActionCell.propTypes = { data: PropTypes.object.isRequired };

const COLUMNS = [
  {
    field: "name",
    headerName: "Model",
    minWidth: 260,
    flex: 2,
    sort: "asc",
    cellRenderer: ModelCell,
  },
  { field: "providerName", headerName: "Provider", minWidth: 160, flex: 1 },
  {
    field: "status",
    headerName: "Gateway status",
    minWidth: 150,
    flex: 1,
    cellRenderer: StatusCell,
  },
  {
    colId: "actions",
    headerName: "Open model",
    minWidth: 180,
    flex: 1,
    sortable: false,
    cellRenderer: ActionCell,
  },
];

// An isolated own-provider index: never reads the managed catalog or a wallet,
// and never merges metadata-free models into a priced catalog row.
export default function OrgModelsPage({ catalogOffered = false }) {
  const navigate = useNavigate();
  const { gatewayId } = useGatewayContext();
  const { canEdit } = useGatewayAdmin();
  const scope = useModelReadScope(gatewayId);
  const providers = useModelProviders(gatewayId, scope);
  const providerAccessState = providerReadState(providers);
  const [search, setSearch] = useState("");
  const [provider, setProvider] = useState("");
  const providerList = providers.data?.providers;

  const options = useMemo(
    () =>
      listOf(providerList)
        .filter((item) => providerId(item))
        .map((item) => ({ id: providerId(item), name: providerName(item) }))
        .sort((a, b) => a.name.localeCompare(b.name)),
    [providerList],
  );
  const rows = useMemo(
    () =>
      listOf(providerList).flatMap((item) => {
        const id = providerId(item);
        return [...new Set(listOf(item?.models))].flatMap((model) => {
          // Use the same qualified route contract as provider drawers and detail.
          // Check only this provider: another provider's matching model is not its route.
          const pageId = orgModelPageId([item], id, model);
          if (!pageId) return [];
          return [
            {
              id: pageId,
              name: model,
              providerId: id,
              providerName: providerName(item),
              pushed: item.pushed !== false,
              providerAccessState,
              status:
                providerAccessState !== "ready"
                  ? "Access not verified"
                  : item.pushed === false
                    ? "Not ready"
                    : "Configured",
              statusReason:
                providerAccessState !== "ready"
                  ? "The provider read failed. These are previously loaded routes, not verified current access."
                  : item.pushed === false
                    ? String(item.status_reason || "")
                    : "",
            },
          ];
        });
      }),
    [providerList, providerAccessState],
  );
  const visible = useMemo(() => {
    const words = search.trim().toLowerCase().split(/\s+/).filter(Boolean);
    return rows.filter((row) => {
      const text = `${row.id} ${row.providerName}`.toLowerCase();
      return (
        (!provider || row.providerId === provider) &&
        words.every((word) => text.includes(word))
      );
    });
  }, [rows, search, provider]);
  const clearFilters = () => {
    setSearch("");
    setProvider("");
  };
  const failed = providerAccessState === "error";
  const loading = !gatewayId || providerAccessState === "pending";

  let body;
  if (failed && !providers.data) {
    body = (
      <GatewayEmptyState
        icon="providers"
        title="Could not load your models"
        description="The provider list could not be read. Retry to see your configured models."
        actionLabel="Retry"
        actionDisabled={providers.isFetching}
        onAction={() => providers.refetch()}
      />
    );
  } else if (loading) {
    body = (
      <Box role="status" aria-label="Loading your models" aria-live="polite">
        <Typography typography="s1" color="text.secondary">
          Loading your models…
        </Typography>
        <Skeleton variant="rounded" height={240} sx={{ mt: 1 }} />
      </Box>
    );
  } else if (!rows.length && !failed) {
    body = (
      <GatewayEmptyState
        icon="providers"
        title="No models configured"
        description={
          canEdit
            ? "Add a provider or configure models for an existing provider on the Providers page."
            : "Ask an Owner or Admin to add a provider or configure its models. You can view the current providers."
        }
        actionLabel={canEdit ? "Add provider" : "View providers"}
        onAction={() => navigate(paths.dashboard.gateway.providers)}
      />
    );
  } else {
    body = (
      <>
        <GatewayFilterBar
          search={search}
          onSearchChange={setSearch}
          searchPlaceholder="Search your models"
          actions={
            <Typography
              typography="s2"
              color="text.secondary"
              aria-live="polite"
            >
              {visible.length === rows.length
                ? `${rows.length} models`
                : `${visible.length} of ${rows.length} models`}
            </Typography>
          }
        >
          <TextField
            select
            size="small"
            label="Provider"
            value={provider}
            onChange={(event) => setProvider(event.target.value)}
            SelectProps={{ displayEmpty: true }}
            InputLabelProps={{ shrink: true }}
            sx={{
              minWidth: 200,
              maxWidth: "100%",
              flex: { xs: "1 1 100%", sm: "0 1 240px" },
            }}
          >
            <MenuItem value="">All providers</MenuItem>
            {options.map((option) => (
              <MenuItem key={option.id} value={option.id}>
                {option.name} ({option.id})
              </MenuItem>
            ))}
          </TextField>
        </GatewayFilterBar>
        <GatewayGrid
          rowData={visible}
          columnDefs={COLUMNS}
          defaultColDef={DEFAULT_COL_DEF}
          stateKey="gateway-org-models-v1"
          getRowId={(params) => params.data.id}
          rowHeight={60}
          onRowClicked={(event) => {
            if (event?.event?.target?.closest?.("a")) return;
            const path = modelPath(event?.data?.id);
            if (path) navigate(path, { state: { gatewayModelOrigin: "your" } });
          }}
          rowStyle={{ cursor: "pointer" }}
          emptyState={{
            variant: "no-results",
            title:
              failed && !rows.length
                ? "No saved models to show"
                : "No models match",
            description:
              failed && !rows.length
                ? "Retry the provider read to check your current models."
                : "Try another search or provider.",
            actionLabel: search || provider ? "Clear filters" : undefined,
            onAction: clearFilters,
          }}
        />
        <Typography typography="s2" color="text.secondary" component="p">
          Model type, pricing and context limits are not reported by this
          provider list. Configured does not verify that a request will succeed.
          Not ready means the provider has not been synced to the gateway.
        </Typography>
      </>
    );
  }

  return (
    <GatewayPageFrame
      title="Your models"
      description="Models configured through your own providers. Open a model to inspect its route, try a request or get API code."
      actions={
        <>
          {catalogOffered && (
            <Button component={RouterLink} href={paths.dashboard.gateway.models} variant="outlined" size="small">
              Catalog
            </Button>
          )}
          <Button
            component={RouterLink}
            href={paths.dashboard.gateway.providers}
            variant="outlined"
            size="small"
          >
            Providers
          </Button>
          <Button
            component={RouterLink}
            href={`${paths.dashboard.gateway.models}/auto`}
            variant="outlined"
            size="small"
          >
            Auto routing
          </Button>
        </>
      }
    >
      {failed && providers.data && (
        <Alert
          severity="warning"
          action={
            <Button
              color="inherit"
              size="small"
              disabled={providers.isFetching}
              onClick={() => providers.refetch()}
            >
              Retry provider access
            </Button>
          }
        >
          Provider access may be stale. Showing previously loaded routes; access
          is not verified.
        </Alert>
      )}
      {body}
    </GatewayPageFrame>
  );
}

OrgModelsPage.propTypes = { catalogOffered: PropTypes.bool };
