import { useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Button,
  Checkbox,
  DialogContent,
  FormControlLabel,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from "@mui/material";

import ModalWrapper from "src/components/ModalWrapper/ModalWrapper";
import Section from "src/components/section";

const CHECKS = {
  contains: [
    "Contains text",
    "Matches literal text, not a regular expression.",
  ],
  not_contains: [
    "Does not contain text",
    "Fails when the literal text appears in the answer.",
  ],
  json_valid: [
    "Valid JSON",
    "The entire answer must be one JSON value, without Markdown fences.",
  ],
  json_equals: [
    "JSON value equals",
    "Matches an RFC6901 pointer and an exact typed JSON value. Null is different from missing.",
  ],
  json_schema: [
    "JSON schema",
    "Validates the entire JSON answer. External references and executable validators are not allowed.",
  ],
  file_delivered: [
    "File delivered",
    "Checks delivery of a target skill file, not whether the model followed it. Not applicable without the target.",
  ],
  semantic: [
    "Plain-language requirement · AI judged",
    "Requires an explicitly selected AI judge and a separately disclosed model call.",
  ],
};
const uid = () => crypto.randomUUID();
const bytes = (value) => new TextEncoder().encode(value).length;
const configFor = (kind) => {
  switch (kind) {
    case "contains":
    case "not_contains":
      return { text: "", caseSensitive: false };
    case "json_equals":
      return { pointer: "", value: null };
    case "json_schema":
      return { schema: { type: "object" } };
    case "file_delivered":
      return { path: "SKILL.md", complete: true };
    case "semantic":
      return { requirement: "" };
    default:
      return {};
  }
};

function CheckEditor({ check, index, onChange, onRemove, onValidity }) {
  const [json, setJson] = useState(
    JSON.stringify(
      check.kind === "json_schema" ? check.config.schema : check.config.value,
      null,
      2,
    ),
  );
  const [invalidJson, setInvalidJson] = useState(false);
  const setConfig = (patch) =>
    onChange({ ...check, config: { ...check.config, ...patch } });
  const changeKind = (kind) => {
    const config = configFor(kind);
    setJson(
      JSON.stringify(
        kind === "json_schema" ? config.schema : config.value,
        null,
        2,
      ),
    );
    setInvalidJson(false);
    onValidity(check.id, true);
    onChange({ ...check, kind, config });
  };
  return (
    <Stack
      spacing={1}
      sx={{ py: 1.5, borderBottom: 1, borderColor: "divider" }}
    >
      <Stack direction="row" spacing={1} alignItems="start">
        <TextField
          select
          fullWidth
          size="small"
          label={`Check ${index + 1}`}
          value={check.kind}
          onChange={(event) => changeKind(event.target.value)}
        >
          {Object.entries(CHECKS).map(([kind, [label]]) => (
            <MenuItem key={kind} value={kind}>
              {label}
            </MenuItem>
          ))}
        </TextField>
        <Button
          size="small"
          onClick={onRemove}
          aria-label={`Remove check ${index + 1}`}
        >
          Remove
        </Button>
      </Stack>
      <Typography variant="s3" color="text.secondary">
        {CHECKS[check.kind]?.[1]}
      </Typography>
      {["contains", "not_contains"].includes(check.kind) && (
        <>
          <TextField
            label="Exact text"
            size="small"
            multiline
            value={check.config.text}
            inputProps={{ maxLength: 2000 }}
            onChange={(event) => setConfig({ text: event.target.value })}
          />
          <FormControlLabel
            control={
              <Checkbox
                checked={check.config.caseSensitive}
                onChange={(event) =>
                  setConfig({ caseSensitive: event.target.checked })
                }
              />
            }
            label="Match letter case"
          />
        </>
      )}
      {check.kind === "json_equals" && (
        <TextField
          label="JSON pointer"
          helperText="For example /decision/amount. Leave empty to check the whole value."
          size="small"
          value={check.config.pointer}
          onChange={(event) => setConfig({ pointer: event.target.value })}
        />
      )}
      {["json_equals", "json_schema"].includes(check.kind) && (
        <TextField
          label={
            check.kind === "json_equals" ? "Expected JSON value" : "JSON schema"
          }
          size="small"
          multiline
          minRows={3}
          value={json ?? ""}
          error={invalidJson}
          helperText={
            invalidJson
              ? "Enter valid JSON. A schema must be an object."
              : "Numbers, strings, booleans and null are compared without type conversion."
          }
          onChange={(event) => {
            setJson(event.target.value);
            try {
              const value = JSON.parse(event.target.value);
              if (
                check.kind === "json_schema" &&
                (!value || typeof value !== "object" || Array.isArray(value))
              )
                throw new Error("Not an object");
              setInvalidJson(false);
              onValidity(check.id, true);
              setConfig(
                check.kind === "json_equals" ? { value } : { schema: value },
              );
            } catch {
              setInvalidJson(true);
              onValidity(check.id, false);
              onChange({ ...check });
            }
          }}
        />
      )}
      {check.kind === "file_delivered" && (
        <>
          <TextField
            label="Target file path"
            size="small"
            value={check.config.path}
            onChange={(event) => setConfig({ path: event.target.value })}
          />
          <FormControlLabel
            control={
              <Checkbox
                checked={check.config.complete}
                onChange={(event) =>
                  setConfig({ complete: event.target.checked })
                }
              />
            }
            label="Require the complete file"
          />
        </>
      )}
      {check.kind === "semantic" && (
        <TextField
          label="AI judged requirement"
          size="small"
          multiline
          minRows={2}
          value={check.config.requirement}
          inputProps={{ maxLength: 2000 }}
          onChange={(event) => setConfig({ requirement: event.target.value })}
        />
      )}
      <FormControlLabel
        control={
          <Checkbox
            checked={check.critical}
            onChange={(event) =>
              onChange({ ...check, critical: event.target.checked })
            }
          />
        }
        label="Highlight failures as critical (every check must still pass)"
      />
    </Stack>
  );
}
CheckEditor.propTypes = {
  check: PropTypes.object.isRequired,
  index: PropTypes.number.isRequired,
  onChange: PropTypes.func.isRequired,
  onRemove: PropTypes.func.isRequired,
  onValidity: PropTypes.func.isRequired,
};

export default function TestCaseEditor({
  open,
  initialCase,
  onClose,
  onSave,
  isSaving = false,
  canSave = true,
  error,
}) {
  const [value, setValue] = useState(() =>
    initialCase
      ? {
          id: initialCase.id,
          title: initialCase.title,
          sourceGroupId: initialCase.sourceGroupId,
          prompt: initialCase.prompt,
          fixtures: initialCase.fixtures,
          expectedTrigger: initialCase.expectedTrigger,
          checks: initialCase.checks,
          reviewed: initialCase.reviewed,
        }
      : {
          id: uid(),
          title: "",
          sourceGroupId: uid(),
          prompt: "",
          fixtures: [],
          expectedTrigger: null,
          checks: [],
          reviewed: false,
        },
  );
  const [invalidChecks, setInvalidChecks] = useState([]);
  const edit = (patch) =>
    setValue((previous) => ({ ...previous, ...patch, reviewed: false }));
  const validChecks = value.checks.every((check) => {
    if (!CHECKS[check.kind]) return false;
    if (["contains", "not_contains"].includes(check.kind))
      return Boolean(check.config.text.trim());
    if (check.kind === "semantic")
      return Boolean(check.config.requirement.trim());
    if (check.kind === "file_delivered")
      return Boolean(check.config.path.trim());
    if (check.kind === "json_equals")
      return (
        check.config.pointer === "" ||
        /^(?:\/(?:[^~]|~[01])*)+$/.test(check.config.pointer)
      );
    return true;
  });
  const promptTooLarge = bytes(value.prompt) > 16 * 1024;
  const fixturesTooLarge =
    bytes(value.fixtures.map((fixture) => fixture.text).join("")) > 48 * 1024;
  const valid =
    value.title.trim() &&
    value.prompt.trim() &&
    !promptTooLarge &&
    !fixturesTooLarge &&
    validChecks &&
    !invalidChecks.length &&
    value.fixtures.every((fixture) => fixture.name.trim()) &&
    (value.expectedTrigger !== null || value.checks.length > 0);
  return (
    <ModalWrapper
      open={open}
      onClose={onClose}
      title={initialCase ? "Review example" : "Add example"}
      actionBtnTitle="Save example"
      onSubmit={() => {
        if (canSave) onSave(value);
      }}
      isValid={Boolean(valid) && canSave}
      isLoading={isSaving}
      modalWidth="720px"
    >
      <DialogContent sx={{ p: 0 }}>
        <Stack spacing={2}>
          <Typography variant="s3" color="text.secondary">
            Saved examples are visible to your workspace. They are not added to
            the downloadable skill.
          </Typography>
          {!canSave && (
            <Alert severity="info">
              Workspace write access is required to save this example. Your
              inputs are preserved.
            </Alert>
          )}
          {error && <Alert severity="error">{error}</Alert>}
          <TextField
            autoFocus
            label="Title"
            size="small"
            value={value.title}
            inputProps={{ maxLength: 120 }}
            onChange={(event) => edit({ title: event.target.value })}
          />
          <TextField
            label="Request"
            size="small"
            multiline
            minRows={4}
            value={value.prompt}
            error={promptTooLarge}
            helperText={
              promptTooLarge
                ? "Keep the request within 16 KiB of UTF-8 text."
                : "Use an actual request with approved facts, not an expected answer hidden in the prompt."
            }
            onChange={(event) => edit({ prompt: event.target.value })}
          />
          <TextField
            select
            label="Use this skill?"
            size="small"
            value={
              value.expectedTrigger === null
                ? "either"
                : String(value.expectedTrigger)
            }
            onChange={(event) =>
              edit({
                expectedTrigger:
                  event.target.value === "either"
                    ? null
                    : event.target.value === "true",
              })
            }
          >
            <MenuItem value="true">Yes</MenuItem>
            <MenuItem value="false">No</MenuItem>
            <MenuItem value="either">Either</MenuItem>
          </TextField>
          <Section
            title="Text fixtures"
            description="Supplied evidence only. No files, scripts or external actions are executed."
          >
            <Stack spacing={1.5}>
              {value.fixtures.map((fixture, index) => (
                <Stack key={fixture.id} spacing={1}>
                  <TextField
                    size="small"
                    label={`Fixture ${index + 1} name`}
                    value={fixture.name}
                    onChange={(event) =>
                      edit({
                        fixtures: value.fixtures.map((item) =>
                          item.id === fixture.id
                            ? { ...item, name: event.target.value }
                            : item,
                        ),
                      })
                    }
                  />
                  <TextField
                    size="small"
                    label={`Fixture ${index + 1} text`}
                    multiline
                    minRows={2}
                    value={fixture.text}
                    onChange={(event) =>
                      edit({
                        fixtures: value.fixtures.map((item) =>
                          item.id === fixture.id
                            ? { ...item, text: event.target.value }
                            : item,
                        ),
                      })
                    }
                  />
                  <Button
                    size="small"
                    onClick={() =>
                      edit({
                        fixtures: value.fixtures.filter(
                          (item) => item.id !== fixture.id,
                        ),
                      })
                    }
                  >
                    Remove fixture {index + 1}
                  </Button>
                </Stack>
              ))}
              {fixturesTooLarge && (
                <Alert severity="error">
                  Keep combined fixture text within 48 KiB.
                </Alert>
              )}
              <Box>
                <Button
                  size="small"
                  variant="outlined"
                  onClick={() =>
                    edit({
                      fixtures: [
                        ...value.fixtures,
                        { id: uid(), name: "", text: "" },
                      ],
                    })
                  }
                >
                  Add text fixture
                </Button>
              </Box>
            </Stack>
          </Section>
          <Section title="Success checks">
            {value.checks.map((check, index) => (
              <CheckEditor
                key={check.id}
                check={check}
                index={index}
                onChange={(next) =>
                  edit({
                    checks: value.checks.map((item) =>
                      item.id === check.id ? next : item,
                    ),
                  })
                }
                onRemove={() => {
                  edit({
                    checks: value.checks.filter((item) => item.id !== check.id),
                  });
                  setInvalidChecks((previous) =>
                    previous.filter((id) => id !== check.id),
                  );
                }}
                onValidity={(id, isValid) =>
                  setInvalidChecks((previous) =>
                    isValid
                      ? previous.filter((item) => item !== id)
                      : [...new Set([...previous, id])],
                  )
                }
              />
            ))}
            <Button
              size="small"
              variant="outlined"
              sx={{ mt: 1 }}
              disabled={value.checks.length >= 10}
              onClick={() =>
                edit({
                  checks: [
                    ...value.checks,
                    {
                      id: uid(),
                      kind: "contains",
                      critical: false,
                      config: configFor("contains"),
                    },
                  ],
                })
              }
            >
              Add success check
            </Button>
            {!value.checks.length && (
              <Typography variant="s3" color="text.secondary" sx={{ mt: 1 }}>
                Without answer checks, results describe selection only, not
                answer quality.
              </Typography>
            )}
          </Section>
          {initialCase?.exposedToAssist && (
            <Alert severity="info">
              Used in recorded assistance. Reusing this example does not
              establish independent improvement.
            </Alert>
          )}
          <FormControlLabel
            control={
              <Checkbox
                checked={value.reviewed}
                onChange={(event) =>
                  setValue((previous) => ({
                    ...previous,
                    reviewed: event.target.checked,
                  }))
                }
              />
            }
            label="I reviewed the request, supplied facts and expected behavior"
          />
          <Typography variant="s3" color="text.secondary">
            Editing the request, fixtures or expectations clears this review.
            Unreviewed examples can be saved but cannot run.
          </Typography>
        </Stack>
      </DialogContent>
    </ModalWrapper>
  );
}
TestCaseEditor.propTypes = {
  open: PropTypes.bool.isRequired,
  initialCase: PropTypes.object,
  onClose: PropTypes.func.isRequired,
  onSave: PropTypes.func.isRequired,
  isSaving: PropTypes.bool,
  canSave: PropTypes.bool,
  error: PropTypes.string,
};
