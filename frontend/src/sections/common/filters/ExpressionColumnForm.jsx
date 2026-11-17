import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import { Box, Button, Stack, TextField, Typography } from "@mui/material";
import ExpressionInput from "./ExpressionInput";
import { gridCatalogForColumns, gridValueHints } from "./gridDoorModel";
import {
  CELL_ERROR_PREFIX,
  columnDataType,
  expressionColumnError,
} from "./expressionColumn";

const MONO = "ui-monospace, SFMono-Regular, Menlo, monospace";
const TEST_ROWS = 3;

/**
 * W3-10 · the Add column menu's `Expression` form (W5-SCOPE §5 A5): Name,
 * Expression, `Test on 3 rows` (values from the server's preview, the one
 * value function), `Create column`. The mount (AddColumn/Expression, HO D7)
 * passes `test(expr) → Promise<[{row, value, status, error?}] | {cells, type}>`
 * and `onCreate({name, expr})`.
 * Props: columns (dataset columns with role), rows (wire rows for value
 *   hints), test, onCreate, onCancel, creating; edit mode: initialName,
 *   initialExpr, submitLabel and children (the re-run scope).
 */
const ExpressionColumnForm = ({
  columns = [],
  rows = [],
  test,
  onCreate,
  onCancel,
  creating = false,
  initialName = "",
  initialExpr = "",
  submitLabel = "Create column",
  children = null,
}) => {
  const [name, setName] = useState(initialName);
  const [expr, setExpr] = useState(initialExpr);
  const [tested, setTested] = useState(null);
  const catalog = useMemo(() => gridCatalogForColumns(columns), [columns]);
  const hints = useMemo(() => gridValueHints(columns, rows), [columns, rows]);
  const problem = useMemo(
    () => (expr.trim() ? expressionColumnError(expr, columns) : null),
    [expr, columns],
  );
  const error = problem
    ? {
        message: problem.hint
          ? `${problem.error}. ${problem.hint}`
          : problem.error,
        at: problem.at,
        length: problem.length,
      }
    : null;
  const ready = !!name.trim() && !!expr.trim() && !problem;

  const runTest = async () => {
    if (!ready || !test) return;
    setTested({ state: "loading" });
    try {
      // `test` answers the cells, or {cells, type} when the server names
      // the column's type (its preview carries values as text).
      const out = await test(expr.trim());
      const cells = (Array.isArray(out) ? out : out?.cells ?? []).slice(
        0,
        TEST_ROWS,
      );
      setTested({
        state: "done",
        cells,
        type:
          (!Array.isArray(out) && out?.type) ||
          columnDataType(cells.map((c) => c.value)),
      });
    } catch (e) {
      setTested({
        state: "done",
        cells: [],
        error: typeof e?.result === "string" ? e.result : "Test failed.",
      });
    }
  };

  return (
    <Stack spacing={1.5} data-testid="expression-column-form">
      <TextField
        size="small"
        label="Name"
        value={name}
        onChange={(e) => setName(e.target.value)}
        inputProps={{ "data-testid": "expression-column-name" }}
      />
      <Box>
        <Typography typography="s2" color="text.secondary" sx={{ mb: 0.5 }}>
          Expression
        </Typography>
        <ExpressionInput
          value={expr}
          onChange={(v) => {
            setExpr(v);
            setTested(null);
          }}
          catalog={catalog}
          hints={hints}
          error={error}
          placeholder="len(expected_reply)"
          onSubmit={runTest}
        />
      </Box>
      <Stack direction="row" alignItems="center" gap={1}>
        <Button
          size="small"
          variant="outlined"
          disabled={!ready || !test || tested?.state === "loading"}
          onClick={runTest}
          data-testid="expression-column-test"
        >
          Test on {TEST_ROWS} rows
        </Button>
        {tested?.type && (
          <Typography typography="s2" color="text.secondary">
            {tested.type}
          </Typography>
        )}
      </Stack>
      {tested?.state === "done" && (
        <Box data-testid="expression-column-results">
          {tested.error && (
            <Typography typography="s2" color="error.main">
              {tested.error}
            </Typography>
          )}
          {tested.cells.map((c) => (
            <Stack
              key={c.row}
              direction="row"
              gap={1}
              sx={{ fontFamily: MONO, fontSize: 12.5 }}
            >
              <Box sx={{ width: 64, color: "text.secondary" }}>Row {c.row}</Box>
              <Box
                sx={{
                  color: c.status === "error" ? "error.main" : "text.primary",
                }}
              >
                {c.status === "error"
                  ? `${CELL_ERROR_PREFIX}${c.error}`
                  : c.value === null || c.value === undefined
                    ? "—"
                    : typeof c.value === "string"
                      ? c.value
                      : JSON.stringify(c.value)}
              </Box>
            </Stack>
          ))}
        </Box>
      )}
      {children}
      <Stack direction="row" justifyContent="flex-end" gap={1}>
        {onCancel && (
          <Button size="small" onClick={onCancel}>
            Cancel
          </Button>
        )}
        <Button
          size="small"
          variant="contained"
          disabled={!ready || creating}
          onClick={() => onCreate?.({ name: name.trim(), expr: expr.trim() })}
          data-testid="expression-column-create"
        >
          {submitLabel}
        </Button>
      </Stack>
    </Stack>
  );
};

ExpressionColumnForm.propTypes = {
  columns: PropTypes.array,
  rows: PropTypes.array,
  test: PropTypes.func,
  onCreate: PropTypes.func,
  onCancel: PropTypes.func,
  creating: PropTypes.bool,
  initialName: PropTypes.string,
  initialExpr: PropTypes.string,
  submitLabel: PropTypes.string,
  children: PropTypes.node,
};

export default ExpressionColumnForm;
