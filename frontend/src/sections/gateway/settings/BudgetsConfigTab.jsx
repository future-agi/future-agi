/* eslint-disable react/prop-types */
import React, { useState } from "react";
import {
  Box,
  Stack,
  Typography,
  TextField,
  Switch,
  FormControlLabel,
  Paper,
  Divider,
  Alert,
  MenuItem,
  IconButton,
  Chip,
  Collapse,
  Button,
  Autocomplete,
} from "@mui/material";
import Section from "src/components/section";
import { Icon } from "@iconify/react";
import { useAvailableModels } from "../hooks/useAvailableModels";

const PERIODS = [
  { value: "daily", label: "Daily" },
  { value: "weekly", label: "Weekly" },
  { value: "monthly", label: "Monthly" },
  { value: "total", label: "Total (no reset)" },
];

// ---------------------------------------------------------------------------
// Per-model sub-budgets within a hierarchy level entry
// ---------------------------------------------------------------------------
const PerModelBudgets = ({ perModel, onUpdate }) => {
  const availableModels = useAvailableModels();
  const entries = perModel || {};
  const [newModelName, setNewModelName] = useState("");

  const handleAdd = () => {
    if (!newModelName.trim()) return;
    onUpdate({ ...entries, [newModelName.trim()]: 0 });
    setNewModelName("");
  };

  const handleChange = (model, value) => {
    onUpdate({ ...entries, [model]: Number(value) || 0 });
  };

  const handleRemove = (model) => {
    const updated = { ...entries };
    delete updated[model];
    onUpdate(updated);
  };

  return (
    <Box sx={{ pl: 2, pt: 1 }}>
      <Typography
        variant="caption"
        color="text.secondary"
        sx={{ mb: 0.5, display: "block" }}
      >
        Per-Model Limits
      </Typography>
      {Object.entries(entries).map(([model, limit]) => (
        <Stack
          key={model}
          direction="row"
          spacing={1}
          alignItems="center"
          sx={{ mb: 0.5 }}
        >
          <Chip
            label={model}
            size="small"
            variant="outlined"
            icon={<Icon icon="solar:bot-linear" width={14} />}
          />
          <TextField
            size="small"
            type="number"
            label="Limit (USD)"
            value={limit || ""}
            onChange={(e) => handleChange(model, e.target.value)}
            sx={{ width: 120 }}
            inputProps={{ step: 0.01, min: 0 }}
          />
          <IconButton
            sx={{ color: "status.danger.fg" }}
            size="small"
            onClick={() => handleRemove(model)}
          >
            <Icon icon="solar:close-linear" width={16} />
          </IconButton>
        </Stack>
      ))}
      <Stack direction="row" spacing={1} alignItems="center" sx={{ mt: 0.5 }}>
        <Autocomplete
          freeSolo
          options={availableModels}
          inputValue={newModelName}
          onInputChange={(_, val) => setNewModelName(val)}
          onChange={(_, val) => {
            if (val) {
              setNewModelName(val);
            }
          }}
          renderInput={(params) => (
            <TextField
              {...params}
              size="small"
              placeholder="Model (e.g. gpt-4o)"
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  handleAdd();
                }
              }}
            />
          )}
          sx={{ width: 220 }}
        />
        <Button
          size="small"
          variant="text"
          onClick={handleAdd}
          disabled={!newModelName.trim()}
        >
          Add
        </Button>
      </Stack>
    </Box>
  );
};

