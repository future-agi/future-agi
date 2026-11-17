// W7 A2 (W7-3): a decision tree's definition — output type and pass bar,
// the outline (one card per node), the read-only graph, the reasons it
// cannot be saved yet (dagModel.validateDag, verbatim) and Test on a row.
// The create editor and the Details tab both mount it; they own Save.
import React, { useMemo, useState } from "react";
import { createPortal } from "react-dom";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { useEvalsList } from "src/sections/evals/hooks/useEvalsList";
import { numbersOf, validateDag } from "src/sections/common/EvalTypes/dagModel";
import DagGraph from "./DagGraph";
import DagNodeCard from "./DagNodeCard";
import DagTestPanel from "./DagTestPanel";
import { newNode } from "./dagEditorModel";

const fieldSx = { "& .MuiInputBase-root": { fontSize: "13px" } };

export default function DagDefinitionEditor({
  dag,
  onChange,
  threshold,
  onThresholdChange,
  disabled = false,
  asideTarget = null,
  templateId = null,
}) {
  const [selected, setSelected] = useState(dag?.root ?? null);
  const [path, setPath] = useState(null);
  const { data: list } = useEvalsList({ pageSize: 100 });
  const evals = useMemo(
    () =>
      (list?.items ?? [])
        .filter((e) => e.eval_type !== "dag" && e.template_type !== "composite")
        .map((e) => ({ id: e.id, name: e.name })),
    [list],
  );
  const nums = numbersOf(dag);
  const errors = validateDag(dag);
  const passFail = dag.output_type === "pass_fail";
  // The graph and Test on a row: beside the outline (the create page's
  // right panel) when the page gives a place for them, else below it.
  const side = (
    <Stack gap={1.5}>
      <DagGraph dag={dag} selected={selected} path={path} />
      <DagTestPanel
        dag={dag}
        templateId={templateId}
        threshold={passFail ? 0.5 : threshold ?? 0.5}
        onPath={setPath}
      />
    </Stack>
  );
  return (
    <Stack gap={1.5} data-testid="dag-editor">
      <Stack direction="row" gap={1} alignItems="center">
        <TextField
          select
          size="small"
          label="Output"
          value={dag.output_type ?? "score"}
          disabled={disabled}
          sx={{ ...fieldSx, width: 170 }}
          onChange={(e) => onChange({ ...dag, output_type: e.target.value })}
        >
          <MenuItem value="score">Score (0–1)</MenuItem>
          <MenuItem value="pass_fail">Pass / Fail</MenuItem>
        </TextField>
        {!passFail ? (
          <TextField
            size="small"
            type="number"
            label="Pass at ≥"
            value={threshold ?? ""}
            disabled={disabled}
            sx={{ ...fieldSx, width: 120 }}
            inputProps={{ min: 0, max: 1, step: 0.05 }}
            onChange={(e) =>
              onThresholdChange?.(
                e.target.value === "" ? null : Number(e.target.value),
              )
            }
          />
        ) : (
          <Typography typography="s3" color="text.secondary">
            A score of 0.5 or more reads as Pass (1), below as Fail (0).
          </Typography>
        )}
      </Stack>
      <Stack gap={1}>
        {dag.nodes.map((n) => (
          <DagNodeCard
            key={n.id}
            dag={dag}
            node={n}
            nums={nums}
            evals={evals}
            onChange={onChange}
            selected={selected === n.id}
            onSelect={setSelected}
            disabled={disabled}
          />
        ))}
        {!disabled ? (
          <Stack direction="row" gap={1}>
            {[
              ["judge", "Add question"],
              ["check", "Add check"],
              ["eval", "Add eval"],
              ["leaf", "Add score"],
            ].map(([kind, label]) => (
              <Button
                key={kind}
                size="small"
                variant="outlined"
                sx={{ textTransform: "none", fontSize: "12px" }}
                startIcon={<Iconify icon="mingcute:add-line" width={14} />}
                onClick={() =>
                  onChange({
                    ...dag,
                    nodes: [...dag.nodes, newNode(dag, kind)],
                  })
                }
              >
                {label}
              </Button>
            ))}
          </Stack>
        ) : null}
      </Stack>
      {errors.length ? (
        <Box data-testid="dag-errors">
          {errors.map((e) => (
            <Typography key={e} typography="s3" color="error.main">
              {e}
            </Typography>
          ))}
        </Box>
      ) : null}
      {asideTarget ? createPortal(side, asideTarget) : side}
    </Stack>
  );
}

DagDefinitionEditor.propTypes = {
  dag: PropTypes.object.isRequired,
  onChange: PropTypes.func.isRequired,
  threshold: PropTypes.number,
  onThresholdChange: PropTypes.func,
  disabled: PropTypes.bool,
  asideTarget: PropTypes.object,
  templateId: PropTypes.string,
};
