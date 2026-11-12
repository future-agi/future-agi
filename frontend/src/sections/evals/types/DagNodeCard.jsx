// W7 A2 (W7-3): one node of a decision tree in the outline editor — its kind,
// question (yes/no or choice) and judge, check, eval or score, and per
// answer a "Goes to" select (an existing node, or a new question, check,
// eval or score).
import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Chip,
  FormControlLabel,
  IconButton,
  MenuItem,
  Stack,
  Switch,
  TextField,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import ModelSelector from "src/sections/evals/components/ModelSelector";
import { isDecisionModel } from "src/sections/common/DecisionJudges/decisionModels";
import {
  CHOICE_OPTIONS,
  NODE_KINDS,
  edgesOf,
  isChoice,
} from "src/sections/common/EvalTypes/dagModel";
import DagCheckFields from "./DagCheckFields";
import {
  addOption,
  nodeLabel,
  pointEdge,
  removeNode,
  removeOption,
  setChoice,
  updateEdge,
  updateNode,
} from "./dagEditorModel";

const fieldSx = { "& .MuiInputBase-root": { fontSize: "13px" } };
const NEW_TARGETS = [
  { value: "new:judge", label: "New question" },
  { value: "new:check", label: "New check" },
  { value: "new:eval", label: "New eval" },
  { value: "new:leaf", label: "New score" },
];

