import React, { useEffect, useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Autocomplete,
  Box,
  Button,
  Chip,
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
import { GatewayEmptyState } from "../components/frame";
import { useAvailableModels } from "../hooks/useAvailableModels";
import {
  SavedButNotAppliedError,
  useGatewayConfig,
} from "../providers/hooks/useGatewayConfig";
import { formatPerMillion, hasListPrice, modelDisplayName } from "./catalog";
import { managedVerdict } from "./catalogRows";
import { Section } from "./detail/Section";
import {
  AUTO_PREFERENCES,
  autoPatch,
  capError,
  draftFromAuto,
  draftsEqual,
  orgPriceOf,
} from "./autoRouting";
import { saveErrorMessage, useSaveAutoRouting } from "./hooks/useManagedModels";
import { GATEWAY_MONO } from "../utils/fonts";

const OWN_GROUP = "Your providers";
const MANAGED_GROUP = "Future AGI catalogue";
const MISSING_GROUP = "Not offered by any provider";

// "$0.05 in / $0.20 out per 1M"; a field the org left blank is the model
// database's.
const orgPriceText = ({ input, output }) => {
  const part = (v, side) =>
    v == null ? `model database ${side}` : `${formatPerMillion(v)} ${side}`;
  return `${part(input, "in")} / ${part(output, "out")} per 1M`;
};

const AutoRoutingTab = ({
  gatewayId,
  canWrite,
  catalogModels,
  managedStatus,
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
    // Auto never picks a catalog model managed credits cannot buy, nor one
    // priced per request: auto prices a managed candidate from its catalog
    // price card, and sets aside a model that has none as having no price
    // data (server/auto_pool.go baseModelInfo).
    (catalogModels || [])
      .filter((m) => managedVerdict(m).sold && !m.dynamic_price)
      .forEach((m) => map.set(m.id, m));
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
          // A change Django saved but the gateway did not apply is reported
          // as that, never with the success toast.
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

  if (isLoading) {
    return (
      <Stack spacing={1.5}>
        <Skeleton variant="rounded" height={150} />
        <Skeleton variant="rounded" height={120} />
        <Skeleton variant="rounded" height={120} />
      </Stack>
    );
  }

  if (error) {
    return (
      <GatewayEmptyState
        icon="routing"
        title="Couldn't load the routing settings"
        description={error.message || "The gateway did not answer."}
        actionLabel="Retry"
        onAction={() => refetch()}
      />
    );
  }

  const disabled = !canWrite || !config || saveMutation.isPending;

  return (
    <Stack spacing={1.5}>
      <Section
        title="Preference"
        note='How model "auto" trades price against capability. A request can override it with the x-agentcc-auto-preference header, or with its route-hint (cost, balanced or quality) when it sends no such header.'
      >
        <ToggleButtonGroup
          value={draft.preference}
          exclusive
          fullWidth
          disabled={disabled}
          aria-label="Auto routing preference"
          onChange={(_, value) => value && update("preference", value)}
          sx={{
            flexDirection: { xs: "column", sm: "row" },
            // Separate tiles, not a joined segmented bar: undo the group's
            // shared borders and corner rounding.
            "& .MuiToggleButtonGroup-grouped": {
              mx: 0,
              border: (theme) =>
                `1px solid ${theme.palette.divider} !important`,
              borderRadius: (theme) =>
                `${theme.shape.borderRadius}px !important`,
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
                // action.selected is the theme's own selected tint, readable
                // in light and dark (primary.lighter is near-white in dark).
                "&.Mui-selected, &.Mui-selected:hover": {
                  bgcolor: "action.selected",
                },
              }}
            >
              <Box>
                <Typography
                  typography="s1"
                  fontWeight="fontWeightMedium"
                  color={
                    draft.preference === p.value
                      ? "primary.main"
                      : "text.primary"
                  }
                >
                  {p.label}
                </Typography>
                <Typography
                  typography="s2"
                  color="text.secondary"
                  component="div"
                >
                  {p.description}
                </Typography>
              </Box>
            </ToggleButton>
          ))}
        </ToggleButtonGroup>
      </Section>

      <Section
        title="Candidate pool"
        note={
          managedOffered
            ? "Restrict auto to these models. Leave empty for the default pool: your providers' models, plus current models from major vendors in the Future AGI catalogue while Future AGI credits are on."
            : "Restrict auto to these models. Leave empty for the default pool: your providers' models."
        }
      >
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
            // A model on your own provider is priced as the gateway prices
            // it: your Cost Tracking price, else your Model Database price,
            // else the gateway's model database (autoRouting.orgPriceOf).
            const orgPrice = own ? orgPriceOf(config, id) : null;
            return (
              <li {...props} key={id}>
                <Box sx={{ minWidth: 0 }}>
                  <Typography
                    typography="s1"
                    sx={{ fontFamily: GATEWAY_MONO }}
                    noWrap
                  >
                    {id}
                  </Typography>
                  {orgPrice && (
                    <Typography
                      typography="s2"
                      color="text.secondary"
                      component="div"
                    >
                      {`Your price (${orgPrice.source}): ${orgPriceText(orgPrice)}`}
                    </Typography>
                  )}
                  {!orgPrice && m && (
                    <Typography
                      typography="s2"
                      color="text.secondary"
                      component="div"
                    >
                      {modelDisplayName(m)}
                      {hasListPrice(m)
                        ? ` · ${formatPerMillion(m.billed_input_per_mtok)} in / ${formatPerMillion(m.billed_output_per_mtok)} out per 1M`
                        : ""}
                    </Typography>
                  )}
                  {own && !orgPrice && (
                    <Typography
                      typography="s2"
                      color="text.secondary"
                      component="div"
                    >
                      No price from your org: auto skips it unless the
                      gateway&apos;s model database prices this model
                    </Typography>
                  )}
                  {!own && !m && (
                    <Typography
                      typography="s2"
                      color="text.secondary"
                      component="div"
                    >
                      No provider lists it any more
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
                  sx={{ fontFamily: GATEWAY_MONO }}
                  {...tagProps}
                />
              );
            })
          }
          renderInput={(params) => (
            <TextField
              {...params}
              placeholder={draft.candidates.length ? "" : "All eligible models"}
              inputProps={{
                ...params.inputProps,
                "aria-label": "Candidate models",
              }}
            />
          )}
        />
        {managedOff && draft.candidates.some((id) => !ownSet.has(id)) && (
          <Alert severity="info">
            Future AGI catalogue models are only used while Future AGI credits are on.
          </Alert>
        )}
      </Section>

      <Section
        title="Price caps"
        note="Skip models whose provider list price per 1M tokens is above these amounts. Leave blank for no cap."
      >
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
      </Section>

      <Stack
        direction="row"
        spacing={1.5}
        justifyContent="flex-end"
        alignItems="center"
      >
        {!canWrite && (
          <Typography
            typography="s2"
            color="text.secondary"
            sx={{ mr: "auto" }}
          >
            You need edit access to change auto routing.
          </Typography>
        )}
        {canWrite && dirty && (
          <Typography
            typography="s2"
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
          color="primary"
          size="small"
          loading={saveMutation.isPending}
          disabled={disabled || !dirty || Boolean(inputError || outputError)}
          onClick={handleSave}
        >
          Save changes
        </LoadingButton>
      </Stack>
    </Stack>
  );
};

AutoRoutingTab.propTypes = {
  gatewayId: PropTypes.string,
  canWrite: PropTypes.bool,
  catalogModels: PropTypes.array,
  managedStatus: PropTypes.object,
};

export default AutoRoutingTab;
