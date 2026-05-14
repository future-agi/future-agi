/* eslint-disable react/prop-types */
import { useEffect, useRef, useState } from "react";
import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  MenuItem,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import { useUpdateAutomationRule } from "src/api/annotation-queues/annotation-queues";
import {
  SOURCE_OPTIONS,
  TRIGGER_FREQUENCY_OPTIONS,
  RuleFilterSection,
  RuleScopePicker,
  buildConditionsForRule,
  defaultFiltersForSource,
  getRuleSubmitDisabledTooltipTitle,
  isScopeReady,
  ruleConditionsToFilters,
  ruleConditionsToScope,
} from "./create-rule-dialog";

export default function EditRuleDialog({
  open,
  onClose,
  queueId,
  rule,
  queue,
}) {
  const [name, setName] = useState("");
  const [nameTouched, setNameTouched] = useState(false);
  const [sourceType, setSourceType] = useState("trace");
  const [triggerFrequency, setTriggerFrequency] = useState("manual");
  const [scope, setScope] = useState({});
  const [filters, setFilters] = useState(defaultFiltersForSource("trace"));

  const { mutate: updateRule, isPending } = useUpdateAutomationRule();
  const initializedRuleIdRef = useRef(null);

  useEffect(() => {
    if (rule && open && initializedRuleIdRef.current !== rule.id) {
      initializedRuleIdRef.current = rule.id;
      const src = rule.source_type || "trace";
      setName(rule.name || "");
      setNameTouched(false);
      setSourceType(src);
      setTriggerFrequency(rule.trigger_frequency || "manual");
      setScope(ruleConditionsToScope(rule));
      setFilters(ruleConditionsToFilters(rule));
    }
    if (!open) {
      initializedRuleIdRef.current = null;
      setNameTouched(false);
    }
  }, [rule, open]);

  const markNameTouched = () => {
    setNameTouched(true);
  };

  const handleSourceChange = (newSource) => {
    setSourceType(newSource);
    setScope({});
    setFilters(defaultFiltersForSource(newSource));
  };

  const handleSave = () => {
    updateRule(
      {
        queueId,
        ruleId: rule.id,
        name,
        source_type: sourceType,
        trigger_frequency: triggerFrequency,
        conditions: buildConditionsForRule(sourceType, filters, scope, queue),
      },
      {
        onSuccess: () => {
          onClose();
        },
      },
    );
  };

  if (!rule) return null;

  const disabled =
    isPending || !name.trim() || !isScopeReady(sourceType, scope, queue);
  const showNameError = nameTouched && !name.trim();
  const disabledTooltipTitle = getRuleSubmitDisabledTooltipTitle(
    sourceType,
    scope,
    queue,
    name,
  );

  return (
    <Dialog open={open} onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle>Edit Automation Rule</DialogTitle>
      <DialogContent>
        <Stack spacing={2.5} sx={{ mt: 1 }}>
          <TextField
            label="Rule name"
            fullWidth
            size="small"
            value={name}
            onChange={(event) => setName(event.target.value)}
            onBlur={markNameTouched}
            error={showNameError}
            helperText={showNameError ? "Rule name is required" : ""}
            required
            autoFocus
          />

          <Stack direction={{ xs: "column", sm: "row" }} spacing={2}>
            <TextField
              select
              label="Source type"
              fullWidth
              size="small"
              value={sourceType}
              onChange={(event) => {
                markNameTouched();
                handleSourceChange(event.target.value);
              }}
            >
              {SOURCE_OPTIONS.map((option) => (
                <MenuItem key={option.value} value={option.value}>
                  {option.label}
                </MenuItem>
              ))}
            </TextField>

            <TextField
              select
              label="Trigger"
              fullWidth
              size="small"
              value={triggerFrequency}
              onChange={(event) => {
                markNameTouched();
                setTriggerFrequency(event.target.value);
              }}
            >
              {TRIGGER_FREQUENCY_OPTIONS.map((option) => (
                <MenuItem key={option.value} value={option.value}>
                  {option.label}
                </MenuItem>
              ))}
            </TextField>
          </Stack>

          <RuleScopePicker
            sourceType={sourceType}
            scope={scope}
            setScope={setScope}
            queue={queue}
            onInteraction={markNameTouched}
          />

          <Typography variant="subtitle2">Conditions</Typography>
          <RuleFilterSection
            sourceType={sourceType}
            filters={filters}
            setFilters={setFilters}
            scope={scope}
            setScope={setScope}
            queue={queue}
            onInteraction={markNameTouched}
          />
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={isPending}>
          Cancel
        </Button>
        <Tooltip
          title={disabledTooltipTitle}
          disableHoverListener={!disabledTooltipTitle}
        >
          <span style={{ display: "inline-flex" }}>
            <Button
              variant="contained"
              onClick={handleSave}
              disabled={disabled}
            >
              {isPending ? "Saving..." : "Save"}
            </Button>
          </span>
        </Tooltip>
      </DialogActions>
    </Dialog>
  );
}
