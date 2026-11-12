import React, { useEffect, useId, useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Autocomplete,
  Box,
  Button,
  Chip,
  Divider,
  InputAdornment,
  Radio,
  Skeleton,
  Stack,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import { LoadingButton } from "@mui/lab";
import { useQuery } from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";
import { GatewayEmptyState } from "../components/frame";
import { useGatewayConfig, useProviderHealth } from "../providers/hooks/useGatewayConfig";
import { formatPerMillion, hasListPrice, modelDisplayName } from "./catalog";
import { managedVerdict } from "./catalogRows";
import {
  AUTO_PREFERENCES,
  autoPatch,
  capError,
  draftFromAuto,
  draftsEqual,
  orgPriceOf,
} from "./autoRouting";
import { autoApplyReceiptKey, autoApplyWarning, saveErrorMessage, useSaveAutoRouting } from "./hooks/useManagedModels";
import { GATEWAY_MONO } from "../utils/fonts";
import { useModelReadScope } from "./hooks/useCatalogRows";
import { AutoNote, AutoSection } from "./auto/AutoSection";

const OWN_GROUP = "Your providers";
const MANAGED_GROUP = "Future AGI catalogue";
const MISSING_GROUP = "Not offered by any provider";

// The Preference tiles are cards (a radio mark, a title and a sentence), not
// the theme's 24 px segmented option that never wraps (theme/overrides/
// components/toggle-button.js): each tile grows to fit its wrapped text, and
// the three stay the same height.
const PREFERENCE_TILE_FIT = {
  height: "auto",
  whiteSpace: "normal",
  lineHeight: 1.4,
  alignSelf: "stretch",
};

// The group is three equal tiles in a row (stacked on a phone), without the
// segmented control's track. Each tile has its own 1 px border; the selected
// one a primary border on the theme's selected tint, its title unchanged.
// The group's shared borders and corner rounding are undone with !important,
// as the theme's grouped-button rules are more specific than these.
const PREFERENCE_GROUP_SX = {
  display: "grid",
  gridTemplateColumns: {
    xs: "minmax(0, 1fr)",
    sm: "repeat(3, minmax(0, 1fr))",
  },
  gap: 1.5,
  p: 0,
  bgcolor: "transparent",
  borderRadius: 0,
  "& .MuiToggleButtonGroup-grouped": {
    m: 0,
    border: (theme) => `1px solid ${theme.palette.divider} !important`,
    borderRadius: (theme) => `${theme.shape.borderRadius}px !important`,
  },
  "& .MuiToggleButtonGroup-grouped.Mui-selected": {
    borderColor: (theme) => `${theme.palette.primary.main} !important`,
  },
};

const PREFERENCE_TILE_SX = {
  ...PREFERENCE_TILE_FIT,
  display: "flex",
  alignItems: "flex-start",
  justifyContent: "flex-start",
  gap: 1,
  p: 1.5,
  textAlign: "left",
  textTransform: "none",
  "&:hover": { bgcolor: "action.hover" },
  // action.selected is the theme's own selected tint, readable in light and
  // dark (primary.lighter is near-white in dark).
  "&.Mui-selected, &.Mui-selected:hover": {
    bgcolor: "action.selected",
    boxShadow: "none",
  },
};

// The tile itself is the control: its radio is the themed Radio as a mark
// only, out of the tab order and hidden from assistive tech, so the tile is
// named by its label and clicks land on the tile.
const TILE_MARK_SX = { display: "flex", flexShrink: 0, pointerEvents: "none" };

// The save row closes the card. While there are unsaved changes it sticks to
// the bottom of the scrolling page, inside the card, so Save stays in reach.
const FOOTER_SX = {
  display: "flex",
  flexWrap: "wrap",
  alignItems: "center",
  justifyContent: "flex-end",
  gap: 1.5,
  px: 2,
  py: 1.5,
  borderTop: 1,
  borderColor: "divider",
  bgcolor: "background.paper",
  borderBottomLeftRadius: "inherit",
  borderBottomRightRadius: "inherit",
};
const FOOTER_STUCK_SX = {
  ...FOOTER_SX,
  position: "sticky",
  bottom: 0,
  zIndex: 2,
};

const POLICY_TITLE = "Routing policy";

// One part of the Routing policy card: a 12 px medium title and its note,
// then the part's fields. Parts are separated by dividers, not boxes.
function PolicyPart({ title, note, children }) {
  const titleId = useId();
  return (
    <Box
      role="group"
      aria-labelledby={titleId}
      sx={{
        p: 2,
        display: "flex",
        flexDirection: "column",
        gap: 1.5,
        minWidth: 0,
      }}
    >
      <Box>
        <Typography
          id={titleId}
          component="h3"
          typography="s1"
          fontWeight="fontWeightMedium"
          color="text.primary"
        >
          {title}
        </Typography>
        <AutoNote>{note}</AutoNote>
      </Box>
      {children}
    </Box>
  );
}

PolicyPart.propTypes = {
  title: PropTypes.string.isRequired,
  note: PropTypes.node.isRequired,
  children: PropTypes.node,
};

// "$0.05 in / $0.20 out per 1M"; a field the org left blank is the model
// database's.
const orgPriceText = ({ input, output }) => {
  const part = (v, side) =>
    v == null ? `model database ${side}` : `${formatPerMillion(v)} ${side}`;
  return `${part(input, "in")} / ${part(output, "out")} per 1M`;
};

// A model on your own provider that your org does not price: what recent
// decisions show the gateway did (priceEvidence, autoDecisions.
// gatewayPriceEvidence), else how auto will price it.
const unpricedOwnText = (verdict) => {
  if (verdict === "priced")
    return "Priced by the gateway's model database (your org sets no price for it)";
  if (verdict === "unpriced")
    return "No price data: auto set it aside in a recent request. Give it a price in Org Config";
  return "Your org sets no price for it: auto uses the gateway's model database price, and sets it aside if that has none";
};

const AutoRoutingTab = ({
  gatewayId,
  canWrite,
  catalogModels,
  managedStatus,
  priceEvidence = undefined,
}) => {
  const {
    data: config,
    isLoading,
    error,
    refetch,
  } = useGatewayConfig(gatewayId);
  const providers = useProviderHealth(gatewayId);
  const providerList = providers.data?.providers;
  const providersReady = !providers.error && providerList != null;
  const ownModels = useMemo(() => {
    const list = Array.isArray(providerList) ? providerList : Object.values(providerList || {});
    return [...new Set(list.flatMap((p) => Array.isArray(p?.models) ? p.models : []))]
      .filter((model) => typeof model === "string" && model)
      .sort();
  }, [providerList]);
  const saveMutation = useSaveAutoRouting();
  const receiptScope = useModelReadScope(gatewayId);
  // Session-memory evidence, scoped to this gateway. A config refetch only
  // proves storage, so it must not erase an unconfirmed apply receipt. Keeping
  // it in the query cache also survives leaving and reopening this page.
  const { data: applyReceipt } = useQuery({
    queryKey: autoApplyReceiptKey(gatewayId, receiptScope),
    queryFn: () => null,
    enabled: false,
    initialData: null,
    gcTime: Infinity,
  });

  // The patch's nulls are only safe for keys the saved config has (see
  // autoPatch), so a save waits for a successful read.
  const savedAuto = config?.routing?.auto;
  const saved = useMemo(() => draftFromAuto(savedAuto), [savedAuto]);
  const [draft, setDraft] = useState(saved);

  // Start over from the saved config when its content changes (a save, or an
  // edit elsewhere). Background refetches return equal content and leave the
  // draft alone.
  const lastSaved = useRef({ receiptScope, saved });
  useEffect(() => {
    if (lastSaved.current.receiptScope === receiptScope && draftsEqual(lastSaved.current.saved, saved)) return;
    lastSaved.current = { receiptScope, saved };
    setDraft(saved);
  }, [receiptScope, saved]);

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
    if (ownSet.has(id)) return providersReady ? OWN_GROUP : "Your providers (last loaded)";
    if (catalogById.has(id)) return MANAGED_GROUP;
    return providersReady ? MISSING_GROUP : "Provider availability not verified";
  };

  const inputError = capError(draft.maxInput);
  const outputError = capError(draft.maxOutput);
  const dirty = !draftsEqual(draft, saved);
  const managedOffered = Boolean(managedStatus?.available);
  const managedOff = managedOffered && !managedStatus?.enabled;

  const update = (field, value) =>
    setDraft((prev) => ({ ...prev, [field]: value }));

  const submitPatch = (auto) => {
    saveMutation.mutate(
      { gatewayId, auto, receiptScope },
      {
        onSuccess: (result) => {
          // A change Django saved but the gateway did not apply is reported
          // as that, never with the success toast.
          const warning = autoApplyWarning(result);
          if (warning) {
            enqueueSnackbar(warning, {
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
      <AutoSection card title={POLICY_TITLE}>
        <Stack spacing={1.5} sx={{ p: 2 }}>
          <Skeleton variant="rounded" height={150} />
          <Skeleton variant="rounded" height={120} />
          <Skeleton variant="rounded" height={120} />
        </Stack>
      </AutoSection>
    );
  }

  if (error) {
    return (
      <AutoSection card title={POLICY_TITLE}>
        <GatewayEmptyState
          icon="routing"
          title="Couldn't load the routing settings"
          description={error.message || "The gateway did not answer."}
          actionLabel="Retry"
          onAction={() => refetch()}
          sx={{ py: 4 }}
        />
      </AutoSection>
    );
  }

  const disabled = !canWrite || !config || saveMutation.isPending;
  const receiptMatches = applyReceipt && draftsEqual(saved, applyReceipt.draft);

  return (
    <AutoSection card title={POLICY_TITLE}>
      {applyReceipt && (
        <Box sx={{ px: 2, pt: 1.5 }}>
          <Alert
            severity="warning"
            action={
              <Button
                color="inherit"
                size="small"
                disabled={disabled || dirty || !receiptMatches}
                onClick={() => submitPatch(applyReceipt.auto)}
              >
                Retry apply
              </Button>
            }
          >
            <Typography typography="s2" fontWeight={600}>
              {applyReceipt.warning}
            </Typography>
            {dirty
              ? "Save or discard your edits before retrying the stored change."
              : !receiptMatches
                ? "Stored settings have changed since this attempt. Review and save the settings you want; the earlier apply outcome is still unconfirmed."
                : "The settings below are stored, but live routing is not confirmed. Retry sends the same saved patch."}
            <Typography typography="s3" component="div">
              This receipt is kept for this gateway during this console session,
              not across a page reload.
            </Typography>
          </Alert>
        </Box>
      )}
      <PolicyPart
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
          sx={PREFERENCE_GROUP_SX}
        >
          {AUTO_PREFERENCES.map((p) => (
            <ToggleButton key={p.value} value={p.value} sx={PREFERENCE_TILE_SX}>
              <Box aria-hidden sx={TILE_MARK_SX}>
                <Radio
                  size="small"
                  checked={draft.preference === p.value}
                  disabled={disabled}
                  tabIndex={-1}
                  disableRipple
                  inputProps={{ tabIndex: -1, "aria-hidden": true }}
                  sx={{ p: 0 }}
                />
              </Box>
              <Box sx={{ minWidth: 0 }}>
                <Typography
                  typography="s1"
                  fontWeight="fontWeightMedium"
                  color="text.primary"
                  component="div"
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
      </PolicyPart>
      <Divider />
      <PolicyPart
        title="Candidate pool"
        note={
          managedOffered
            ? "Restrict auto to these models. Leave empty for the default pool: your providers' models, plus current models from major vendors in the Future AGI catalogue while Future AGI credits are on."
            : "Restrict auto to these models. Leave empty for the default pool: your providers' models."
        }
      >
        {!providersReady && (
          <Alert
            severity={providers.error ? "warning" : "info"}
            action={!providers.isLoading && (
              <Button color="inherit" size="small" disabled={providers.isFetching} onClick={() => providers.refetch()}>
                Retry providers
              </Button>
            )}
          >
            <Typography typography="s2" fontWeight={600}>
              {providers.error ? "Could not verify your provider models." : "Checking provider models."}
            </Typography>
            Saved candidates are kept. An unverified provider read does not mean a model is no longer offered.
            {providerList != null && " Showing the last loaded provider models."}
          </Alert>
        )}
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
                      {unpricedOwnText(priceEvidence?.get(id))}
                    </Typography>
                  )}
                  {!own && !m && (
                    <Typography
                      typography="s2"
                      color="text.secondary"
                      component="div"
                    >
                      {providersReady ? "No provider lists it any more" : "Provider availability has not been verified"}
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
        {managedOff && draft.candidates.some((id) => catalogById.has(id) && !ownSet.has(id)) && (
          <Alert severity="info">
            Future AGI catalogue models are only used while Future AGI credits are on.
          </Alert>
        )}
      </PolicyPart>
      <Divider />
      <PolicyPart
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
      </PolicyPart>
      <Box sx={dirty ? FOOTER_STUCK_SX : FOOTER_SX}>
        {!canWrite && (
          <Typography
            typography="s1"
            color="text.secondary"
            sx={{ mr: "auto" }}
          >
            You need edit access to change auto routing.
          </Typography>
        )}
        {canWrite && dirty && (
          <Box
            sx={{ display: "flex", alignItems: "center", gap: 1, mr: "auto" }}
          >
            <Box
              aria-hidden
              sx={{
                width: 8,
                height: 8,
                flexShrink: 0,
                borderRadius: "50%",
                bgcolor: "warning.main",
              }}
            />
            <Typography
              typography="s1"
              fontWeight="fontWeightMedium"
              color="text.primary"
            >
              Unsaved changes
            </Typography>
          </Box>
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
          disabled={disabled || (!dirty && !(applyReceipt && !receiptMatches)) || Boolean(inputError || outputError)}
          onClick={() => submitPatch(autoPatch(draft, savedAuto))}
        >
          Save changes
        </LoadingButton>
      </Box>
    </AutoSection>
  );
};

AutoRoutingTab.propTypes = {
  gatewayId: PropTypes.string,
  canWrite: PropTypes.bool,
  catalogModels: PropTypes.array,
  managedStatus: PropTypes.object,
  // Model id -> "priced" | "unpriced", from recent auto decisions.
  priceEvidence: PropTypes.instanceOf(Map),
};

export default AutoRoutingTab;