// ---------------------------------------------------------------------------
// Collapsible section for one hierarchy level (teams, users, keys, tags)
// ---------------------------------------------------------------------------
const BudgetLevelSection = ({
  title,
  icon,
  entries,
  onUpdate,
  placeholder,
}) => {
  const [expanded, setExpanded] = useState(false);
  const [newName, setNewName] = useState("");
  const items = entries || {};
  const count = Object.keys(items).length;

  const handleAdd = () => {
    if (!newName.trim()) return;
    const updated = { ...items, [newName.trim()]: { limit: 0 } };
    onUpdate(updated);
    setNewName("");
    setExpanded(true);
  };

  const handleRemove = (name) => {
    const updated = { ...items };
    delete updated[name];
    onUpdate(updated);
  };

  const handleEntryChange = (name, field, value) => {
    const updated = { ...items };
    updated[name] = { ...(updated[name] || {}), [field]: value };
    onUpdate(updated);
  };

  return (
    <Paper variant="outlined" sx={{ p: 2 }}>
      <Stack
        direction="row"
        alignItems="center"
        spacing={1}
        sx={{ cursor: "pointer" }}
        onClick={() => setExpanded(!expanded)}
      >
        <Icon icon={icon} width={20} />
        <Typography variant="subtitle1" fontWeight="600" sx={{ flex: 1 }}>
          {title}
        </Typography>
        {count > 0 && (
          <Chip label={count} size="small" color="primary" variant="outlined" />
        )}
        <Icon
          icon={
            expanded
              ? "solar:alt-arrow-up-linear"
              : "solar:alt-arrow-down-linear"
          }
          width={20}
        />
      </Stack>

      <Collapse in={expanded}>
        <Box sx={{ mt: 2 }}>
          {count === 0 && (
            <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
              No {title.toLowerCase()} defined.
            </Typography>
          )}

          {Object.entries(items).map(([name, cfg]) => {
            const config = cfg || {};
            const hardValue = config.hard !== undefined ? config.hard : true;
            return (
              <Section
                key={name}
                sx={{
                  py: 1.5,
                  mb: 1.5,
                  borderTop: "1px solid",
                  borderColor: "divider",
                }}
              >
                <Stack
                  direction="row"
                  spacing={1.5}
                  alignItems="center"
                  flexWrap="wrap"
                >
                  <Chip
                    label={name}
                    size="small"
                    color="secondary"
                    variant="outlined"
                  />
                  <TextField
                    size="small"
                    type="number"
                    label="Limit (USD)"
                    value={config.limit || ""}
                    onChange={(e) =>
                      handleEntryChange(
                        name,
                        "limit",
                        Number(e.target.value) || 0,
                      )
                    }
                    sx={{ width: 130 }}
                    inputProps={{ step: 0.01, min: 0 }}
                  />
                  <TextField
                    select
                    size="small"
                    label="Period"
                    value={config.period || ""}
                    onChange={(e) =>
                      handleEntryChange(name, "period", e.target.value)
                    }
                    sx={{ width: 150 }}
                  >
                    <MenuItem value="">
                      <em>Default</em>
                    </MenuItem>
                    {PERIODS.map((p) => (
                      <MenuItem key={p.value} value={p.value}>
                        {p.label}
                      </MenuItem>
                    ))}
                  </TextField>
                  <FormControlLabel
                    control={
                      <Switch
                        size="small"
                        checked={hardValue}
                        onChange={(e) =>
                          handleEntryChange(name, "hard", e.target.checked)
                        }
                      />
                    }
                    label={<Typography variant="caption">Hard</Typography>}
                  />
                  <IconButton
                    sx={{ color: "status.danger.fg" }}
                    size="small"
                    onClick={() => handleRemove(name)}
                  >
                    <Icon icon="solar:trash-bin-trash-linear" width={18} />
                  </IconButton>
                </Stack>
                <PerModelBudgets
                  perModel={config.per_model || config.perModel}
                  onUpdate={(pm) => handleEntryChange(name, "per_model", pm)}
                />
              </Section>
            );
          })}

          <Stack direction="row" spacing={1} alignItems="center" sx={{ mt: 1 }}>
            <TextField
              size="small"
              placeholder={placeholder}
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  handleAdd();
                }
              }}
              sx={{ width: 220 }}
            />
            <Button
              size="small"
              variant="outlined"
              onClick={handleAdd}
              disabled={!newName.trim()}
            >
              Add
            </Button>
          </Stack>
        </Box>
      </Collapse>
    </Paper>
  );
};

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------
const BudgetsConfigTab = ({
  budgets,
  costTracking,
  onBudgetsChange,
  onCostTrackingChange,
}) => {
  const availableModels = useAvailableModels();
  const budgetConfig = budgets || {};
  const costConfig = costTracking || {};
  const customPricing =
    costConfig.custom_pricing || costConfig.customPricing || {};
  const [newModel, setNewModel] = useState("");

  const updateBudget = (key, value) => {
    onBudgetsChange({ ...budgetConfig, [key]: value });
  };

  const updateCost = (key, value) => {
    onCostTrackingChange({ ...costConfig, [key]: value });
  };

  const handleAddPricing = () => {
    if (!newModel.trim()) return;
    const updated = {
      ...customPricing,
      [newModel.trim()]: { input_per_mtok: 0, output_per_mtok: 0 },
    };
    updateCost("custom_pricing", updated);
    setNewModel("");
  };

  const handlePricingChange = (model, field, value) => {
    const updated = { ...customPricing };
    updated[model] = { ...(updated[model] || {}), [field]: Number(value) || 0 };
    updateCost("custom_pricing", updated);
  };

  const handleRemovePricing = (model) => {
    const updated = { ...customPricing };
    delete updated[model];
    updateCost("custom_pricing", updated);
  };

  return (
    <Box>
      <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 2 }}>
        <Icon icon="solar:wallet-linear" width={24} />
        <Typography variant="h6">Budgets & Cost Tracking</Typography>
      </Stack>

      {/* ===== BUDGETS ===== */}
      <Alert severity="info" sx={{ mb: 2 }}>
        Set hierarchical spend budgets at org, team, user, key, and tag levels.
        When a budget is exceeded, requests can be blocked (hard limit) or
        flagged with warnings.
      </Alert>

      <FormControlLabel
        control={
          <Switch
            checked={budgetConfig.enabled || false}
            onChange={(e) => updateBudget("enabled", e.target.checked)}
          />
        }
        label="Enable per-org budgets"
        sx={{ mb: 2 }}
      />

      {budgetConfig.enabled && (
        <Stack spacing={3}>
          {/* Organization Budget (existing) */}
          <Paper variant="outlined" sx={{ p: 2 }}>
            <Typography variant="subtitle1" fontWeight="600" gutterBottom>
              Organization Budget
            </Typography>
            <Stack direction="row" spacing={2} flexWrap="wrap">
              <TextField
                label="Spend Limit (USD)"
                type="number"
                size="small"
                value={budgetConfig.org_limit || budgetConfig.orgLimit || ""}
                onChange={(e) =>
                  updateBudget("org_limit", Number(e.target.value) || 0)
                }
                helperText="0 = unlimited"
                sx={{ width: 160 }}
                inputProps={{ step: 0.01, min: 0 }}
              />
              <TextField
                select
                label="Budget Period"
                size="small"
                value={
                  budgetConfig.org_period ||
                  budgetConfig.orgPeriod ||
                  budgetConfig.default_period ||
                  budgetConfig.defaultPeriod ||
                  "monthly"
                }
                onChange={(e) => updateBudget("org_period", e.target.value)}
                sx={{ width: 180 }}
              >
                {PERIODS.map((p) => (
                  <MenuItem key={p.value} value={p.value}>
                    {p.label}
                  </MenuItem>
                ))}
              </TextField>
              <TextField
                label="Warning Threshold"
                type="number"
                size="small"
                value={
                  budgetConfig.warn_threshold ||
                  budgetConfig.warnThreshold ||
                  0.8
                }
                onChange={(e) =>
                  updateBudget("warn_threshold", Number(e.target.value) || 0)
                }
                helperText="0-1 (e.g. 0.8 = 80%)"
                sx={{ width: 160 }}
                inputProps={{ step: 0.05, min: 0, max: 1 }}
              />
            </Stack>
            <FormControlLabel
              control={
                <Switch
                  size="small"
                  checked={
                    budgetConfig.hard_limit ?? budgetConfig.hardLimit ?? true
                  }
                  onChange={(e) => updateBudget("hard_limit", e.target.checked)}
                />
              }
              label={
                <Typography variant="body2">
                  Hard limit (block requests when exceeded)
                </Typography>
              }
              sx={{ mt: 1 }}
            />
          </Paper>

          {/* Hierarchical Budget Levels */}
          <BudgetLevelSection
            title="Team Budgets"
            icon="solar:users-group-rounded-linear"
            levelKey="teams"
            entries={budgetConfig.teams}
            onUpdate={(v) => updateBudget("teams", v)}
            placeholder="Team name (e.g. engineering)"
          />
          <BudgetLevelSection
            title="User Budgets"
            icon="solar:user-linear"
            levelKey="users"
            entries={budgetConfig.users}
            onUpdate={(v) => updateBudget("users", v)}
            placeholder="User name (e.g. alice)"
          />
          <BudgetLevelSection
            title="Key Budgets"
            icon="solar:key-linear"
            levelKey="keys"
            entries={budgetConfig.keys}
            onUpdate={(v) => updateBudget("keys", v)}
            placeholder="API key name (e.g. ci-key)"
          />
          <BudgetLevelSection
            title="Tag Budgets"
            icon="solar:tag-linear"
            levelKey="tags"
            entries={budgetConfig.tags}
            onUpdate={(v) => updateBudget("tags", v)}
            placeholder="Tag (e.g. project:alpha)"
          />
        </Stack>
      )}

      <Divider sx={{ my: 3 }} />

      {/* ===== COST TRACKING ===== */}
      <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 2 }}>
        <Icon icon="solar:banknote-linear" width={20} />
        <Typography variant="subtitle1" fontWeight="600">
          Custom Pricing
        </Typography>
      </Stack>

      <Alert severity="info" sx={{ mb: 2 }}>
        Override default model pricing for cost calculations. Useful for
        negotiated enterprise rates or self-hosted models.
      </Alert>

      <FormControlLabel
        control={
          <Switch
            checked={costConfig.enabled || false}
            onChange={(e) => updateCost("enabled", e.target.checked)}
          />
        }
        label="Enable custom cost tracking"
        sx={{ mb: 2 }}
      />

      {costConfig.enabled && (
        <Stack spacing={2}>
          {Object.keys(customPricing).length === 0 && (
            <Typography variant="body2" color="text.secondary">
              No custom pricing defined. Models will use default pricing.
            </Typography>
          )}

          {Object.entries(customPricing).map(([model, pricing]) => (
            <Paper key={model} variant="outlined" sx={{ p: 1.5 }}>
              <Stack
                direction="row"
                spacing={1.5}
                alignItems="center"
                flexWrap="wrap"
              >
                <Chip
                  label={model}
                  size="small"
                  color="primary"
                  variant="outlined"
                />
                <TextField
                  size="small"
                  type="number"
                  label="Input $/MTok"
                  value={pricing.input_per_mtok ?? pricing.inputPerMtok ?? ""}
                  onChange={(e) =>
                    handlePricingChange(model, "input_per_mtok", e.target.value)
                  }
                  sx={{ width: 140 }}
                  inputProps={{ step: 0.01, min: 0 }}
                />
                <TextField
                  size="small"
                  type="number"
                  label="Output $/MTok"
                  value={pricing.output_per_mtok ?? pricing.outputPerMtok ?? ""}
                  onChange={(e) =>
                    handlePricingChange(
                      model,
                      "output_per_mtok",
                      e.target.value,
                    )
                  }
                  sx={{ width: 140 }}
                  inputProps={{ step: 0.01, min: 0 }}
                />
                <IconButton
                  sx={{ color: "status.danger.fg" }}
                  size="small"
                  onClick={() => handleRemovePricing(model)}
                >
                  <Icon icon="solar:trash-bin-trash-linear" width={18} />
                </IconButton>
              </Stack>
            </Paper>
          ))}

          <Stack direction="row" spacing={1}>
            <Autocomplete
              freeSolo
              options={availableModels}
              inputValue={newModel}
              onInputChange={(_, val) => setNewModel(val)}
              onChange={(_, val) => {
                if (val) {
                  setNewModel(val);
                }
              }}
              renderInput={(params) => (
                <TextField
                  {...params}
                  size="small"
                  placeholder="Model name (e.g. gpt-4o)"
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      handleAddPricing();
                    }
                  }}
                />
              )}
              sx={{ width: 250 }}
            />
          </Stack>
        </Stack>
      )}
    </Box>
  );
};

export default BudgetsConfigTab;
