import PropTypes from "prop-types";
import { useState } from "react";
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Alert,
  Autocomplete,
  Box,
  Button,
  Checkbox,
  FormControlLabel,
  FormHelperText,
  IconButton,
  Radio,
  RadioGroup,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import { useQuery } from "@tanstack/react-query";
import Iconify from "src/components/iconify";
import axios, { endpoints } from "src/utils/axios";
import {
  ENVIRONMENTS,
  EVENT_FAMILIES,
  FAILURE_STATUSES,
  FULL_PAYLOAD_HINT,
  FULL_PAYLOAD_OFF,
  HEADERS_HINT,
  LABEL_ACTIONS,
  NO_REPLAY,
  QUERY_TOKEN_WARNING,
  RUN_TARGETING_HINT,
  TRIGGER_KINDS,
  urlHint,
  URL_KEPT_HINT,
  hasQueryValue,
} from "./copy";
import { keptLine } from "./form";
import HttpUrlWarning from "./HttpUrlWarning";

const toggle = (list, value) =>
  list.includes(value) ? list.filter((v) => v !== value) : [...list, value];

function ChipChecks({ label, options, value, onChange }) {
  return (
    <Stack direction="row" alignItems="center" flexWrap="wrap" columnGap={1}>
      <Typography
        variant="caption"
        color="text.secondary"
        sx={{ minWidth: 92 }}
      >
        {label}
      </Typography>
      {options.map((o) => (
        <FormControlLabel
          key={o.value}
          control={
            <Checkbox
              size="small"
              checked={value.includes(o.value)}
              onChange={() => onChange(toggle(value, o.value))}
            />
          }
          label={o.label}
        />
      ))}
    </Stack>
  );
}

ChipChecks.propTypes = {
  label: PropTypes.string,
  options: PropTypes.array,
  value: PropTypes.array,
  onChange: PropTypes.func,
};

function PromptPicker({ value, onChange }) {
  const [search, setSearch] = useState("");
  const { data = [], isFetching } = useQuery({
    queryKey: ["webhooks", "prompt-picker", search],
    queryFn: () =>
      axios.get(endpoints.develop.runPrompt.createTemplateId, {
        params: { search: search || undefined, limit: 25 },
      }),
    select: (res) =>
      (res?.data?.results || []).map((p) => ({
        id: String(p.id),
        name: p.name,
      })),
    staleTime: 30_000,
  });
  return (
    <Autocomplete
      multiple
      size="small"
      options={data}
      value={value}
      loading={isFetching}
      filterOptions={(x) => x}
      isOptionEqualToValue={(a, b) => a.id === b.id}
      getOptionLabel={(o) => o.name}
      onInputChange={(_e, v) => setSearch(v)}
      onChange={(_e, v) => onChange(v)}
      renderInput={(params) => (
        <TextField
          {...params}
          label="These prompts"
          placeholder="Search prompts"
        />
      )}
      sx={{ mt: 1 }}
    />
  );
}

PromptPicker.propTypes = { value: PropTypes.array, onChange: PropTypes.func };

