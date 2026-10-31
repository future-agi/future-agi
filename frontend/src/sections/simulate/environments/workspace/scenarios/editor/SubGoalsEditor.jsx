import PropTypes from "prop-types";
import { useState } from "react";
import { enqueueSnackbar } from "notistack";
import {
  Box,
  Button,
  ButtonBase,
  IconButton,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import UiOnlyFlag from "../../../components/UiOnlyFlag";
import { UI_ONLY_SAVED } from "../../gaps/NeedsInputButton";
import {
  EDITOR_COPY,
  SUBGOAL_MAX,
  SUBGOAL_MIN,
  subGoalCountNote,
} from "../scenarioEditor.constants";
import SectionHeader from "./SectionHeader";

const MARKS = [
  {
    value: "critical",
    label: "Critical",
    tip: "Fails the scenario if it isn't met",
  },
  {
    value: "supporting",
    label: "Supporting",
    tip: "Only lowers the score if it isn't met",
  },
];

// Critical or supporting, as a two-way segmented control.
function MarkToggle({ value, onChange, goal }) {
  return (
    <Stack
      direction="row"
      sx={{
        flexShrink: 0,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "4px",
        p: "1px",
      }}
    >
      {MARKS.map((m) => {
        const on = value === m.value;
        return (
          <CustomTooltip key={m.value} show arrow size="small" title={m.tip}>
            <ButtonBase
              aria-pressed={on}
              aria-label={`${m.label}: ${goal}`}
              onClick={() => !on && onChange(m.value)}
              sx={{
                px: 0.75,
                height: 20,
                borderRadius: "4px",
                typography: "s3",
                fontWeight: on ? "fontWeightSemiBold" : "fontWeightMedium",
                color: on ? "text.primary" : "text.subtitle",
                bgcolor: on ? "action.selected" : "transparent",
              }}
            >
              {m.label}
            </ButtonBase>
          </CustomTooltip>
        );
      })}
    </Stack>
  );
}
MarkToggle.propTypes = {
  value: PropTypes.string,
  onChange: PropTypes.func,
  goal: PropTypes.string,
};

// The scenario's sub-goals, each critical or supporting, 2 to 7 of them. The
// API has no sub-goal or mark writes yet, so edits stay in the editor (they
// ride along with Send to builder) and never reach Save.
export default function SubGoalsEditor({ goals, onChange }) {
  const [adding, setAdding] = useState("");
  const note = subGoalCountNote(goals.length);
  const full = goals.length >= SUBGOAL_MAX;

  const write = (next) => {
    onChange(next);
    enqueueSnackbar(UI_ONLY_SAVED, { variant: "info" });
  };
  const add = () => {
    const text = adding.trim();
    if (!text || full) return;
    write([
      ...goals,
      { key: `new-${Date.now()}-${goals.length}`, text, mark: "critical" },
    ]);
    setAdding("");
  };

  return (
    <Box component="section" aria-label={EDITOR_COPY.subGoalsSection}>
      <Stack spacing={0.75}>
        <SectionHeader
          title={EDITOR_COPY.subGoalsSection}
          hint={`${SUBGOAL_MIN} to ${SUBGOAL_MAX} per scenario. It passes when every critical one holds; supporting ones are only reported.`}
          flag={<UiOnlyFlag feature="scenarioStates" />}
        />
        {goals.map((g, i) => {
          const removable = goals.length > SUBGOAL_MIN;
          return (
            <Stack
              key={g.key}
              direction="row"
              alignItems="center"
              spacing={1}
              sx={{
                px: 1.25,
                py: 0.75,
                border: "1px solid",
                borderColor: "divider",
                borderRadius: 1,
              }}
            >
              <Typography
                sx={{
                  typography: "s3",
                  color: "text.disabled",
                  width: 14,
                  flexShrink: 0,
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                {i + 1}.
              </Typography>
              <Typography
                sx={{
                  typography: "s2",
                  flex: 1,
                  minWidth: 0,
                  wordBreak: "break-word",
                }}
              >
                {g.text}
              </Typography>
              <MarkToggle
                value={g.mark}
                goal={g.text}
                onChange={(mark) =>
                  write(
                    goals.map((x) => (x.key === g.key ? { ...x, mark } : x)),
                  )
                }
              />
              <CustomTooltip
                show
                arrow
                size="small"
                title={removable ? "Remove" : `Keep at least ${SUBGOAL_MIN}`}
              >
                <span>
                  <IconButton
                    size="small"
                    aria-label={`Remove ${g.text}`}
                    disabled={!removable}
                    onClick={() => write(goals.filter((x) => x.key !== g.key))}
                  >
                    <Iconify
                      icon="solar:close-circle-linear"
                      width={16}
                      sx={{ color: "text.subtitle" }}
                    />
                  </IconButton>
                </span>
              </CustomTooltip>
            </Stack>
          );
        })}
        <Stack direction="row" spacing={1} alignItems="center">
          <TextField
            size="small"
            fullWidth
            value={adding}
            onChange={(e) => setAdding(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                add();
              }
            }}
            disabled={full}
            placeholder={
              full
                ? `${SUBGOAL_MAX} is the most a scenario takes`
                : "Add a sub-goal, e.g. Confirm the refund amount before issuing it"
            }
            InputProps={{ sx: { typography: "s2" } }}
          />
          <Button
            size="small"
            variant="outlined"
            onClick={add}
            disabled={!adding.trim() || full}
            sx={{
              color: "text.primary",
              borderColor: "divider",
              flexShrink: 0,
            }}
          >
            Add
          </Button>
        </Stack>
        {note && (
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
            {note}
          </Typography>
        )}
      </Stack>
    </Box>
  );
}
SubGoalsEditor.propTypes = {
  goals: PropTypes.arrayOf(
    PropTypes.shape({
      key: PropTypes.string,
      text: PropTypes.string,
      mark: PropTypes.string,
    }),
  ).isRequired,
  onChange: PropTypes.func.isRequired,
};
