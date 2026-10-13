import { useEffect, useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  Grid,
  List,
  ListItem,
  ListItemText,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import ConnectorPanel from "./ConnectorPanel";
import {
  coverageRows,
  filterIntegrations,
  integrationFamily,
  integrationState,
  INTEGRATION_COVERAGE,
} from "./integrationCatalog";

const rows = (result) =>
  result?.available === true && Array.isArray(result.connectors)
    ? result.connectors
    : [];
const label = (row) =>
  row?.name || row?.label || row?.id || row?.connectorId || "Integration";
const stateLabel = (state) =>
  state === "connected"
    ? "Connected"
    : state === "available"
      ? "Available"
      : "Not available on this deployment";
const requestKey = () =>
  `squad-mcp-${globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`}`;

export default function IntegrationDiscoveryPanel({
  result,
  client,
  onRefreshWorkforce,
}) {
  const connectors = rows(result);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("all");
  const [selectedId, setSelectedId] = useState("");
  const [customResult, setCustomResult] = useState(null);
  const [customReview, setCustomReview] = useState(null);
  const [registrationOpen, setRegistrationOpen] = useState(false);
  const [registration, setRegistration] = useState({
    id: "",
    origin: "",
    path: "/mcp",
    credentialId: "",
  });
  const [registrationMessage, setRegistrationMessage] = useState("");
  const [registrationBusy, setRegistrationBusy] = useState(false);
  const customRows = useMemo(
    () =>
      customResult?.available === true && Array.isArray(customResult.connectors)
        ? customResult.connectors.map((row) => ({
            ...row,
            provider: "MCP",
            name: row.name || `Custom MCP · ${row.id}`,
            status: row.status === "active" ? "available" : row.status,
          }))
        : [],
    [customResult],
  );
  const allConnectors = useMemo(
    () => [...connectors, ...customRows],
    [connectors, customRows],
  );
  const visibleRows = useMemo(
    () => filterIntegrations(allConnectors, { query, filter }),
    [allConnectors, query, filter],
  );
  const matrix = useMemo(() => coverageRows(allConnectors), [allConnectors]);
  const selected =
    visibleRows.find((row) => (row.id || row.connectorId) === selectedId) ||
    null;
  const families = useMemo(
    () => [...new Set(INTEGRATION_COVERAGE.map((row) => row.family))],
    [],
  );

  useEffect(() => {
    setSelectedId((current) =>
      visibleRows.some((row) => (row.id || row.connectorId) === current)
        ? current
        : "",
    );
  }, [visibleRows]);

  useEffect(() => {
    let active = true;
    if (typeof client.listCustomConnectors !== "function") return undefined;
    client.listCustomConnectors().then((result) => {
      if (active) setCustomResult(result);
    });
    return () => {
      active = false;
    };
  }, [client]);

  useEffect(() => {
    let active = true;
    setCustomReview(null);
    if (
      selected?.kind === "remote_mcp" &&
      typeof client.getCustomConnectorReview === "function"
    ) {
      client
        .getCustomConnectorReview(selected.id || selected.connectorId)
        .then((result) => {
          if (active) setCustomReview(result);
        });
    }
    return () => {
      active = false;
    };
  }, [client, selected]);

  const registerCustomMcp = async (event) => {
    event.preventDefault();
    setRegistrationMessage("");
    let url;
    try {
      url = new URL(registration.origin);
    } catch {
      setRegistrationMessage("Use a valid HTTPS MCP origin.");
      return;
    }
    if (
      url.protocol !== "https:" ||
      url.username ||
      url.password ||
      !registration.id.trim() ||
      !registration.path.startsWith("/")
    ) {
      setRegistrationMessage(
        "Custom MCP requires an id, an HTTPS origin, and an absolute path without embedded credentials.",
      );
      return;
    }
    setRegistrationBusy(true);
    const result = await client.createCustomConnector(
      {
        id: registration.id.trim(),
        kind: "remote_mcp",
        origin: url.origin,
        path: registration.path.trim(),
        ...(registration.credentialId.trim()
          ? { credentialId: registration.credentialId.trim() }
          : {}),
      },
      requestKey(),
    );
    setRegistrationBusy(false);
    if (!result?.available) {
      setRegistrationMessage(
        result?.reason ||
          "Custom MCP registration is unavailable on this deployment.",
      );
      return;
    }
    setRegistrationMessage(
      "Custom MCP registration was accepted for server review. Runtime availability is still reported by EE.",
    );
    setRegistrationOpen(false);
    if (typeof client.listCustomConnectors === "function")
      setCustomResult(await client.listCustomConnectors());
  };

  if (result?.available === false)
    return (
      <Alert severity="info">
        {result.reason ||
          "The integration catalog is unavailable on this deployment."}
      </Alert>
    );

  return (
    <Stack spacing={2} component="section" aria-label="Integration catalog">
      <Card variant="outlined">
        <CardContent>
          <Stack spacing={1.5}>
            <Box>
              <Typography variant="h6">Integrations</Typography>
              <Typography variant="body2" color="text.secondary">
                Search the server-owned catalog, inspect connected status, and
                open an integration to use its existing authorization flow.
                Missing providers stay unavailable; Squad never invents a
                Connect action.
              </Typography>
            </Box>
            <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5}>
              <TextField
                fullWidth
                size="small"
                label="Search integrations"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
              />
              <TextField
                select
                size="small"
                label="Category"
                value={filter}
                onChange={(event) => setFilter(event.target.value)}
                sx={{ minWidth: 210 }}
              >
                <MenuItem value="all">All integrations</MenuItem>
                <MenuItem value="connected">Connected</MenuItem>
                {families.map((family) => (
                  <MenuItem key={family} value={family}>
                    {family}
                  </MenuItem>
                ))}
              </TextField>
            </Stack>
            <Stack direction="row" spacing={1} alignItems="center">
              {typeof client.createCustomConnector === "function" ? (
                <Button
                  size="small"
                  variant="outlined"
                  onClick={() => setRegistrationOpen((current) => !current)}
                >
                  Register custom MCP
                </Button>
              ) : null}
              {registrationMessage ? (
                <Typography variant="caption" color="text.secondary">
                  {registrationMessage}
                </Typography>
              ) : null}
            </Stack>
            {registrationOpen ? (
              <Box
                component="form"
                onSubmit={registerCustomMcp}
                sx={{
                  display: "grid",
                  gap: 1,
                  gridTemplateColumns: { xs: "1fr", sm: "1fr 1fr" },
                }}
                aria-label="Register custom MCP"
              >
                <TextField
                  required
                  size="small"
                  label="MCP id"
                  value={registration.id}
                  onChange={(event) =>
                    setRegistration((current) => ({
                      ...current,
                      id: event.target.value,
                    }))
                  }
                />
                <TextField
                  required
                  size="small"
                  label="HTTPS origin"
                  value={registration.origin}
                  onChange={(event) =>
                    setRegistration((current) => ({
                      ...current,
                      origin: event.target.value,
                    }))
                  }
                  placeholder="https://mcp.example.com"
                />
                <TextField
                  required
                  size="small"
                  label="MCP path"
                  value={registration.path}
                  onChange={(event) =>
                    setRegistration((current) => ({
                      ...current,
                      path: event.target.value,
                    }))
                  }
                />
                <TextField
                  size="small"
                  label="Credential id (optional)"
                  value={registration.credentialId}
                  onChange={(event) =>
                    setRegistration((current) => ({
                      ...current,
                      credentialId: event.target.value,
                    }))
                  }
                />
                <Button
                  type="submit"
                  variant="contained"
                  disabled={registrationBusy}
                >
                  {registrationBusy
                    ? "Registering…"
                    : "Submit for server review"}
                </Button>
              </Box>
            ) : null}
            {visibleRows.length === 0 ? (
              <Typography color="text.secondary">
                No server catalog rows match this search.
              </Typography>
            ) : (
              <List
                dense
                disablePadding
                aria-label="Available server integrations"
              >
                {visibleRows.map((row) => {
                  const id = row.id || row.connectorId;
                  const state = integrationState(row);
                  return (
                    <ListItem
                      key={id}
                      divider
                      selected={id === selectedId}
                      secondaryAction={
                        state === "unavailable" ? (
                          <Chip
                            size="small"
                            color="warning"
                            variant="outlined"
                            label={stateLabel(state)}
                          />
                        ) : (
                          <Button
                            size="small"
                            onClick={() => setSelectedId(id)}
                          >
                            {id === selectedId ? "Selected" : "Open details"}
                          </Button>
                        )
                      }
                    >
                      <ListItemText
                        primary={label(row)}
                        secondary={`${integrationFamily(row)} · ${stateLabel(state)}`}
                      />
                    </ListItem>
                  );
                })}
              </List>
            )}
          </Stack>
        </CardContent>
      </Card>
      <Card variant="outlined">
        <CardContent>
          <Typography variant="subtitle1">Provider coverage</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
            This matrix is a truthful projection of the current server catalog.
            “Not available” means no matching server row was returned; it does
            not promise future support.
          </Typography>
          <Grid container spacing={1} sx={{ mt: 0.5 }}>
            {matrix.map((item) => (
              <Grid item xs={12} sm={6} md={4} key={item.id}>
                <Stack
                  direction="row"
                  justifyContent="space-between"
                  alignItems="center"
                  sx={{
                    border: 1,
                    borderColor: "divider",
                    borderRadius: 1,
                    p: 1,
                  }}
                >
                  <Box>
                    <Typography variant="body2">{item.label}</Typography>
                    <Typography variant="caption" color="text.secondary">
                      {item.family}
                    </Typography>
                  </Box>
                  <Chip
                    size="small"
                    color={
                      item.state === "connected"
                        ? "success"
                        : item.state === "available"
                          ? "info"
                          : "default"
                    }
                    variant="outlined"
                    label={stateLabel(item.state)}
                  />
                </Stack>
              </Grid>
            ))}
          </Grid>
        </CardContent>
      </Card>
      {selected?.kind === "remote_mcp" ? (
        <Card variant="outlined">
          <CardContent>
            <Typography variant="subtitle1">Custom MCP review</Typography>
            <Typography variant="body2" color="text.secondary">
              {selected.origin}
              {selected.path}
            </Typography>
            <Alert
              severity={
                customReview?.review?.runtime?.available ? "success" : "warning"
              }
              sx={{ mt: 1 }}
            >
              {customReview?.review?.runtime?.available
                ? "The tenant MCP runtime is available."
                : customReview?.review?.runtime?.reason ||
                  "The tenant MCP broker is unavailable; no remote tool call can be made."}
            </Alert>
          </CardContent>
        </Card>
      ) : selected ? (
        <ConnectorPanel
          key={selected.id || selected.connectorId}
          result={{ available: true, connectors: [selected] }}
          client={client}
          onRefreshWorkforce={onRefreshWorkforce}
        />
      ) : (
        <Alert severity="info">
          Select an available catalog row to inspect metadata, OAuth
          authorization, sources, syncs, or agent attachment.
        </Alert>
      )}
    </Stack>
  );
}

IntegrationDiscoveryPanel.propTypes = {
  result: PropTypes.object,
  client: PropTypes.object.isRequired,
  onRefreshWorkforce: PropTypes.func,
};