export default function WebhookFields({
  form,
  setForm,
  access,
  fullPayloadAllowed,
  urlPolicy,
  fieldError,
  showScope,
}) {
  const set = (patch) => setForm((f) => ({ ...f, ...patch }));
  const kept = form.keptSubs || [];
  const keptTypes = new Set(kept.map((s) => s.event_type));
  const checked = (type) => !!form.events[type];
  const promptsOn =
    checked("prompt.version.created") || checked("prompt.label.changed");
  const labelsOn = checked("prompt.label.changed");
  const runsOn =
    checked("deployment.run.completed") || checked("deployment.run.failed");
  const failuresOn = checked("deployment.run.failed");
  const fullDisabled = !access?.can_admin || !fullPayloadAllowed;

  return (
    <Stack spacing={2}>
      {showScope && (
        <Box>
          <Typography variant="subtitle2" gutterBottom>
            Receives events from
          </Typography>
          <RadioGroup
            row
            value={form.orgWide ? "org" : "workspace"}
            onChange={(e) => set({ orgWide: e.target.value === "org" })}
          >
            <FormControlLabel
              value="workspace"
              control={<Radio size="small" />}
              label="This workspace"
            />
            <FormControlLabel
              value="org"
              control={<Radio size="small" />}
              label="Every workspace (org-wide)"
            />
          </RadioGroup>
        </Box>
      )}

      <Box>
        <Typography variant="subtitle2" gutterBottom>
          Send to
        </Typography>
        <RadioGroup row value="http">
          <FormControlLabel
            value="http"
            control={<Radio size="small" />}
            label={
              urlPolicy?.http_allowed === true
                ? "An HTTPS or HTTP endpoint"
                : "An HTTPS endpoint"
            }
          />
        </RadioGroup>
      </Box>

      <TextField
        label="Name"
        size="small"
        value={form.name}
        onChange={(e) => set({ name: e.target.value })}
        error={fieldError?.attr === "name"}
        inputProps={{ maxLength: 200 }}
        required
      />

      <Box>
        <TextField
          label="Endpoint URL"
          size="small"
          fullWidth
          className={NO_REPLAY}
          value={form.url}
          onChange={(e) => set({ url: e.target.value })}
          placeholder={
            form.urlMasked
              ? form.savedUrl
              : "https://hooks.example.com/futureagi"
          }
          error={fieldError?.attr === "url"}
          helperText={
            fieldError?.attr === "url"
              ? fieldError.message
              : form.urlMasked
                ? URL_KEPT_HINT
                : urlHint(urlPolicy)
          }
          required={!form.urlMasked}
        />
        {fieldError?.attr !== "url" &&
          (form.url === form.savedUrl ||
            (!form.url && !form.savedUrl) ||
            (form.urlMasked && !form.url)) && (
            <HttpUrlWarning urlScheme={form.savedUrlScheme} />
          )}
        {hasQueryValue(form.url) && (
          <Alert severity="warning" sx={{ mt: 1 }}>
            {QUERY_TOKEN_WARNING}
          </Alert>
        )}
      </Box>

      <Box>
        <Typography variant="subtitle2">Send these events</Typography>
        {EVENT_FAMILIES.map((family) => (
          <Box key={family.id} sx={{ mt: 1 }}>
            <Typography variant="caption" color="text.secondary">
              {family.label}
            </Typography>
            {family.events.map((ev) => (
              <FormControlLabel
                key={ev.type}
                sx={{ display: "flex" }}
                control={
                  <Checkbox
                    size="small"
                    checked={checked(ev.type) || keptTypes.has(ev.type)}
                    disabled={keptTypes.has(ev.type)}
                    onChange={(e) =>
                      set({
                        events: { ...form.events, [ev.type]: e.target.checked },
                      })
                    }
                    inputProps={{ "aria-label": ev.label }}
                  />
                }
                label={
                  <span>
                    {ev.label}{" "}
                    <Typography
                      component="span"
                      variant="caption"
                      sx={{ fontFamily: "monospace" }}
                    >
                      {ev.type}
                    </Typography>
                  </span>
                }
              />
            ))}
          </Box>
        ))}
        {kept.length > 0 && (
          <Box
            sx={{ mt: 1 }}
            role="group"
            aria-label="Subscriptions set through the API"
          >
            <Typography variant="caption" color="text.secondary">
              Also sends these, set through the API or the CLI. They are kept as
              they are when you save:
            </Typography>
            <Box component="ul" sx={{ m: 0, pl: 2.5 }}>
              {kept.map((sub) => (
                <Typography
                  component="li"
                  variant="body2"
                  key={keptLine(sub)}
                  sx={{ fontFamily: "monospace", fontSize: 12 }}
                >
                  {keptLine(sub)}
                </Typography>
              ))}
            </Box>
          </Box>
        )}
        {fieldError?.attr?.startsWith("subscriptions") && (
          <FormHelperText error>{fieldError.message}</FormHelperText>
        )}
      </Box>

      {(promptsOn || runsOn) && (
        <Box>
          <Typography variant="subtitle2">Only for</Typography>
          {promptsOn && (
            <Box sx={{ mt: 1 }}>
              <RadioGroup
                row
                value={form.promptMode}
                onChange={(e) => set({ promptMode: e.target.value })}
              >
                <FormControlLabel
                  value="all"
                  control={<Radio size="small" />}
                  label="All prompts"
                />
                <FormControlLabel
                  value="these"
                  control={<Radio size="small" />}
                  label="These prompts"
                />
              </RadioGroup>
              {form.promptMode === "these" && (
                <PromptPicker
                  value={form.prompts}
                  onChange={(prompts) => set({ prompts })}
                />
              )}
            </Box>
          )}
          {labelsOn && (
            <Box sx={{ mt: 1 }}>
              <RadioGroup
                row
                value={form.labelMode}
                onChange={(e) => set({ labelMode: e.target.value })}
              >
                <FormControlLabel
                  value="any"
                  control={<Radio size="small" />}
                  label="Any label"
                />
                <FormControlLabel
                  value="these"
                  control={<Radio size="small" />}
                  label="These labels"
                />
              </RadioGroup>
              {form.labelMode === "these" && (
                <Autocomplete
                  multiple
                  freeSolo
                  size="small"
                  options={["Production", "Staging", "Development"]}
                  value={form.labelNames}
                  onChange={(_e, v) => set({ labelNames: v })}
                  renderInput={(params) => (
                    <TextField {...params} label="Labels" />
                  )}
                  sx={{ my: 1 }}
                />
              )}
              <ChipChecks
                label="Changes"
                options={LABEL_ACTIONS}
                value={form.actions}
                onChange={(actions) => set({ actions })}
              />
            </Box>
          )}
          {runsOn && (
            <Box sx={{ mt: 1 }}>
              <Typography variant="caption" color="text.secondary">
                All deployments
              </Typography>
              <ChipChecks
                label="Environments"
                options={ENVIRONMENTS}
                value={form.environments}
                onChange={(environments) => set({ environments })}
              />
              <ChipChecks
                label="Started by"
                options={TRIGGER_KINDS}
                value={form.triggerKinds}
                onChange={(triggerKinds) => set({ triggerKinds })}
              />
              {failuresOn && (
                <ChipChecks
                  label="For failures"
                  options={FAILURE_STATUSES}
                  value={form.statuses}
                  onChange={(statuses) => set({ statuses })}
                />
              )}
            </Box>
          )}
        </Box>
      )}

      <Box>
        <Typography variant="subtitle2">Payload</Typography>
        <RadioGroup
          value={form.payloadMode}
          onChange={(e) => set({ payloadMode: e.target.value })}
        >
          <FormControlLabel
            value="thin"
            control={<Radio size="small" />}
            label="IDs, names and links"
          />
          <Tooltip
            title={fullDisabled ? FULL_PAYLOAD_OFF : ""}
            placement="bottom-start"
          >
            <FormControlLabel
              value="full"
              disabled={fullDisabled}
              control={<Radio size="small" />}
              label="Also include the prompt text"
            />
          </Tooltip>
        </RadioGroup>
        <FormHelperText>
          {fullDisabled ? FULL_PAYLOAD_OFF : FULL_PAYLOAD_HINT}
        </FormHelperText>
        <FormControlLabel
          control={
            <Checkbox
              size="small"
              checked={form.includePeopleNames}
              onChange={(e) => set({ includePeopleNames: e.target.checked })}
            />
          }
          label="Include people's names"
        />
        <FormHelperText sx={{ mt: -0.5 }}>
          Display names only, never emails.
        </FormHelperText>
      </Box>

      <Accordion disableGutters variant="outlined">
        <AccordionSummary
          expandIcon={<Iconify icon="eva:arrow-ios-downward-fill" />}
        >
          <Typography variant="subtitle2">Headers</Typography>
        </AccordionSummary>
        <AccordionDetails>
          <FormHelperText sx={{ mb: 1 }}>{HEADERS_HINT}</FormHelperText>
          {form.headers.map((h, i) => (
            <Stack key={i} direction="row" spacing={1} sx={{ mb: 1 }}>
              <TextField
                size="small"
                label="Header name"
                value={h.name}
                onChange={(e) => {
                  const headers = form.headers.map((x, j) =>
                    j === i ? { ...x, name: e.target.value } : x,
                  );
                  set({ headers, headersTouched: true });
                }}
              />
              <TextField
                size="small"
                label="Value"
                type="password"
                value={h.value}
                placeholder={h.saved ? "Hidden after saving" : ""}
                onChange={(e) => {
                  const headers = form.headers.map((x, j) =>
                    j === i ? { ...x, value: e.target.value, saved: false } : x,
                  );
                  set({ headers, headersTouched: true });
                }}
                sx={{ flex: 1 }}
              />
              <IconButton
                aria-label="Remove header"
                onClick={() =>
                  set({
                    headers: form.headers.filter((_x, j) => j !== i),
                    headersTouched: true,
                  })
                }
              >
                <Iconify icon="eva:trash-2-outline" />
              </IconButton>
            </Stack>
          ))}
          <Button
            size="small"
            startIcon={<Iconify icon="eva:plus-fill" />}
            onClick={() =>
              set({
                headers: [...form.headers, { name: "", value: "" }],
                headersTouched: true,
              })
            }
          >
            Add header
          </Button>
          {fieldError?.attr?.startsWith("headers") && (
            <FormHelperText error>{fieldError.message}</FormHelperText>
          )}
        </AccordionDetails>
      </Accordion>

      <Accordion disableGutters variant="outlined">
        <AccordionSummary
          expandIcon={<Iconify icon="eva:arrow-ios-downward-fill" />}
        >
          <Typography variant="subtitle2">Delivery</Typography>
        </AccordionSummary>
        <AccordionDetails>
          <Stack direction="row" flexWrap="wrap" gap={1.5}>
            <TextField
              size="small"
              type="number"
              label="Timeout (s)"
              value={form.timeoutS}
              inputProps={{ min: 5, max: 30 }}
              onChange={(e) => set({ timeoutS: e.target.value })}
              sx={{ width: 130 }}
            />
            <TextField
              size="small"
              type="number"
              label="At most, requests a second"
              value={form.ratePerS}
              inputProps={{ min: 1, max: 100 }}
              onChange={(e) => set({ ratePerS: e.target.value })}
              sx={{ width: 200 }}
            />
            <TextField
              size="small"
              type="number"
              label="At a time"
              value={form.maxInFlight}
              inputProps={{ min: 1, max: 16 }}
              onChange={(e) => set({ maxInFlight: e.target.value })}
              sx={{ width: 110 }}
            />
            <TextField
              size="small"
              type="number"
              label="Keep retrying for (h)"
              value={form.maxAgeH}
              inputProps={{ min: 1, max: 72 }}
              onChange={(e) => set({ maxAgeH: e.target.value })}
              sx={{ width: 170 }}
            />
          </Stack>
          {access?.can_admin && (
            <>
              <FormControlLabel
                sx={{ mt: 1 }}
                control={
                  <Checkbox
                    size="small"
                    checked={form.acceptsRunTargeting}
                    onChange={(e) =>
                      set({ acceptsRunTargeting: e.target.checked })
                    }
                  />
                }
                label="Runs can ask to send their result here"
              />
              <FormHelperText>{RUN_TARGETING_HINT}</FormHelperText>
            </>
          )}
        </AccordionDetails>
      </Accordion>
    </Stack>
  );
}

WebhookFields.propTypes = {
  form: PropTypes.object.isRequired,
  setForm: PropTypes.func.isRequired,
  access: PropTypes.object,
  fullPayloadAllowed: PropTypes.bool,
  urlPolicy: PropTypes.object,
  fieldError: PropTypes.object,
  showScope: PropTypes.bool,
};
