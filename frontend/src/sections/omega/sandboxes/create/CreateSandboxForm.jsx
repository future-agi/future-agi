import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import MenuItem from "@mui/material/MenuItem";
import Switch from "@mui/material/Switch";
import TextField from "@mui/material/TextField";
import ToggleButton from "@mui/material/ToggleButton";
import ToggleButtonGroup from "@mui/material/ToggleButtonGroup";
import Typography from "@mui/material/Typography";

import { formatDuration } from "../lib/format";
import {
  CUSTOM_SIZE,
  LIMIT_FIELDS,
  canUseCustomLimits,
  canUseNetwork,
  chooseSize,
  chooseTemplate,
  extraImages,
  findTemplate,
  hasSizes,
  sourceChoices,
  templateChoices,
  ttlOptions,
} from "../lib/createForm";
import { networkOffText } from "../lib/offerCopy";
import SizeCards from "./SizeCards";
import TemplateGallery from "./TemplateGallery";

const SOURCE_LABELS = {
  catalog: "Template",
  image: "Image",
  environment: "Environment",
};

function Group({ label, hint, children, testId, action }) {
  return (
    <Box
      data-testid={testId}
      sx={{ display: "flex", flexDirection: "column", gap: 1 }}
    >
      <Box
        sx={{
          display: "flex",
          alignItems: "flex-start",
          justifyContent: "space-between",
          gap: 1,
        }}
      >
        <Box>
          <Typography
            typography="s1"
            fontWeight="fontWeightMedium"
            color="text.primary"
          >
            {label}
          </Typography>
          {hint && (
            <Typography typography="s2" color="text.secondary">
              {hint}
            </Typography>
          )}
        </Box>
        {action}
      </Box>
      {children}
    </Box>
  );
}

Group.propTypes = {
  label: PropTypes.string.isRequired,
  hint: PropTypes.node,
  children: PropTypes.node,
  testId: PropTypes.string,
  action: PropTypes.node,
};

function LimitFields({ caps, form, errors, set }) {
  return (
    <Box
      data-testid="create-custom-limits"
      sx={{
        display: "grid",
        gridTemplateColumns: { xs: "1fr", sm: "repeat(3, 1fr)" },
        gap: 1.5,
      }}
    >
      {LIMIT_FIELDS.map((field) => (
        <TextField
          key={field.key}
          size="small"
          type="number"
          label={field.unit ? `${field.label} (${field.unit})` : field.label}
          value={form[field.key]}
          onChange={set(field.key)}
          error={Boolean(errors[field.key])}
          helperText={
            errors[field.key] ||
            `${caps.limits.min[field.key]}–${caps.limits.max[field.key]}`
          }
          inputProps={{
            min: caps.limits.min[field.key],
            max: caps.limits.max[field.key],
            step: field.step,
            "data-testid": `create-${field.key}`,
          }}
        />
      ))}
    </Box>
  );
}

LimitFields.propTypes = {
  caps: PropTypes.object.isRequired,
  form: PropTypes.object.isRequired,
  errors: PropTypes.object.isRequired,
  set: PropTypes.func.isRequired,
};

