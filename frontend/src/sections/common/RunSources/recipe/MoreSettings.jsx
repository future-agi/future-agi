// `More settings` of a harness recipe (lane Rb MUST 1; options-truth L1 G3
// A6/A7): one collapsed one-view Section whose summary line is the answer
// (`v2.x · Shell, Edit files · CLAUDE.md · 40 turns · $1.00 per try · Git
// diff + final message · Edit`); it expands in place to the version (pins
// the install line), built-in tools, instructions, budget, collect and the
// command preview (= the script's cli_command = the catalog's command).
// Open state is remembered (Section).
import React from "react";
import PropTypes from "prop-types";
import {
  Box,
  Checkbox,
  FormControlLabel,
  InputAdornment,
  MenuItem,
  Select,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import CustomTooltip from "src/components/tooltip";
import { Section } from "src/sections/common/ux/oneview";
import ScriptBlock from "../ScriptBlock";
import { catalogEntry } from "../catalog";
import RecipeRow from "./RecipeRow";
import {
  BUILTIN_TOOLS,
  COLLECT_OPTIONS,
  PROVIDER_TOOLS_OFF,
  builtinToolsWords,
  commandPreview,
  installLine,
  moreSettingsSummary,
  NO_TURN_LIMIT,
  profileOf,
  turnLimitWords,
  versionNote,
} from "./recipeModel";

const INSTRUCTION_FILES = [
  "CLAUDE.md",
  "AGENTS.md",
  "GEMINI.md",
  "CONVENTIONS.md",
  ".goosehints",
];

/** Where the harness reads the instructions file, in words. */
function instructionsWhere(recipe) {
  const p = profileOf(recipe.harness);
  const at = p.instructionsAt;
  if (!recipe.instructions) return "no instructions file";
  if (at === "--read") return `passed with --read ${recipe.instructions}`;
  if (at === "task")
    return "put before the task (this harness reads no instructions file)";
  if (at === "env")
    return `FI_INSTRUCTIONS_FILE=${recipe.instructions} in your command's environment`;
  const name = catalogEntry(recipe.harness)?.name ?? p.bin;
  if (at === recipe.instructions) return `read from ${at} in each try's folder`;
  return `copied to ${at}, where ${name} reads it`;
}

const numberField = (
  value,
  onChange,
  { label, end, min, max, step = 1, width = 104 },
) => (
  <TextField
    size="small"
    type="number"
    value={value ?? ""}
    onChange={(e) => {
      const n = Number(e.target.value);
      if (Number.isFinite(n)) onChange(Math.min(max, Math.max(min, n)));
    }}
    inputProps={{ min, max, step, "aria-label": label }}
    InputProps={
      end
        ? {
            endAdornment: (
              <InputAdornment position="end">
                <Typography typography="s3" color="text.secondary">
                  {end}
                </Typography>
              </InputAdornment>
            ),
          }
        : undefined
    }
    sx={{ width, "& input": { py: 0.5, typography: "s2" } }}
  />
);

export default function MoreSettings({ recipe, onChange, scopeId }) {
  const set = (patch) => onChange({ ...recipe, ...patch });
  const preview = commandPreview(recipe);
  const install = installLine(recipe);
  const note = versionNote(recipe);
  const files = [
    ...new Set([profileOf(recipe.harness).instructions, ...INSTRUCTION_FILES]),
  ];
  return (
    <Section
      id="harness-more"
      surface="newExperiment"
      scopeId={scopeId}
      title="More settings"
      defaultOpen={false}
      summary={`${moreSettingsSummary(recipe)} · Edit`}
    >
      <Stack spacing={1} sx={{ pl: 1, pb: 1 }} data-testid="harness-more">
        <RecipeRow
          label="Version"
          hint="The CLI version the script installs: a major (2.x) or an exact version (2.0.14)"
        >
          <TextField
            size="small"
            value={recipe.version ?? ""}
            onChange={(e) => set({ version: e.target.value.trim() })}
            placeholder="2.x"
            inputProps={{
              "aria-label": "Harness version",
              "data-testid": "harness-version",
              style: { fontFamily: "monospace" },
            }}
            sx={{ width: 120, "& input": { py: 0.5, typography: "s2" } }}
          />
          {install ? (
            <Typography
              typography="s3"
              color="text.secondary"
              data-testid="install-line"
              sx={{ fontFamily: "monospace", minWidth: 0 }}
            >
              {install}
            </Typography>
          ) : null}
          {note ? (
            <Typography typography="s3" color="text.secondary">
              {note}
            </Typography>
          ) : null}
        </RecipeRow>
        <RecipeRow
          label="Built-in tools"
          hint={`Tools the harness itself provides · ${builtinToolsWords(recipe.harness)}`}
        >
          {BUILTIN_TOOLS.map((t) => {
            const providerSide = !t.default;
            const box = (
              <FormControlLabel
                key={t.id}
                sx={{
                  mr: 0.5,
                  "& .MuiFormControlLabel-label": { typography: "s2" },
                }}
                control={
                  <Checkbox
                    size="small"
                    checked={Boolean(recipe.builtin_tools?.[t.id])}
                    onChange={(e) =>
                      set({
                        builtin_tools: {
                          ...recipe.builtin_tools,
                          [t.id]: e.target.checked,
                        },
                      })
                    }
                    sx={{ p: 0.5 }}
                  />
                }
                label={t.label}
              />
            );
            return providerSide ? (
              <CustomTooltip
                key={t.id}
                show
                title={PROVIDER_TOOLS_OFF}
                size="small"
                placement="top"
              >
                <span>{box}</span>
              </CustomTooltip>
            ) : (
              box
            );
          })}
        </RecipeRow>
        <RecipeRow
          label="Instructions"
          hint="The file the harness reads first in each try's folder"
        >
          <Select
            size="small"
            value={recipe.instructions}
            onChange={(e) => set({ instructions: e.target.value })}
            sx={{ height: 30, typography: "s2", minWidth: 150 }}
            inputProps={{ "aria-label": "Instructions file" }}
          >
            {files.map((f) => (
              <MenuItem key={f} value={f} sx={{ typography: "s2" }}>
                {f}
              </MenuItem>
            ))}
          </Select>
          <Typography
            typography="s3"
            color="text.secondary"
            data-testid="instructions-where"
          >
            {instructionsWhere(recipe)}
          </Typography>
        </RecipeRow>
        <RecipeRow
          label="Budget"
          hint="Per try. The runner stops a try at the first limit it hits."
        >
          {turnLimitWords(recipe) ? (
            numberField(
              recipe.budget.max_turns,
              (v) => set({ budget: { ...recipe.budget, max_turns: v } }),
              {
                label: "Max turns",
                end: "turns",
                min: 1,
                max: 200,
              },
            )
          ) : (
            <Typography
              typography="s2"
              color="text.secondary"
              data-testid="no-turn-limit"
            >
              {NO_TURN_LIMIT}
            </Typography>
          )}
          {numberField(
            recipe.budget.max_cost_usd,
            (v) => set({ budget: { ...recipe.budget, max_cost_usd: v } }),
            {
              label: "Max cost per try (USD)",
              end: "$ / try",
              min: 0.01,
              max: 50,
              step: 0.01,
              width: 120,
            },
          )}
          {numberField(
            recipe.budget.timeout_s,
            (v) => set({ budget: { ...recipe.budget, timeout_s: v } }),
            {
              label: "Time limit (seconds)",
              end: "s",
              min: 5,
              max: 7200,
              step: 5,
            },
          )}
          {turnLimitWords(recipe) ? (
            <Typography
              typography="s3"
              color="text.secondary"
              data-testid="turn-limit-words"
              sx={{ flexBasis: "100%" }}
            >
              {`Max turns: ${turnLimitWords(recipe)}`}
            </Typography>
          ) : null}
        </RecipeRow>
        <RecipeRow
          label="Collect"
          hint="What each try keeps as artifacts on its result"
        >
          {COLLECT_OPTIONS.map((c) => (
            <FormControlLabel
              key={c.id}
              sx={{
                mr: 0.5,
                "& .MuiFormControlLabel-label": { typography: "s2" },
              }}
              control={
                <Checkbox
                  size="small"
                  checked={Boolean(recipe.collect?.[c.id])}
                  onChange={(e) =>
                    set({
                      collect: { ...recipe.collect, [c.id]: e.target.checked },
                    })
                  }
                  sx={{ p: 0.5 }}
                />
              }
              label={c.label}
            />
          ))}
          <TextField
            size="small"
            placeholder="+ Files (glob, e.g. reports/*.json)"
            value={(recipe.extra_files ?? []).join(", ")}
            onChange={(e) =>
              set({
                extra_files: e.target.value
                  .split(",")
                  .map((x) => x.trim())
                  .filter(Boolean),
              })
            }
            inputProps={{ "aria-label": "Extra files to collect" }}
            sx={{ width: 220, "& input": { py: 0.5, typography: "s2" } }}
          />
        </RecipeRow>
        <Box>
          <Stack
            direction="row"
            alignItems="center"
            gap={0.75}
            sx={{ mb: 0.5 }}
          >
            <Typography typography="s2" color="text.secondary">
              Command preview
            </Typography>
            <Typography
              typography="s3"
              color="text.disabled"
              data-testid="command-approx"
            >
              {preview.label}
            </Typography>
          </Stack>
          <ScriptBlock
            text={preview.text}
            language="bash"
            dense
            copyTooltip="Copy command"
            testId="command-preview"
          />
          {preview.files.length ? (
            <Stack spacing={0.25} sx={{ mt: 0.5 }} data-testid="config-files">
              {preview.files.map((f) => (
                <Typography key={f.path} typography="s3" color="text.secondary">
                  <Box component="span" sx={{ fontFamily: "monospace" }}>
                    {f.path}
                  </Box>
                  {` · ${f.what} (the script writes it in each try's folder)`}
                </Typography>
              ))}
            </Stack>
          ) : null}
        </Box>
      </Stack>
    </Section>
  );
}

MoreSettings.propTypes = {
  recipe: PropTypes.object.isRequired,
  onChange: PropTypes.func.isRequired,
  scopeId: PropTypes.string,
};
