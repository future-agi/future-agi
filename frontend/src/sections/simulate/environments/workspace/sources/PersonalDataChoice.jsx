import PropTypes from "prop-types";
import { useId } from "react";
import { useSetPersonalData } from "src/api/simulate-environments/sources";
import {
  Box,
  MenuItem,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import { MONO_FONT } from "../contract/contract.constants";
import { Option } from "./SourceUsage";
import {
  COLUMN_MODES,
  NOT_PERSONAL,
  PERSONAL_CHOICES,
  applyFileChoice,
  choiceOf,
  columnsOf,
  standInOf,
} from "./personalData";

const shownChoices = (choice) =>
  PERSONAL_CHOICES.filter((one) => one.id !== "custom" || choice === "custom");

function ColumnRow({ column, readOnly, onColumn }) {
  return (
    <Stack direction="row" alignItems="center" spacing={1.5}>
      <Typography
        noWrap
        sx={{
          typography: "s1",
          fontFamily: MONO_FONT,
          width: 140,
          flexShrink: 0,
        }}
      >
        {column.name}
        {column.sensitive && (
          <Box
            component="span"
            sx={{ typography: "s3", color: "text.tertiary", ml: 0.5 }}
          >
            found
          </Box>
        )}
      </Typography>
      <TextField
        select
        size="small"
        value={column.mode}
        disabled={readOnly}
        onChange={(event) => onColumn(column.name, event.target.value)}
        inputProps={{ "aria-label": `${column.name} handling` }}
        sx={{ width: 180, flexShrink: 0 }}
      >
        {COLUMN_MODES.map((mode) => (
          <MenuItem key={mode.id} value={mode.id}>
            {mode.label}
          </MenuItem>
        ))}
      </TextField>
      <Typography
        noWrap
        sx={{
          typography: "s3",
          fontFamily: MONO_FONT,
          color: "text.secondary",
          flex: 1,
          minWidth: 0,
        }}
      >
        {column.mode === NOT_PERSONAL
          ? column.value
          : `${column.value} → ${standInOf(column.value, column.mode)}`}
      </Typography>
    </Stack>
  );
}

ColumnRow.propTypes = {
  column: PropTypes.object.isRequired,
  readOnly: PropTypes.bool,
  onColumn: PropTypes.func.isRequired,
};

/**
 * How one file's personal data is handled: keep it, replace it with synthetic
 * values, or say there is none, then column by column. The builder's
 * suggestion is marked, never applied for the person. Compact shows only the
 * file-level choices on one line.
 */
export default function PersonalDataChoice({
  suggested = "replace",
  choice = null,
  columns = [],
  onChoose,
  onColumn,
  readOnly = false,
  compact = false,
}) {
  const name = useId();
  if (compact) {
    return (
      <Stack
        direction="row"
        role="radiogroup"
        aria-label="Personal data"
        spacing={2}
        useFlexGap
        flexWrap="wrap"
      >
        {shownChoices(choice).map((one) => (
          <Tooltip key={one.id} title={one.note} arrow>
            <Box>
              <Option
                name={name}
                checked={choice === one.id}
                disabled={readOnly}
                onPick={() => onChoose(one.id)}
                label={`${one.id === "replace" ? "Replace" : one.label}${one.id === suggested ? " (suggested)" : ""}`}
              />
            </Box>
          </Tooltip>
        ))}
      </Stack>
    );
  }

  const found = columns.filter((column) => column.sensitive);
  return (
    <Stack spacing={1.5}>
      <Box>
        <Box role="radiogroup" aria-label="Personal data">
          {shownChoices(choice).map((one) => (
            <Option
              key={one.id}
              name={name}
              checked={choice === one.id}
              disabled={readOnly}
              onPick={() => onChoose(one.id)}
              note={one.note}
              label={
                <>
                  {one.label}
                  {one.id === suggested && (
                    <Box
                      component="span"
                      sx={{
                        typography: "s3",
                        color: "text.tertiary",
                        ml: 0.75,
                      }}
                    >
                      Suggested
                    </Box>
                  )}
                </>
              }
            />
          ))}
        </Box>
        {choice !== "none" && found.length > 0 && (
          <Typography
            sx={{ typography: "s3", color: "text.tertiary", mt: 0.5 }}
          >
            {`The builder found: ${found.map((column) => column.name).join(", ")}.`}
          </Typography>
        )}
        <Typography sx={{ typography: "s3", color: "text.secondary", mt: 0.5 }}>
          The agent and simulated people see the result.
        </Typography>
      </Box>
      {choice !== "none" && columns.length > 0 && onColumn && (
        <Stack spacing={1}>
          <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
            Mark any column the builder missed. Replace consistently keeps
            joins.
          </Typography>
          {columns.map((column) => (
            <ColumnRow
              key={column.name}
              column={column}
              readOnly={readOnly}
              onColumn={onColumn}
            />
          ))}
        </Stack>
      )}
    </Stack>
  );
}

PersonalDataChoice.propTypes = {
  suggested: PropTypes.string,
  // keep, replace, none or custom; null until the person picks.
  choice: PropTypes.string,
  // From `columnsOf`: { name, sensitive, mode, value } per column.
  columns: PropTypes.arrayOf(PropTypes.object),
  onChoose: PropTypes.func.isRequired,
  // (columnName, mode) for one column's handling.
  onColumn: PropTypes.func,
  readOnly: PropTypes.bool,
  compact: PropTypes.bool,
};

/**
 * The file drawer's Personal data tab. Every click rewrites the file's one
 * choice and column map, so all its edits stay a single pending edit.
 */
export function PersonalDataTab({ envId, source, readOnly = false }) {
  const setPersonalData = useSetPersonalData(envId);
  const choice = source.ui?.personal_choice || null;
  const edit = (change) => {
    const [columns, nextChoice] = change(columnsOf(source), choice);
    setPersonalData(source.source_id, {
      choice: nextChoice,
      privacy: Object.fromEntries(
        columns.map((column) => [column.name, column.mode]),
      ),
    });
  };

  return (
    <Box>
      <Typography sx={{ typography: "m3", mb: 0.5 }}>
        {choice ? "Personal data" : "Personal data: choose how to handle it"}
      </Typography>
      <PersonalDataChoice
        choice={choice}
        columns={columnsOf(source)}
        readOnly={readOnly}
        onChoose={(next) =>
          edit((columns) => [applyFileChoice(columns, next), next])
        }
        onColumn={(name, mode) =>
          edit((columns, fileChoice) => {
            const next = columns.map((column) =>
              column.name === name ? { ...column, mode } : column,
            );
            return [next, choiceOf(next, fileChoice)];
          })
        }
      />
    </Box>
  );
}

PersonalDataTab.propTypes = {
  envId: PropTypes.string.isRequired,
  source: PropTypes.object.isRequired,
  readOnly: PropTypes.bool,
};
