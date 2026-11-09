import React, { useEffect, useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import Alert from "@mui/material/Alert";
import { GatewayStatusChip } from "../../components/frame";
import SectionTitle from "../components/SectionTitle";
import { limitsPatch, limitsRows } from "./toolLimits";

// Edits only non-dollar limits; the enclosing drawer owns confirmation/save.
export default function ToolLimitsFields({ budgets, onChange }) {
  const [rpm, setRpm] = useState(
    budgets?.rpm_per_key == null ? "" : String(budgets.rpm_per_key),
  );
  const [calls, setCalls] = useState(
    budgets?.max_tool_calls_per_run == null
      ? ""
      : String(budgets.max_tool_calls_per_run),
  );
  const [rows, setRows] = useState(() => limitsRows(budgets));
  const [error, setError] = useState("");
  useEffect(() => {
    setRpm(budgets?.rpm_per_key == null ? "" : String(budgets.rpm_per_key));
    setCalls(
      budgets?.max_tool_calls_per_run == null
        ? ""
        : String(budgets.max_tool_calls_per_run),
    );
    setRows(limitsRows(budgets));
    setError("");
  }, [budgets]);
  const change = (nextRpm, nextCalls, nextRows) => {
    setRpm(nextRpm);
    setCalls(nextCalls);
    setRows(nextRows);
    try {
      const patch = limitsPatch(nextRpm, nextCalls, nextRows, budgets);
      setError("");
      onChange(patch, "");
    } catch (err) {
      setError(err.message);
      onChange(undefined, err.message);
    }
  };
  const active =
    rpm !== "" ||
    calls !== "" ||
    rows.some((row) => row.rpm !== "" || row.calls !== "");
  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
      <SectionTitle
        title="Tool call limits"
        note="Applied after Save and a successful gateway sync. No limits are set by default."
        component="h3"
      />
      <Box>
        <GatewayStatusChip
          tone="neutral"
          label={active ? "Limits configured" : "Limits off"}
        />
      </Box>
      <Typography typography="s2" color="text.secondary">
        Blank means no limit; zero blocks calls. Rates use a shared 60-second
        window. Run counts require a run id or budget scope and clear once the
        run is idle for its budget scope TTL (24 hours by default). Redis
        unavailable means configured calls are refused.
      </Typography>
      <TextField
        size="small"
        label="Calls per minute per key"
        value={rpm}
        onChange={(e) => change(e.target.value, calls, rows)}
        inputProps={{ inputMode: "numeric" }}
      />
      <TextField
        size="small"
        label="Calls per run"
        value={calls}
        onChange={(e) => change(rpm, e.target.value, rows)}
        inputProps={{ inputMode: "numeric" }}
      />
      {rows.map((row, index) => (
        <Box
          key={index}
          sx={{
            display: "flex",
            flexDirection: "column",
            gap: 1,
            border: "1px solid",
            borderColor: "divider",
            p: 1.5,
          }}
        >
          <TextField
            size="small"
            label="Tool pattern"
            value={row.pattern}
            placeholder="server/*"
            onChange={(e) =>
              change(
                rpm,
                calls,
                rows.map((r, i) =>
                  i === index ? { ...r, pattern: e.target.value } : r,
                ),
              )
            }
          />
          <TextField
            size="small"
            label="Calls per minute per principal and tool"
            value={row.rpm}
            onChange={(e) =>
              change(
                rpm,
                calls,
                rows.map((r, i) =>
                  i === index ? { ...r, rpm: e.target.value } : r,
                ),
              )
            }
            inputProps={{ inputMode: "numeric" }}
          />
          <TextField
            size="small"
            label="Calls per run matching this pattern"
            value={row.calls}
            onChange={(e) =>
              change(
                rpm,
                calls,
                rows.map((r, i) =>
                  i === index ? { ...r, calls: e.target.value } : r,
                ),
              )
            }
            inputProps={{ inputMode: "numeric" }}
          />
          <Button
            size="small"
            onClick={() =>
              change(
                rpm,
                calls,
                rows.filter((_, i) => i !== index),
              )
            }
          >
            Remove rule
          </Button>
        </Box>
      ))}
      <Button
        size="small"
        variant="outlined"
        onClick={() =>
          change(rpm, calls, [...rows, { pattern: "", rpm: "", calls: "" }])
        }
      >
        Add tool rule
      </Button>
      {error && <Alert severity="error">{error}</Alert>}
    </Box>
  );
}
ToolLimitsFields.propTypes = {
  budgets: PropTypes.object,
  onChange: PropTypes.func.isRequired,
};
