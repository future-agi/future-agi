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
import TemplateRollupRow from "./TemplateRollupRow";
import TemplateSingleRow from "./TemplateSingleRow";
import TraceVerdictRow from "./TraceVerdictRow";
import { NAME_W, groupByTaskTemplate, isPassed } from "./utils";

const EvalRollupSection = ({
  evals,
  evalResults = {},
  scope = "trace",
  emptyMessage,
  onSelectSpan,
  onFixWithFalcon,
}) => {
  const [search, setSearch] = useState("");
  const list = useMemo(() => (Array.isArray(evals) ? evals : []), [evals]);
  const groups = useMemo(() => groupByTaskTemplate(list), [list]);
  const filteredGroups = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return groups;
    return groups
      .map((task) => {
        const taskMatch = task.name.toLowerCase().includes(q);
        const templates = task.templates.filter(
          (tpl) =>
            taskMatch ||
            tpl.name.toLowerCase().includes(q) ||
            tpl.rows.some((r) => (r.spanName || "").toLowerCase().includes(q)),
        );
        return { ...task, templates };
      })
      .filter((task) => task.templates.length > 0);
  }, [groups, search]);

  // Summary covers pass_fail + numeric results only (choices have no pass/fail).
  const scored = useMemo(
    () =>
      list.filter((e) => {
        const k = resolveEvalKind({ outputType: e.output_type });
        return k === EVAL_KIND.PASS_FAIL || k === EVAL_KIND.NUMERIC;
      }),
    [list],
  );
  const failing = useMemo(() => scored.filter((e) => !isPassed(e)), [scored]);
  const passed = scored.length - failing.length;
  const passRate = scored.length
    ? Math.round((passed / scored.length) * 100)
    : 0;

  if (list.length === 0) {
    return (
      <Box sx={{ textAlign: "center", py: 4, color: "text.secondary" }}>
        <Iconify icon="mdi:chart-box-outline" width={32} sx={{ mb: 1, opacity: 0.4 }} />
        <Typography variant="body2" fontSize={12}>
          {emptyMessage || "No evaluations available"}
        </Typography>
      </Box>
    );
  }

  const renderTemplate = (task, tpl) => {
    if (scope === "span")
      return (
        <TemplateSingleRow
          key={tpl.id}
          template={tpl}
          onFixWithFalcon={onFixWithFalcon}
        />
      );
    if (task.rowType === "traces")
      return (
        <TraceVerdictRow
          key={tpl.id}
          template={tpl}
          rollup={evalResults[tpl.id]}
          onFixWithFalcon={onFixWithFalcon}
        />
      );
    return (
      <TemplateRollupRow
        key={tpl.id}
        template={tpl}
        rollup={evalResults[tpl.id]}
        onSelectSpan={onSelectSpan}
        onFixWithFalcon={onFixWithFalcon}
      />
    );
  };

  return (
    <Box sx={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <SummaryBar
        passed={passed}
        total={scored.length}
        failedCount={failing.length}
        passRate={passRate}
        onFix={
          scope !== "span" && failing.length > 0 && onFixWithFalcon
            ? () =>
                onFixWithFalcon({
                  level: "span",
                  failingEvals: failing,
                  allEvals: list,
                })
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

        {filteredGroups.length === 0 ? (
          <Box sx={{ textAlign: "center", py: 3, fontSize: 12, color: "text.secondary" }}>
            No evals match your search
          </Box>
        ) : (
          filteredGroups.map((task) => (
            <Box key={task.id}>
              <TaskHeader name={task.name} rowType={task.rowType} />
              {task.templates.map((tpl) => renderTemplate(task, tpl))}
            </Box>
          ))
        )}
      </Box>
    </Box>
  );
};

EvalRollupSection.propTypes = {
  evals: PropTypes.array,
  evalResults: PropTypes.object,
  scope: PropTypes.oneOf(["trace", "span"]),
  emptyMessage: PropTypes.string,
  onSelectSpan: PropTypes.func,
  onFixWithFalcon: PropTypes.func,
};

export default EvalRollupSection;
