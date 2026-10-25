import React, { useEffect, useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Autocomplete,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  Grid,
  InputAdornment,
  Skeleton,
  Stack,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import { LoadingButton } from "@mui/lab";
import { enqueueSnackbar } from "notistack";
import Iconify from "src/components/iconify";
import PageErrorState from "../components/PageErrorState";
import { useAvailableModels } from "../hooks/useAvailableModels";
import {
  SavedButNotAppliedError,
  useGatewayConfig,
} from "../providers/hooks/useGatewayConfig";
import CodeSnippet from "./CodeSnippet";
import { formatPerMillion, hasListPrice, modelDisplayName } from "./catalog";
import {
  AUTO_PREFERENCES,
  autoPatch,
  capError,
  draftFromAuto,
  draftsEqual,
  orgPriceOf,
} from "./autoRouting";
import { saveErrorMessage, useSaveAutoRouting } from "./hooks/useManagedModels";
import { curlSnippet, pythonSnippet } from "./snippets";

const OWN_GROUP = "Your providers";
const MANAGED_GROUP = "Managed catalog";
const MISSING_GROUP = "Not offered by any provider";

// Each line states what the gateway does: routing/auto.go ChooseAuto,
// server/auto_route.go (routeAuto, recordAutoDecision) and
// server/auto_pool.go modelInfoFor. Change them together.
const HOW_IT_WORKS = [
  {
    icon: "mdi:filter-outline",
    text: "Models that cannot serve the request are set aside first: missing tools, structured output or image support, a capability the request's require header asks for, a context or output limit too small, a price above your caps, or a provider whose circuit is open.",
  },
  {
    icon: "mdi:currency-usd",
    text: "A model on your own provider is priced from your Cost Tracking price, else your Model Database price, else the gateway's model database. A model none of them prices is set aside as having no price data: give self-hosted or renamed models a price in Org Config. Managed catalog models always use the catalog price.",
  },
  {
    icon: "mdi:stairs",
    text: "The gateway scores the prompt as a simple, standard or complex task. Auto ranks the models your providers serve by how well their price tier fits the task and your preference.",
  },
  {
    icon: "mdi:swap-horizontal",
    text: "The next two models in the ranking become fallbacks if the first one fails.",
  },
  {
    icon: "mdi:text-box-search-outline",
    text: "Each request log records the chosen model, the task tier and the top of the ranking. The response header x-agentcc-auto-model names the model auto chose.",
  },
];

// "$0.05 in / $0.20 out per 1M"; a field the org left blank is the model
// database's.
const orgPriceText = ({ input, output }) => {
  const part = (v, side) =>
    v == null ? `model database ${side}` : `${formatPerMillion(v)} ${side}`;
  return `${part(input, "in")} / ${part(output, "out")} per 1M`;
};

const CardTitle = ({ title, subtitle }) => (
  <Box mb={2}>
    <Typography variant="subtitle1">{title}</Typography>
    {subtitle && (
      <Typography variant="body2" color="text.secondary">
        {subtitle}
      </Typography>
    )}
  </Box>
);

CardTitle.propTypes = {
  title: PropTypes.string.isRequired,
  subtitle: PropTypes.string,
};

const AutoRoutingTab = ({
  gatewayId,
  canWrite,
  catalogModels,
  managedStatus,
  baseUrl,
}) => {
  const {
    data: config,
    isLoading,
    error,
    refetch,
  } = useGatewayConfig(gatewayId);
  const ownModels = useAvailableModels();
  const saveMutation = useSaveAutoRouting();

  // The patch's nulls are only safe for keys the saved config has (see
  // autoPatch), so a save waits for a successful read.
  const savedAuto = config?.routing?.auto;
  const saved = useMemo(() => draftFromAuto(savedAuto), [savedAuto]);
  const [draft, setDraft] = useState(saved);

  // Start over from the saved config when its content changes (a save, or an
  // edit elsewhere). Background refetches return equal content and leave the
  // draft alone.
  const lastSaved = useRef(saved);
  useEffect(() => {
    if (draftsEqual(lastSaved.current, saved)) return;
    lastSaved.current = saved;
    setDraft(saved);
  }, [saved]);

  const catalogById = useMemo(() => {
    const map = new Map();
    (catalogModels || []).forEach((m) => map.set(m.id, m));
    return map;
  }, [catalogModels]);

  const ownSet = useMemo(() => new Set(ownModels), [ownModels]);
  const options = useMemo(() => {
    const managed = [...catalogById.keys()]
      .filter((id) => !ownSet.has(id))
      .sort();
    // A saved candidate no provider lists any more stays visible (and
    // removable) rather than vanishing from the field.
    const missing = saved.candidates.filter(
      (id) => !ownSet.has(id) && !catalogById.has(id),
    );
    return [...ownModels, ...managed, ...missing];
  }, [ownModels, ownSet, catalogById, saved]);

  const groupOf = (id) => {
    if (ownSet.has(id)) return OWN_GROUP;
    return catalogById.has(id) ? MANAGED_GROUP : MISSING_GROUP;
  };

  const inputError = capError(draft.maxInput);
  const outputError = capError(draft.maxOutput);
  const dirty = !draftsEqual(draft, saved);
  const managedOffered = Boolean(managedStatus?.available);
  const managedOff = managedOffered && !managedStatus?.enabled;

  const update = (field, value) =>
    setDraft((prev) => ({ ...prev, [field]: value }));

  const handleSave = () => {
    saveMutation.mutate(
      { gatewayId, auto: autoPatch(draft, savedAuto) },
      {
        onSuccess: (result) => {
          // A change Django saved but the gateway did not apply is
          // reported as that, never with the success toast.
          if (result?.gateway_synced === false) {
            enqueueSnackbar(new SavedButNotAppliedError(result).message, {
              variant: "warning",
            });
            return;
          }
          enqueueSnackbar("Auto routing saved", { variant: "success" });
        },
        onError: (err) =>
          enqueueSnackbar(
            saveErrorMessage(err, "Failed to save auto routing"),
            { variant: "error" },
          ),
      },
    );
  };

  const snippets = useMemo(
    () => [
      { key: "curl", label: "cURL", code: curlSnippet(baseUrl, "auto") },
      { key: "python", label: "Python", code: pythonSnippet(baseUrl, "auto") },
    ],
    [baseUrl],
  );

  if (isLoading) {
    return (
      <Grid container spacing={3}>
        <Grid item xs={12} md={7}>
          <Stack spacing={2}>
            <Skeleton variant="rounded" height={180} />
            <Skeleton variant="rounded" height={140} />
          </Stack>
        </Grid>
        <Grid item xs={12} md={5}>
          <Skeleton variant="rounded" height={340} />
        </Grid>
      </Grid>
    );
  }

  if (error) {
    return (
      <PageErrorState
        message={`Couldn't load the routing config: ${error.message || "unknown error"}`}
        onRetry={refetch}
      />
    );
  }

  const disabled = !canWrite || !config || saveMutation.isPending;

  return (
    <Grid container spacing={3}>
      <Grid item xs={12} md={7}>
        <Stack spacing={2}>
          <Card variant="outlined">
            <CardContent>
              <CardTitle
                title="Preference"
                subtitle={
                  'How model "auto" trades price against capability. A request can override it with the x-agentcc-auto-preference header, or with its route-hint (cost, balanced or quality) when it sends no such header.'
                }
              />
              <ToggleButtonGroup
                value={draft.preference}
                exclusive
                fullWidth
                disabled={disabled}
                aria-label="Auto routing preference"
                onChange={(_, value) => value && update("preference", value)}
                sx={{
                  flexDirection: { xs: "column", sm: "row" },
                  // Separate cards, not a joined segmented bar: undo the
                  // group's shared borders and corner rounding.
                  "& .MuiToggleButtonGroup-grouped": {
                    mx: 0,
                    border: (theme) =>
                      `1px solid ${theme.palette.divider} !important`,
                    borderRadius: "8px !important",
                  },
                  "& .MuiToggleButtonGroup-grouped.Mui-selected": {
                    borderColor: (theme) =>
                      `${theme.palette.primary.main} !important`,
                  },
                  gap: 1,
                }}
              >
                {AUTO_PREFERENCES.map((p) => (
                  <ToggleButton
                    key={p.value}
                    value={p.value}
                    sx={{
                      flex: 1,
                      p: 1.5,
                      textTransform: "none",
                      textAlign: "left",
                      alignItems: "flex-start",
                      justifyContent: "flex-start",
                      "&.Mui-selected, &.Mui-selected:hover": {
                        bgcolor: "primary.lighter",
                      },
                    }}
                  >
                    <Box>
                      <Typography
                        variant="subtitle2"
                        color={
                          draft.preference === p.value
                            ? "primary.main"
                            : "text.primary"
                        }
                      >
                        {p.label}
                      </Typography>
                      <Typography
                        variant="caption"
                        color="text.secondary"
                        component="div"
                      >
                        {p.description}
                      </Typography>
                    </Box>
                  </ToggleButton>
                ))}
              </ToggleButtonGroup>
            </CardContent>
          </Card>

          <Card variant="outlined">
            <CardContent>
              <CardTitle
                title="Candidate models"
                subtitle={
                  managedOffered
                    ? "Restrict auto to these models. Leave empty for the default pool: your providers' models, plus current models from major vendors in the managed catalog while managed models are on."
                    : "Restrict auto to these models. Leave empty for the default pool: your providers' models."
                }
              />
              <Autocomplete
                multiple
                disableCloseOnSelect
                size="small"
                options={options}
                value={draft.candidates}
                onChange={(_, value) => update("candidates", value)}
                groupBy={groupOf}
                disabled={disabled}
                renderOption={(props, id) => {
                  const m = catalogById.get(id);
                  const own = ownSet.has(id);
                  const orgPrice = own ? orgPriceOf(config, id) : null;
                  return (
                    <li {...props} key={id}>
                      <Box sx={{ minWidth: 0 }}>
                        <Typography
                          variant="body2"
                          sx={{ fontFamily: "monospace" }}
                          noWrap
                        >
                          {id}
                        </Typography>
                        {orgPrice && (
                          <Typography
                            variant="caption"
                            color="text.secondary"
                            component="div"
                          >
                            {`Your price (${orgPrice.source}): ${orgPriceText(orgPrice)}`}
                          </Typography>
                        )}
                        {!orgPrice && m && (
                          <Typography variant="caption" color="text.secondary">
                            {modelDisplayName(m)}
                            {hasListPrice(m)
                              ? ` · ${formatPerMillion(m.billed_input_per_mtok)} in / ${formatPerMillion(m.billed_output_per_mtok)} out per 1M`
                              : ""}
                          </Typography>
                        )}
                        {own && !orgPrice && (
                          <Typography
                            variant="caption"
                            color="text.secondary"
                            component="div"
                          >
                            No price from your org: auto skips it unless the
                            gateway&apos;s model database prices this model
                          </Typography>
                        )}
                      </Box>
                    </li>
                  );
                }}
                renderTags={(value, getTagProps) =>
                  value.map((id, index) => {
                    const { key, ...tagProps } = getTagProps({ index });
                    return (
                      <Chip
                        key={key}
                        size="small"
                        variant="outlined"
                        label={id}
                        sx={{ fontFamily: "monospace", fontSize: "0.75rem" }}
                        {...tagProps}
                      />
                    );
                  })
                }
                renderInput={(params) => (
                  <TextField
                    {...params}
                    placeholder={
                      draft.candidates.length ? "" : "All eligible models"
                    }
                    inputProps={{
                      ...params.inputProps,
                      "aria-label": "Candidate models",
                    }}
                  />
                )}
              />
              {managedOff && draft.candidates.some((id) => !ownSet.has(id)) && (
                <Alert severity="info" sx={{ mt: 1.5 }}>
                  Managed catalog models are only used while managed models are
                  on.
                </Alert>
              )}
            </CardContent>
          </Card>

          <Card variant="outlined">
            <CardContent>
              <CardTitle
                title="Price caps"
                subtitle="Skip models whose provider list price per 1M tokens is above these amounts. Leave blank for no cap."
              />
              <Stack direction={{ xs: "column", sm: "row" }} spacing={2}>
                <TextField
                  label="Max input price"
                  size="small"
                  fullWidth
                  value={draft.maxInput}
                  onChange={(e) => update("maxInput", e.target.value)}
                  disabled={disabled}
                  error={Boolean(inputError)}
                  helperText={inputError || "Per 1M input tokens"}
                  inputProps={{ inputMode: "decimal" }}
                  InputProps={{
                    startAdornment: (
                      <InputAdornment position="start">$</InputAdornment>
                    ),
                  }}
                />
                <TextField
                  label="Max output price"
                  size="small"
                  fullWidth
                  value={draft.maxOutput}
                  onChange={(e) => update("maxOutput", e.target.value)}
                  disabled={disabled}
                  error={Boolean(outputError)}
                  helperText={outputError || "Per 1M output tokens"}
                  inputProps={{ inputMode: "decimal" }}
                  InputProps={{
                    startAdornment: (
                      <InputAdornment position="start">$</InputAdornment>
                    ),
                  }}
                />
              </Stack>
            </CardContent>
          </Card>

          <Stack
            direction="row"
            spacing={1.5}
            justifyContent="flex-end"
            alignItems="center"
          >
            {!canWrite && (
              <Typography
                variant="caption"
                color="text.secondary"
                sx={{ mr: "auto" }}
              >
                You need edit access to change auto routing.
              </Typography>
            )}
            {canWrite && dirty && (
              <Typography
                variant="caption"
                color="text.secondary"
                sx={{ mr: "auto" }}
              >
                Unsaved changes
              </Typography>
            )}
            <Button
              variant="outlined"
              color="inherit"
              size="small"
              disabled={!dirty || saveMutation.isPending}
              onClick={() => setDraft(saved)}
            >
              Discard
            </Button>
            <LoadingButton
              variant="contained"
              size="small"
              loading={saveMutation.isPending}
              disabled={
                disabled || !dirty || Boolean(inputError || outputError)
              }
              onClick={handleSave}
              startIcon={<Iconify icon="mdi:content-save-outline" width={18} />}
            >
              Save changes
            </LoadingButton>
          </Stack>
        </Stack>
      </Grid>

      <Grid item xs={12} md={5}>
        <Stack spacing={2}>
          <Card variant="outlined">
            <CardContent>
              <CardTitle
                title="How auto works"
                subtitle={
                  'Send model "auto" and the gateway chooses per request.'
                }
              />
              <Stack spacing={1.5}>
                {HOW_IT_WORKS.map((item) => (
                  <Stack
                    key={item.icon}
                    direction="row"
                    spacing={1.25}
                    alignItems="flex-start"
                  >
                    <Iconify
                      icon={item.icon}
                      width={18}
                      sx={{ color: "primary.main", mt: 0.25, flexShrink: 0 }}
                    />
                    <Typography variant="body2" color="text.secondary">
                      {item.text}
                    </Typography>
                  </Stack>
                ))}
              </Stack>
            </CardContent>
          </Card>
          <Card variant="outlined">
            <CardContent>
              <CardTitle title="Try it" />
              <CodeSnippet
                snippets={snippets}
                ariaLabel="Auto example language"
              />
            </CardContent>
          </Card>
        </Stack>
      </Grid>
    </Grid>
  );
};

AutoRoutingTab.propTypes = {
  gatewayId: PropTypes.string,
  canWrite: PropTypes.bool,
  catalogModels: PropTypes.array,
  managedStatus: PropTypes.object,
  baseUrl: PropTypes.string,
};

export default AutoRoutingTab;