export default function DagNodeCard({
  dag,
  node,
  nums,
  evals,
  onChange,
  selected,
  onSelect,
  disabled,
}) {
  const set = (patch) => onChange(updateNode(dag, node.id, patch));
  const choice = isChoice(node);
  // The decision model last refused on this choice question (its note).
  const [decisionRefused, setDecisionRefused] = useState(null);
  const isRoot = dag.root === node.id;
  const targets = dag.nodes.filter((n) => n.id !== node.id);
  return (
    <Box
      data-testid="dag-node"
      data-node-id={node.id}
      onClick={() => onSelect?.(node.id)}
      sx={{
        p: 1.25,
        border: "1px solid",
        borderColor: selected ? "primary.main" : "divider",
        borderRadius: 1,
      }}
    >
      <Stack direction="row" alignItems="center" gap={1} sx={{ mb: 1 }}>
        <Typography typography="s2" fontWeight={700} color="primary.main">
          {node.kind === "leaf" ? "Score" : `#${nums[node.id]}`}
        </Typography>
        {isRoot ? <Chip size="small" label="First question" /> : null}
        <TextField
          select
          size="small"
          value={node.kind}
          disabled={disabled}
          sx={{ ...fieldSx, width: 130 }}
          inputProps={{ "aria-label": "Node kind" }}
          onChange={(e) => set({ kind: e.target.value })}
        >
          {NODE_KINDS.map((k) => (
            <MenuItem key={k.value} value={k.value}>
              {k.label}
            </MenuItem>
          ))}
        </TextField>
        <Box sx={{ flex: 1 }} />
        {!isRoot ? (
          <IconButton
            size="small"
            aria-label="Remove node"
            disabled={disabled}
            onClick={(e) => {
              e.stopPropagation();
              onChange(removeNode(dag, node.id));
            }}
          >
            <Iconify icon="solar:trash-bin-trash-bold" width={16} />
          </IconButton>
        ) : null}
      </Stack>

      {node.kind === "judge" ? (
        <Stack gap={1}>
          <TextField
            size="small"
            multiline
            minRows={1}
            label="Question"
            value={node.question ?? ""}
            disabled={disabled}
            sx={fieldSx}
            placeholder="Does the reply say a refund was issued?"
            onChange={(e) => set({ question: e.target.value })}
          />
          <Stack direction="row" alignItems="center" gap={1} flexWrap="wrap">
            <FormControlLabel
              label={<Typography typography="s3">Choice question</Typography>}
              control={
                <Switch
                  size="small"
                  checked={choice}
                  disabled={disabled}
                  onChange={(e) =>
                    onChange(setChoice(dag, node.id, e.target.checked))
                  }
                />
              }
            />
            <Typography typography="s3" color="text.secondary">
              Judge
            </Typography>
            <ModelSelector
              model={node.judge_model ?? ""}
              disabled={disabled}
              showMode={false}
              showPlus={false}
              hideDatasetContextToggle
              onModelChange={(m) => {
                // A choice question asks an LLM judge about each option;
                // a decision model judges the tree's yes/no questions. Say
                // so rather than keep the pick.
                const refused = Boolean(choice && isDecisionModel(m));
                setDecisionRefused(refused ? m : null);
                if (!refused) set({ judge_model: m || undefined });
              }}
            />
            {decisionRefused && choice ? (
              <Typography
                typography="s3"
                color="warning.dark"
                data-testid="dag-choice-decision-note"
              >
                {`${decisionRefused} judges this tree's yes/no questions; a choice question asks an LLM judge about each option.`}
              </Typography>
            ) : null}
            {choice ? (
              <Typography typography="s3" color="text.secondary">
                Each option is asked on its own; the highest wins, a tie goes to
                the first.
              </Typography>
            ) : null}
          </Stack>
        </Stack>
      ) : null}
      {node.kind === "check" ? (
        <DagCheckFields
          node={node}
          disabled={disabled}
          onCheck={(p) => set({ check: { ...(node.check ?? {}), ...p } })}
        />
      ) : null}
      {node.kind === "eval" ? (
        <TextField
          select
          size="small"
          label="Eval"
          value={node.template_id ?? ""}
          disabled={disabled}
          sx={{ ...fieldSx, minWidth: 240 }}
          onChange={(e) => set({ template_id: e.target.value })}
        >
          {evals.map((ev) => (
            <MenuItem key={ev.id} value={ev.id}>
              {ev.name}
            </MenuItem>
          ))}
        </TextField>
      ) : null}
      {node.kind === "leaf" ? (
        <Stack direction="row" gap={1}>
          <TextField
            size="small"
            label="Label"
            value={node.label ?? ""}
            disabled={disabled}
            sx={fieldSx}
            onChange={(e) => set({ label: e.target.value })}
          />
          <TextField
            size="small"
            type="number"
            label="Score"
            value={node.score ?? ""}
            disabled={disabled}
            sx={{ ...fieldSx, width: 110 }}
            inputProps={{ min: 0, max: 1, step: 0.1 }}
            onChange={(e) =>
              set({
                score: e.target.value === "" ? "" : Number(e.target.value),
              })
            }
          />
        </Stack>
      ) : null}

      {node.kind !== "leaf" ? (
        <Stack gap={0.75} sx={{ mt: 1 }}>
          {edgesOf(node).map((a, i) => (
            <Stack
              key={`${node.id}-${i}`}
              direction="row"
              alignItems="center"
              gap={1}
            >
              {choice ? (
                <TextField
                  size="small"
                  value={a.label}
                  disabled={disabled}
                  sx={{
                    ...fieldSx,
                    flex: "1 1 220px",
                    minWidth: 160,
                    maxWidth: 360,
                  }}
                  inputProps={{ "aria-label": `Option ${i + 1}` }}
                  onChange={(e) =>
                    onChange(
                      updateEdge(dag, node.id, i, { label: e.target.value }),
                    )
                  }
                />
              ) : (
                <Typography typography="s2" sx={{ width: 50 }}>
                  {a.label}
                </Typography>
              )}
              <TextField
                select
                size="small"
                label="Goes to"
                value={a.next || ""}
                disabled={disabled}
                sx={{ ...fieldSx, minWidth: 260 }}
                inputProps={{
                  "aria-label": `${nums[node.id]} ${a.label} goes to`,
                }}
                onChange={(e) =>
                  onChange(pointEdge(dag, node.id, i, e.target.value))
                }
              >
                {targets.map((n) => (
                  <MenuItem key={n.id} value={n.id}>
                    {nodeLabel(dag, n, nums)}
                  </MenuItem>
                ))}
                {NEW_TARGETS.map((t) => (
                  <MenuItem key={t.value} value={t.value}>
                    {t.label}
                  </MenuItem>
                ))}
              </TextField>
              {choice && edgesOf(node).length > CHOICE_OPTIONS.min ? (
                <IconButton
                  size="small"
                  aria-label="Remove option"
                  disabled={disabled}
                  onClick={() => onChange(removeOption(dag, node.id, i))}
                >
                  <Iconify icon="mingcute:close-line" width={14} />
                </IconButton>
              ) : null}
            </Stack>
          ))}
          {choice && edgesOf(node).length < CHOICE_OPTIONS.max ? (
            <Typography
              component="button"
              type="button"
              typography="s3"
              color="primary.main"
              sx={{
                border: 0,
                background: "none",
                cursor: "pointer",
                alignSelf: "flex-start",
                p: 0,
              }}
              onClick={() => onChange(addOption(dag, node.id))}
              disabled={disabled}
            >
              Add option
            </Typography>
          ) : null}
        </Stack>
      ) : null}
    </Box>
  );
}

DagNodeCard.propTypes = {
  dag: PropTypes.object.isRequired,
  node: PropTypes.object.isRequired,
  nums: PropTypes.object.isRequired,
  evals: PropTypes.array.isRequired,
  onChange: PropTypes.func.isRequired,
  selected: PropTypes.bool,
  onSelect: PropTypes.func,
  disabled: PropTypes.bool,
};
