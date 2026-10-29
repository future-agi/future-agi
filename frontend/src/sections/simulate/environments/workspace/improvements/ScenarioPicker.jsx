import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Checkbox, InputBase, Stack, Typography } from "@mui/material";

import Iconify from "src/components/iconify";

import { scenarioStatus } from "./launchFormView";

const link = {
  typography: "s3",
  color: "text.secondary",
  cursor: "pointer",
  "&:hover": { color: "text.primary" },
};

function TextLink({ onClick, children }) {
  return (
    <Typography
      component="button"
      type="button"
      onClick={onClick}
      sx={{ ...link, border: 0, p: 0, bgcolor: "transparent" }}
    >
      {children}
    </Typography>
  );
}
TextLink.propTypes = { onClick: PropTypes.func, children: PropTypes.node };

// One list of scenarios to tick: search, All / None / Back to suggested, then
// a row per scenario with how it did in the run.
function ScenarioList({
  testId,
  title,
  note,
  items,
  chosen,
  suggested,
  onChange,
  emptyText,
}) {
  const [q, setQ] = useState("");
  const set = new Set(chosen);
  const query = q.trim().toLowerCase();
  const shown = items.filter(
    (s) => !query || s.name.toLowerCase().includes(query),
  );
  const isSuggested =
    suggested.length === chosen.length && suggested.every((k) => set.has(k));
  const toggle = (key) =>
    onChange(set.has(key) ? chosen.filter((k) => k !== key) : [...chosen, key]);
  return (
    <Box data-testid={testId}>
      <Stack direction="row" alignItems="baseline" spacing={1} sx={{ mb: 0.5 }}>
        <Typography sx={{ typography: "s2", fontWeight: 600 }}>
          {title}
        </Typography>
        <Typography
          sx={{
            typography: "s3",
            color: "text.tertiary",
            flex: 1,
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {`${chosen.length} of ${items.length}`}
        </Typography>
        {!isSuggested && (
          <TextLink onClick={() => onChange(suggested)}>
            Back to suggested
          </TextLink>
        )}
        <TextLink onClick={() => onChange(items.map((s) => s.key))}>
          All
        </TextLink>
        <TextLink onClick={() => onChange([])}>None</TextLink>
      </Stack>
      <Typography sx={{ typography: "s3", color: "text.tertiary", mb: 0.75 }}>
        {note}
      </Typography>
      <Box
        sx={{ border: "1px solid", borderColor: "divider", borderRadius: 1 }}
      >
        {items.length > 8 && (
          <Stack
            direction="row"
            alignItems="center"
            spacing={0.75}
            sx={{
              px: 1.25,
              height: 32,
              borderBottom: "1px solid",
              borderColor: "divider",
            }}
          >
            <Iconify
              icon="solar:magnifer-linear"
              width={14}
              sx={{ color: "text.disabled" }}
            />
            <InputBase
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Search cases"
              inputProps={{ "aria-label": `Search ${title}` }}
              sx={{ typography: "s2", flex: 1 }}
            />
          </Stack>
        )}
        <Box sx={{ maxHeight: 248, overflowY: "auto" }}>
          {shown.map((s, i) => {
            const on = set.has(s.key);
            return (
              <Stack
                key={s.key}
                direction="row"
                alignItems="center"
                spacing={1}
                role="checkbox"
                aria-checked={on}
                aria-label={s.name}
                tabIndex={0}
                onClick={() => toggle(s.key)}
                onKeyDown={(e) => {
                  if (e.key === " " || e.key === "Enter") {
                    e.preventDefault();
                    toggle(s.key);
                  }
                }}
                sx={{
                  px: 1.25,
                  height: 32,
                  cursor: "pointer",
                  "&:hover": { bgcolor: "action.hover" },
                  ...(i > 0 && {
                    borderTop: "1px solid",
                    borderColor: "divider",
                  }),
                }}
              >
                <Checkbox
                  size="small"
                  checked={on}
                  tabIndex={-1}
                  disableRipple
                  inputProps={{ "aria-hidden": true }}
                  sx={{ p: 0 }}
                />
                <Typography
                  noWrap
                  sx={{ typography: "s2", flex: 1, minWidth: 0 }}
                >
                  {s.name}
                </Typography>
                <Typography
                  sx={{
                    typography: "s3",
                    color: "text.tertiary",
                    flexShrink: 0,
                  }}
                >
                  {scenarioStatus(s.passed)}
                </Typography>
              </Stack>
            );
          })}
          {!shown.length && (
            <Typography
              sx={{ typography: "s3", color: "text.tertiary", px: 1.25, py: 1 }}
            >
              {items.length ? "No case matches." : emptyText}
            </Typography>
          )}
        </Box>
      </Box>
    </Box>
  );
}
ScenarioList.propTypes = {
  testId: PropTypes.string,
  title: PropTypes.string.isRequired,
  note: PropTypes.string,
  items: PropTypes.array.isRequired,
  chosen: PropTypes.array.isRequired,
  suggested: PropTypes.array.isRequired,
  onChange: PropTypes.func.isRequired,
  emptyText: PropTypes.string,
};

// The design's case picker on launch-options: a scenario with a failed
// sub-goal can be fixed; any other can be protected. Never both.
export default function ScenarioPicker({
  options,
  targets,
  protectedKeys,
  onTargets,
  onProtected,
}) {
  const fixable = options.scenarios.filter((s) => s.failing_sub_goals.length);
  const protectable = options.scenarios.filter((s) => !targets.includes(s.key));
  return (
    <Stack spacing={2} sx={{ mt: 1.25 }}>
      <ScenarioList
        testId="cases-fix"
        title="Cases to fix"
        note="The failing cases this improvement works on. Some are held back to prove the fix; it never sees them."
        items={fixable}
        chosen={targets}
        suggested={options.suggested.target_scenario_keys}
        onChange={(keys) => {
          onTargets(keys);
          onProtected(protectedKeys.filter((k) => !keys.includes(k)));
        }}
        emptyText="Nothing is failing, so there's nothing to fix."
      />
      <ScenarioList
        testId="cases-protect"
        title="Cases to protect"
        note="Cases that must keep passing. Suggested: every case that passed in this run."
        items={protectable}
        chosen={protectedKeys}
        suggested={options.suggested.protected_scenario_keys.filter(
          (k) => !targets.includes(k),
        )}
        onChange={onProtected}
        emptyText="No other cases to protect."
      />
    </Stack>
  );
}

ScenarioPicker.propTypes = {
  options: PropTypes.object.isRequired,
  targets: PropTypes.array.isRequired,
  protectedKeys: PropTypes.array.isRequired,
  onTargets: PropTypes.func.isRequired,
  onProtected: PropTypes.func.isRequired,
};
