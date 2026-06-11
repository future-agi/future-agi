import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import {
  resolveEvalKind,
  EVAL_KIND,
} from "src/sections/projects/LLMTracing/evalCellModel";
import SummaryBar from "./SummaryBar";
import SearchBar from "./SearchBar";
import TaskHeader from "./TaskHeader";
import EvalRollupRow from "./EvalRollupRow";
import EvalSingleRow from "./EvalSingleRow";
import { NAME_W } from "./utils";

// Renders the backend's pre-grouped, pre-aggregated eval_scores object directly
// (no client-side grouping or rollup).
const EvalRollupSection = ({
  evalScores,
  emptyMessage,
  onSelectSpan,
  onFixWithFalcon,
}) => {
  const [search, setSearch] = useState("");
  const scope = evalScores?.scope === "span" ? "span" : "trace";
  const tasks = useMemo(() => evalScores?.eval_tasks || [], [evalScores]);

  const filteredTasks = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return tasks;
    return tasks
      .map((task) => {
        const taskMatch = (task.eval_task_name || "").toLowerCase().includes(q);
        const evals = (task.evals || []).filter(
          (ev) =>
            taskMatch ||
            (ev.eval_name || "").toLowerCase().includes(q) ||
            (ev.spans || []).some((s) =>
              (s.span_name || "").toLowerCase().includes(q),
            ),
        );
        return { ...task, evals };
      })
      .filter((task) => task.evals.length > 0);
  }, [tasks, search]);

  // Summary covers pass_fail + numeric (choices have no pass/fail). Counts are
  // span-level: Pass/Fail uses the aggregate counts; score counts per span.
  const { passed, failed, total, passRate, failingEvals } = useMemo(() => {
    let p = 0;
    let f = 0;
    const failing = [];
    for (const task of tasks) {
      for (const ev of task.evals || []) {
        const kind = resolveEvalKind({ outputType: ev.output_type });
        if (kind === EVAL_KIND.PASS_FAIL) {
          p += ev.aggregate?.pass || 0;
          f += ev.aggregate?.fail || 0;
          if (ev.aggregate?.fail) failing.push(ev);
        } else if (kind === EVAL_KIND.NUMERIC) {
          for (const s of ev.spans || []) {
            if (s.error || typeof s.value !== "number") continue;
            if (s.value >= 50) p += 1;
            else {
              f += 1;
              failing.push(ev);
            }
          }
        }
      }
    }
    const t = p + f;
    return {
      passed: p,
      failed: f,
      total: t,
      passRate: t ? Math.round((p / t) * 100) : 0,
      failingEvals: failing,
    };
  }, [tasks]);

  const hasEvals = tasks.some((t) => (t.evals || []).length > 0);
  if (!hasEvals) {
    return (
      <Box sx={{ textAlign: "center", py: 4, color: "text.secondary" }}>
        <Iconify
          icon="mdi:chart-box-outline"
          width={32}
          sx={{ mb: 1, opacity: 0.4 }}
        />
        <Typography variant="body2" fontSize={12}>
          {emptyMessage || "No evaluations available"}
        </Typography>
      </Box>
    );
  }

  return (
    <Box sx={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <SummaryBar
        passed={passed}
        total={total}
        failedCount={failed}
        passRate={passRate}
        onFix={
          scope !== "span" && failed > 0 && onFixWithFalcon
            ? () =>
                onFixWithFalcon({ level: "span", passed, total, failingEvals })
            : undefined
        }
      />
      <SearchBar value={search} onChange={setSearch} />

      <Box sx={{ flex: 1, overflow: "auto" }}>
        <Box
          sx={{
            position: "sticky",
            top: 0,
            zIndex: 2,
            display: "flex",
            alignItems: "center",
            gap: 1,
            px: 1.5,
            py: 0.5,
            bgcolor: "background.paper",
            borderBottom: "1px solid",
            borderColor: "divider",
          }}
        >
          <Box sx={{ width: 18, flexShrink: 0, display: "flex", alignItems: "center" }}>
            <Iconify
              icon="mdi:checkbox-marked-circle-outline"
              width={12}
              color="text.secondary"
            />
          </Box>
          <Typography
            sx={{ width: NAME_W, fontSize: 11, fontWeight: 600, color: "text.secondary" }}
          >
            Evaluation metric
          </Typography>
          <Typography
            sx={{ flex: 1, fontSize: 11, fontWeight: 600, color: "text.secondary" }}
          >
            Score
          </Typography>
        </Box>

        {filteredTasks.length === 0 ? (
          <Box sx={{ textAlign: "center", py: 3, fontSize: 12, color: "text.secondary" }}>
            No evals match your search
          </Box>
        ) : (
          filteredTasks.map((task) => (
            <Box key={task.eval_task_id || task.eval_task_name}>
              <TaskHeader name={task.eval_task_name} />
              {task.evals.map((ev) =>
                scope === "span" ? (
                  <EvalSingleRow
                    key={ev.eval_config_id}
                    ev={ev}
                    onFixWithFalcon={onFixWithFalcon}
                  />
                ) : (
                  <EvalRollupRow
                    key={ev.eval_config_id}
                    ev={ev}
                    onSelectSpan={onSelectSpan}
                    onFixWithFalcon={onFixWithFalcon}
                  />
                ),
              )}
            </Box>
          ))
        )}
      </Box>
    </Box>
  );
};

EvalRollupSection.propTypes = {
  evalScores: PropTypes.object,
  emptyMessage: PropTypes.string,
  onSelectSpan: PropTypes.func,
  onFixWithFalcon: PropTypes.func,
};

export default EvalRollupSection;