function SourcePicker({ caps, form, errors, onChange, environments }) {
  const set = (key) => (event) => onChange({ [key]: event.target.value });
  const envChoices = templateChoices(environments.data);
  // "Environment" is offered only when this workspace has a container
  // environment a sandbox can start from; an empty, disabled list was a dead
  // end (console-reality audit, sandboxes: Source toggle).
  const hasEnvironment = envChoices.some((choice) => !choice.disabled);
  const choices = sourceChoices(caps).filter(
    (choice) =>
      choice !== "environment" ||
      hasEnvironment ||
      form.source === "environment",
  );
  const images = caps.templates?.length ? extraImages(caps) : caps.images;
  return (
    <Group
      label={form.source === "catalog" ? "Template" : "Source"}
      hint={
        form.source === "catalog"
          ? "A ready image with the tools already installed."
          : form.source === "image"
            ? "An image from this deployment's allowed list."
            : "One of this workspace's container environments."
      }
      testId="create-source"
      action={
        choices.length > 1 ? (
          <ToggleButtonGroup
            exclusive
            size="small"
            value={form.source}
            onChange={(_, value) => value && onChange({ source: value })}
            aria-label="Source"
          >
            {choices.map((choice) => (
              <ToggleButton
                key={choice}
                value={choice}
                sx={{ px: 1.25, py: 0.25, typography: "s2" }}
                data-testid={`create-source-${choice}`}
              >
                {SOURCE_LABELS[choice]}
              </ToggleButton>
            ))}
          </ToggleButtonGroup>
        ) : null
      }
    >
      {form.source === "catalog" && (
        <>
          <TemplateGallery
            templates={caps.templates}
            sizes={caps.sizes || []}
            value={form.template}
            onChange={(id) => onChange(chooseTemplate(form, caps, id))}
          />
          {errors.template && (
            <Typography typography="s2" color="error.main">
              {errors.template}
            </Typography>
          )}
        </>
      )}
      {form.source === "image" && (
        <TextField
          select
          size="small"
          label="Image"
          value={images.includes(form.image) ? form.image : ""}
          onChange={set("image")}
          error={Boolean(errors.image)}
          helperText={errors.image}
          inputProps={{ "data-testid": "create-image" }}
        >
          {images.map((image) => (
            <MenuItem
              key={image}
              value={image}
              sx={{ fontFamily: "monospace", fontSize: 13 }}
            >
              {image}
            </MenuItem>
          ))}
        </TextField>
      )}
      {form.source === "environment" && (
        <TextField
          select
          size="small"
          label="Environment"
          value={form.environmentId}
          onChange={set("environmentId")}
          disabled={environments.isLoading || envChoices.length === 0}
          error={Boolean(errors.environmentId) || Boolean(environments.error)}
          helperText={
            environments.error
              ? environments.error.message
              : envChoices.length === 0 && !environments.isLoading
                ? "This workspace has no container environments yet."
                : errors.environmentId
          }
          inputProps={{ "data-testid": "create-environment" }}
        >
          {envChoices.map((choice) => (
            <MenuItem
              key={choice.id}
              value={choice.id}
              disabled={choice.disabled}
            >
              <Box>
                <Typography typography="s1">{choice.name}</Typography>
                {choice.reason && (
                  <Typography typography="s2" color="text.secondary">
                    {choice.reason}
                  </Typography>
                )}
              </Box>
            </MenuItem>
          ))}
        </TextField>
      )}
    </Group>
  );
}

SourcePicker.propTypes = {
  caps: PropTypes.object.isRequired,
  form: PropTypes.object.isRequired,
  errors: PropTypes.object.isRequired,
  onChange: PropTypes.func.isRequired,
  environments: PropTypes.object.isRequired,
};

function SizePicker({ caps, form, errors, onChange }) {
  const set = (key) => (event) => onChange({ [key]: event.target.value });
  if (!hasSizes(caps)) {
    return (
      <Group
        label="Resources"
        hint={`Up to ${caps.limits.max.cpus} CPU, ${caps.limits.max.memoryMb} MiB and ${caps.limits.max.pidsLimit} processes here.`}
      >
        <LimitFields caps={caps} form={form} errors={errors} set={set} />
      </Group>
    );
  }
  const custom = form.size === CUSTOM_SIZE;
  const customAllowed = canUseCustomLimits(caps);
  return (
    <Group
      label="Size"
      hint="CPU and memory reserved as the sandbox's limit. You can pick a different size for each sandbox."
      testId="create-size"
      action={
        customAllowed ? (
          <Button
            size="small"
            onClick={() =>
              onChange(
                chooseSize(
                  caps,
                  custom ? caps.defaultSize || "small" : CUSTOM_SIZE,
                ),
              )
            }
            sx={{ typography: "s2", minWidth: 0, flexShrink: 0 }}
            data-testid="create-size-custom"
          >
            {custom ? "Use a size" : "Custom"}
          </Button>
        ) : null
      }
    >
      {custom ? (
        <LimitFields caps={caps} form={form} errors={errors} set={set} />
      ) : (
        <SizeCards
          sizes={caps.sizes}
          value={form.size}
          suggested={suggestedSize(caps, form)}
          onChange={(id) => onChange(chooseSize(caps, id))}
        />
      )}
      {(errors.size || form.sizeBumped || form.sizeSuggested) && (
        <Typography
          typography="s2"
          color={errors.size ? "error.main" : "text.secondary"}
          data-testid="create-size-note"
        >
          {errors.size ||
            (form.sizeBumped
              ? `Moved to ${form.sizeBumped}: this template needs at least that much.`
              : `Set to ${form.sizeSuggested}, the size suggested for this template. You can pick another.`)}
        </Typography>
      )}
    </Group>
  );
}

