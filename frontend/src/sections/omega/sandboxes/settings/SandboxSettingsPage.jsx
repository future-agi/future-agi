import React, { useEffect, useMemo, useState } from "react";
import PropTypes from "prop-types";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Checkbox from "@mui/material/Checkbox";
import FormControlLabel from "@mui/material/FormControlLabel";
import MenuItem from "@mui/material/MenuItem";
import Switch from "@mui/material/Switch";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import LoadingButton from "@mui/lab/LoadingButton";
import { useQueryClient } from "@tanstack/react-query";
import { useSnackbar } from "notistack";

import { useWorkspace } from "src/contexts/WorkspaceContext";

import CodeButton from "../../code/CodeButton";
import { sandboxSettingsRequests } from "../../code/sandboxRequests";
import { sandboxKeys, useSandboxSettings } from "../api/queries";
import { useSandboxesApi } from "../api/SandboxesApiContext";
import SandboxErrorState from "../components/SandboxErrorState";
import SandboxesUnavailable, {
  sandboxesUnavailable,
} from "../components/SandboxesUnavailable";
import Section from "../components/Section";
import SessionGate, { LoadingRows } from "../components/SessionGate";
import { sizeSummary, ttlOptions } from "../lib/createForm";
import { formatDuration } from "../lib/format";
import {
  createRefusalText,
  sizeReason,
  templateReason,
} from "../lib/offerCopy";
import {
  buildSettingsPayload,
  orderSizes,
  reconcileDefaults,
  settingsFormFrom,
  sizeChoices,
  templateChoices,
  templateFitNote,
  templateFits,
  toggle,
  validateSettings,
} from "../lib/settingsForm";

function Header() {
  return (
    <Box sx={{ mb: 2.5 }}>
      <Typography
        typography="m3"
        fontWeight="fontWeightMedium"
        color="text.primary"
      >
        Sandboxes
      </Typography>
      <Typography typography="s1" color="text.secondary">
        What people in this workspace can create, within this deployment&apos;s
        limits. You can narrow them here.
      </Typography>
    </Box>
  );
}

function CheckList({ items, selected, disabled, onToggle, testId }) {
  return (
    <Box
      data-testid={testId}
      sx={{ display: "flex", flexDirection: "column", gap: 0.25 }}
    >
      {items.map((item) => (
        <FormControlLabel
          key={item.id}
          disabled={disabled || item.locked}
          control={
            <Checkbox
              size="small"
              // A choice this deployment cannot run is never shown as offered.
              checked={!item.locked && selected.includes(item.id)}
              onChange={() => onToggle(item.id)}
              inputProps={{ "data-testid": `${testId}-${item.id}` }}
            />
          }
          label={
            <Box sx={{ display: "flex", flexDirection: "column", py: 0.5 }}>
              <Typography typography="s1" color="text.primary">
                {item.label}
              </Typography>
              {item.detail && (
                <Typography
                  typography="s2"
                  sx={{
                    // Readable on both themes (warning.main is pale on white).
                    color: (theme) =>
                      item.warn
                        ? theme.palette.warning[
                            theme.palette.mode === "dark" ? "light" : "dark"
                          ]
                        : theme.palette.text.secondary,
                  }}
                  data-testid={`${testId}-${item.id}-detail`}
                >
                  {item.detail}
                </Typography>
              )}
            </Box>
          }
          sx={{
            alignItems: "flex-start",
            m: 0,
            "& .MuiCheckbox-root": { mt: 0.25 },
            // A size the binding or the host cannot run (or a template whose
            // image is not on the host) reads as unavailable, as its card does
            // in the create drawer, not as a live choice.
            opacity: item.locked ? 0.55 : 1,
          }}
        />
      ))}
    </Box>
  );
}

CheckList.propTypes = {
  items: PropTypes.array.isRequired,
  selected: PropTypes.array.isRequired,
  disabled: PropTypes.bool,
  onToggle: PropTypes.func.isRequired,
  testId: PropTypes.string.isRequired,
};