SizePicker.propTypes = {
  caps: PropTypes.object.isRequired,
  form: PropTypes.object.isRequired,
  errors: PropTypes.object.isRequired,
  onChange: PropTypes.func.isRequired,
};

/** The size the chosen template suggests, for the "Suggested" mark on its card. */
function suggestedSize(caps, form) {
  if (form.source !== "catalog") return null;
  return findTemplate(caps, form.template)?.sizeHint || null;
}

/** The fields of the create drawer; every choice comes from EE's caps. */
export default function CreateSandboxForm({
  caps,
  form,
  errors,
  onChange,
  environments,
}) {
  const set = (key) => (event) => onChange({ [key]: event.target.value });
  const networkOffered = canUseNetwork(caps);
  const networkOn = form.network === "full";

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 3 }}>
      <TextField
        size="small"
        label="Name"
        placeholder="Optional, e.g. data-cleanup"
        value={form.name}
        onChange={set("name")}
        error={Boolean(errors.name)}
        helperText={errors.name}
        inputProps={{ "data-testid": "create-name", maxLength: 120 }}
      />

      <SourcePicker
        caps={caps}
        form={form}
        errors={errors}
        onChange={onChange}
        environments={environments}
      />

      <SizePicker caps={caps} form={form} errors={errors} onChange={onChange} />

      <Group label="Network" testId="create-network">
        <Box
          sx={{
            display: "flex",
            alignItems: "flex-start",
            justifyContent: "space-between",
            gap: 2,
          }}
        >
          <Box>
            <Typography
              typography="s1"
              color={networkOffered ? "text.primary" : "text.disabled"}
            >
              Outbound network {networkOn ? "policy-controlled" : "off"}
            </Typography>
            <Typography
              typography="s2"
              color="text.secondary"
              data-testid="create-network-explain"
            >
              {!networkOffered
                ? networkOffText(caps)
                : networkOn
                  ? "With networking selected, outbound access depends on this deployment's network controls. Where per-sandbox network policy is enabled, sandboxes created here start with outbound IP traffic blocked until you set an allowed policy through the Sandboxes API."
                  : "Off by default. The container has no network interface besides loopback, so nothing inside can reach the internet, your services or cloud metadata."}
            </Typography>
          </Box>
          <Switch
            checked={networkOn}
            disabled={!networkOffered}
            onChange={(event) =>
              onChange({ network: event.target.checked ? "full" : "none" })
            }
            inputProps={{
              "aria-label": "Outbound network",
              "data-testid": "create-network-toggle",
            }}
          />
        </Box>
        {networkOffered && (
          <Typography
            typography="s3"
            color="text.secondary"
            data-testid="create-egress-reason"
          >
            A policy can allow all public hosts, or only listed IPv4 address
            ranges, optionally limited to TCP or UDP and then to specific ports.
            This deployment sets the widest mode you can use. Cloud metadata,
            private and link-local addresses, the sandbox host and other
            sandboxes stay blocked in every mode.
          </Typography>
        )}
      </Group>

      <Group
        label="Idle timeout"
        hint="An idle sandbox hibernates: its container stops and its files are kept."
      >
        <TextField
          select
          size="small"
          label="Hibernate after"
          value={form.idleTtlSeconds}
          onChange={(event) =>
            onChange({ idleTtlSeconds: Number(event.target.value) })
          }
          inputProps={{ "data-testid": "create-ttl" }}
        >
          {ttlOptions(caps).map((seconds) => (
            <MenuItem key={seconds} value={seconds}>
              {formatDuration(seconds)} idle
            </MenuItem>
          ))}
        </TextField>
      </Group>
    </Box>
  );
}

CreateSandboxForm.propTypes = {
  caps: PropTypes.object.isRequired,
  form: PropTypes.object.isRequired,
  errors: PropTypes.object.isRequired,
  onChange: PropTypes.func.isRequired,
  environments: PropTypes.shape({
    data: PropTypes.array,
    isLoading: PropTypes.bool,
    error: PropTypes.object,
  }).isRequired,
};