function SettingsForm({ view }) {
  const api = useSandboxesApi();
  const queryClient = useQueryClient();
  const { currentWorkspaceId } = useWorkspace();
  const { enqueueSnackbar } = useSnackbar();
  const { operator, caps, canEdit } = view;
  const initial = useMemo(() => settingsFormFrom(view), [view]);
  const [form, setForm] = useState(initial);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  useEffect(() => setForm(initial), [initial]);

  const errors = validateSettings(form, view);
  const payload = buildSettingsPayload(form, view);
  const dirty =
    JSON.stringify(payload) !==
    JSON.stringify(buildSettingsPayload(initial, view));
  const set = (patch) => setForm((current) => ({ ...current, ...patch }));
  // A size or template change keeps both defaults among what is offered.
  const choose = (patch) =>
    setForm((current) => reconcileDefaults({ ...current, ...patch }, view));

  const sizeOffers = sizeChoices(view);
  const sizeItems = sizeOffers.map((size) => ({
    id: size.id,
    label: size.cpus ? `${size.label} · ${sizeSummary(size)}` : size.label,
    detail: size.selectable ? size.goodFor : sizeReason(size),
    locked: !size.selectable,
  }));
  const templates = templateChoices(view);
  const templateItems = templates.map((template) => {
    const fitNote =
      template.selectable && form.templates.includes(template.id)
        ? templateFitNote(view, template, form.sizes)
        : null;
    return {
      id: template.id,
      label: template.name,
      detail: !template.selectable
        ? templateReason(template)
        : fitNote || template.description,
      warn: Boolean(fitNote),
      locked: !template.selectable,
    };
  });
  const defaultTemplates = templates.filter(
    (template) =>
      template.selectable &&
      form.templates.includes(template.id) &&
      templateFits(template, form.sizes),
  );

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      await api.saveSettings(payload);
      await queryClient.invalidateQueries({
        queryKey: sandboxKeys.all(currentWorkspaceId ?? "none"),
      });
      enqueueSnackbar("Sandbox settings saved", { variant: "success" });
    } catch (failure) {
      setError(failure.message || "The settings could not be saved.");
    } finally {
      setSaving(false);
    }
  };

  const idleChoices = caps ? ttlOptions(caps) : [form.idleTtlSeconds];

  return (
    <Box
      data-testid="sandbox-settings"
      sx={{ display: "flex", flexDirection: "column", gap: 2, maxWidth: 760 }}
    >
      {!canEdit && (
        <Alert severity="info" data-testid="sandbox-settings-readonly">
          Only workspace owners and admins can change these settings.
        </Alert>
      )}
      {caps?.createRefusal && (
        <Alert severity="warning" data-testid="sandbox-settings-refusal">
          Nobody in this workspace can create a sandbox right now.{" "}
          {createRefusalText(caps.createRefusal)}
        </Alert>
      )}

      <Section
        title="Sizes"
        caption="The sizes people can pick when they create a sandbox. A sandbox gets the size it asks for, never more."
        testId="settings-sizes"
      >
        <CheckList
          items={sizeItems}
          selected={form.sizes}
          disabled={!canEdit}
          onToggle={(id) =>
            choose({ sizes: orderSizes(toggle(form.sizes, id)) })
          }
          testId="settings-size"
        />
        {errors.sizes && (
          <Typography typography="s2" color="error.main">
            {errors.sizes}
          </Typography>
        )}
        <TextField
          select
          size="small"
          label="Default size"
          value={form.sizes.includes(form.defaultSize) ? form.defaultSize : ""}
          onChange={(event) => set({ defaultSize: event.target.value })}
          disabled={!canEdit}
          error={Boolean(errors.defaultSize)}
          helperText={errors.defaultSize}
          sx={{ mt: 1.5, minWidth: 220 }}
          inputProps={{ "data-testid": "settings-default-size" }}
        >
          {sizeOffers
            .filter((size) => size.selectable && form.sizes.includes(size.id))
            .map((size) => (
              <MenuItem key={size.id} value={size.id}>
                {size.label}
              </MenuItem>
            ))}
        </TextField>
      </Section>

      {templateItems.length > 0 && (
        <Section
          title="Templates"
          caption="Ready images with tools installed. Each one is pinned to an exact image."
          testId="settings-templates"
        >
          <CheckList
            items={templateItems}
            selected={form.templates}
            disabled={!canEdit}
            onToggle={(id) =>
              choose({
                templates: toggle(
                  form.templates,
                  id,
                  templateItems.map((item) => item.id),
                ),
              })
            }
            testId="settings-template"
          />
          {errors.templates && (
            <Typography
              typography="s2"
              color="error.main"
              data-testid="settings-templates-error"
            >
              {errors.templates}
            </Typography>
          )}
          <TextField
            select
            size="small"
            label="Default template"
            value={
              defaultTemplates.some((item) => item.id === form.defaultTemplate)
                ? form.defaultTemplate
                : ""
            }
            onChange={(event) => set({ defaultTemplate: event.target.value })}
            disabled={!canEdit || defaultTemplates.length === 0}
            error={Boolean(errors.defaultTemplate)}
            helperText={errors.defaultTemplate}
            sx={{ mt: 1.5, minWidth: 220 }}
            inputProps={{ "data-testid": "settings-default-template" }}
          >
            {defaultTemplates.map((item) => (
              <MenuItem key={item.id} value={item.id}>
                {item.name}
              </MenuItem>
            ))}
          </TextField>
        </Section>
      )}

      {operator.images.length > 0 && (
        <Section
          title="Other images"
          caption="Images this deployment allows outside the template catalog."
          testId="settings-images"
        >
          <CheckList
            items={operator.images.map((image) => ({
              id: image,
              label: image,
            }))}
            selected={form.images}
            disabled={!canEdit}
            onToggle={(id) => set({ images: toggle(form.images, id) })}
            testId="settings-image"
          />
        </Section>
      )}

      <Section title="Network" testId="settings-network">
        <Box
          sx={{
            display: "flex",
            alignItems: "flex-start",
            justifyContent: "space-between",
            gap: 2,
          }}
        >
          <Box>
            <Typography typography="s1" color="text.primary">
              Allow sandboxes with outbound network
            </Typography>
            <Typography typography="s2" color="text.secondary">
              {operator.allowNetwork
                ? "Network is off by default. This setting lets people request a sandbox with networking; it does not by itself allow outbound traffic."
                : "This deployment offers sandboxes without a network only."}
            </Typography>
          </Box>
          <Switch
            checked={form.allowNetwork}
            disabled={!canEdit || !operator.allowNetwork}
            onChange={(event) => set({ allowNetwork: event.target.checked })}
            inputProps={{
              "aria-label": "Allow sandboxes with outbound network",
              "data-testid": "settings-allow-network",
            }}
          />
        </Box>
      </Section>

      <Section title="Limits" testId="settings-limits">
        <Box
          sx={{
            display: "grid",
            gridTemplateColumns: { xs: "1fr", sm: "1fr 1fr" },
            gap: 2,
          }}
        >
          <TextField
            size="small"
            type="number"
            label="Running sandboxes limit"
            value={form.maxActive}
            onChange={(event) => set({ maxActive: event.target.value })}
            disabled={!canEdit}
            error={Boolean(errors.maxActive)}
            helperText={
              errors.maxActive ||
              `Hibernated sandboxes do not count. Up to ${operator.maxActivePerWorkspace} on this deployment.`
            }
            inputProps={{
              min: 1,
              max: operator.maxActivePerWorkspace,
              "data-testid": "settings-max-active",
            }}
          />
          <TextField
            select
            size="small"
            label="Default idle timeout"
            value={form.idleTtlSeconds}
            onChange={(event) =>
              set({ idleTtlSeconds: Number(event.target.value) })
            }
            disabled={!canEdit}
            helperText="People can still pick another when they create one."
            inputProps={{ "data-testid": "settings-idle" }}
          >
            {[...new Set([...idleChoices, form.idleTtlSeconds])]
              .sort((a, b) => a - b)
              .map((seconds) => (
                <MenuItem key={seconds} value={seconds}>
                  {formatDuration(seconds)} idle
                </MenuItem>
              ))}
          </TextField>
        </Box>
      </Section>

      {error && (
        <Alert severity="error" data-testid="sandbox-settings-error">
          {error}
        </Alert>
      )}
      {canEdit && (
        <Box sx={{ display: "flex", justifyContent: "flex-end", gap: 1 }}>
          <Button
            color="inherit"
            disabled={!dirty || saving}
            onClick={() => setForm(initial)}
          >
            Reset
          </Button>
          <LoadingButton
            variant="contained"
            color="primary"
            loading={saving}
            disabled={!dirty || Object.keys(errors).length > 0}
            onClick={save}
            data-testid="sandbox-settings-save"
          >
            Save
          </LoadingButton>
        </Box>
      )}
      {view.updatedAt && (
        <Typography typography="s3" color="text.secondary">
          Last changed {new Date(view.updatedAt).toLocaleString()}
        </Typography>
      )}
    </Box>
  );
}

SettingsForm.propTypes = { view: PropTypes.object.isRequired };

function SettingsBody() {
  const query = useSandboxSettings();
  if (query.isLoading) return <LoadingRows />;
  // Nothing here can be set until sandboxes are set up on this deployment.
  const unavailable = sandboxesUnavailable(query);
  if (unavailable) return <SandboxesUnavailable reason={unavailable.reason} />;
  if (query.error && !query.data) {
    return (
      <SandboxErrorState error={query.error} onRetry={() => query.refetch()} />
    );
  }
  return (
    <>
      <Box sx={{ display: "flex", justifyContent: "flex-end", mb: 1.5 }}>
        <CodeButton
          requests={sandboxSettingsRequests()}
          subject="sandbox settings"
        />
      </Box>
      <SettingsForm view={query.data} />
    </>
  );
}

/** Settings → Sandboxes: a workspace's own narrowing of the operator's sandbox policy. */
export default function SandboxSettingsPage() {
  return (
    <Box sx={{ p: { xs: 1, sm: 2 } }}>
      <Header />
      <SessionGate>
        <SettingsBody />
      </SessionGate>
    </Box>
  );
}
