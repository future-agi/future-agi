import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import { useNavigate } from "react-router-dom";
import {
  Box,
  ButtonBase,
  MenuItem,
  Select,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Typography,
} from "@mui/material";
import { alpha, useTheme } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import AnalysisCard from "src/sections/common/Analysis/AnalysisCard";
import { rowsPill } from "src/sections/common/Results/filterPills";
import {
  datasetGridHref,
  resultsGridHref,
} from "src/sections/common/Analysis/analysisLinks";
import JudgePerLabelLine from "src/sections/evals/alignment/JudgePerLabelLine";
import { num, pct } from "./summaryApi";

const tabular = { fontVariantNumeric: "tabular-nums" };
// A judge review (R2D-05) has no dataset eval / label column: key it by its
// metric and review so the pickers never show a blank value.
const evalKeyOf = (p) => p.eval_id ?? p.metric_key ?? p.eval_name;
const labelKeyOf = (p) => p.label_id ?? p.review_id ?? p.label_name;
const pairKey = (p) => `${evalKeyOf(p)}|${labelKeyOf(p)}`;
const selectSx = {
  typography: "s2",
  height: 30,
  "& .MuiSelect-select": { py: 0.5, pl: 1.25 },
};

/** Pick the default pair: the first eval in dataset order, most rows. */
export function defaultPair(pairs, evalOrder = []) {
  if (!pairs?.length) return null;
  for (const id of evalOrder) {
    const hit = pairs
      .filter((p) => evalKeyOf(p) === id)
      .sort((a, b) => b.n - a.n)[0];
    if (hit) return hit;
  }
  return pairs[0];
}

function MatrixCell({
  cell,
  agree,
  judge,
  human,
  evalName,
  labelName,
  onOpen,
}) {
  const theme = useTheme();
  const tone = agree ? theme.palette.green[500] : theme.palette.red[500];
  const count = cell?.count ?? 0;
  const describe = `${evalName} ${judge} · human ${human === "up" ? "👍" : "👎"}`;
  return (
    <CustomTooltip
      show
      size="small"
      arrow
      title={
        count
          ? `${count} ${count === 1 ? "row" : "rows"}: ${describe}${agree ? "" : " (disagree)"} · open in Data`
          : `No rows: ${describe}`
      }
    >
      <span>
        <ButtonBase
          disabled={!count}
          onClick={onOpen}
          aria-label={`${count} rows where ${describe}`}
          sx={{
            width: "100%",
            height: 64,
            borderRadius: 1,
            flexDirection: "column",
            gap: 0.25,
            bgcolor: alpha(tone, agree ? 0.14 : 0.1),
            border: "1px solid",
            borderColor: alpha(tone, 0.3),
            "&:hover": { bgcolor: alpha(tone, agree ? 0.22 : 0.18) },
            "&.Mui-disabled": { opacity: 0.6 },
          }}
        >
          <Typography typography="m3" fontWeight={600} sx={tabular}>
            {count}
          </Typography>
          <Typography typography="s3" color="text.secondary">
            {agree ? "Agree" : "Disagree"}
          </Typography>
        </ButtonBase>
      </span>
    </CustomTooltip>
  );
}

MatrixCell.propTypes = {
  cell: PropTypes.object,
  agree: PropTypes.bool,
  judge: PropTypes.string,
  human: PropTypes.string,
  evalName: PropTypes.string,
  labelName: PropTypes.string,
  onOpen: PropTypes.func,
};

/**
 * JudgeHumanMatrix — an eval (LLM judge) against a 👍/👎 human label on the
 * same rows: 2×2 confusion, agreement, Cohen's κ, n. Every cell opens the
 * Data tab filtered to exactly its rows. Below, all pairs, lowest κ first.
 */
const JudgeHumanMatrix = ({ pairs, datasetId, version, evalOrder }) => {
  const navigate = useNavigate();
  const [selected, setSelected] = useState(null);
  const current = useMemo(() => {
    const byKey = selected && pairs.find((p) => pairKey(p) === selected);
    return byKey ?? defaultPair(pairs, evalOrder);
  }, [pairs, selected, evalOrder]);

  const evals = useMemo(() => {
    const seen = new Map();
    pairs.forEach((p) => seen.set(evalKeyOf(p), p.eval_name));
    return [...seen.entries()];
  }, [pairs]);
  const labels = useMemo(
    () =>
      current ? pairs.filter((p) => evalKeyOf(p) === evalKeyOf(current)) : [],
    [pairs, current],
  );
  const ranked = useMemo(
    () => [...pairs].sort((a, b) => (a.kappa ?? 0) - (b.kappa ?? 0)),
    [pairs],
  );

  if (!current) return null;
  // Cells from a judge review (no grid filter) open the reviewed run's rows
  // by id, so the count on the cell equals the rows that open.
  const open = (cell) => () =>
    navigate(
      !cell?.filters?.length && cell?.row_ids?.length && current?.run_id
        ? resultsGridHref({
            mode: "compare",
            datasetId,
            runIds: [current.run_id],
            pills: [
              rowsPill(cell.row_ids, {
                label: `${current.eval_name} vs ${current.label_name}`,
              }),
            ],
          })
        : datasetGridHref(datasetId, cell?.filters ?? [], { version }),
    );
  const judgeText = (pass) =>
    current.threshold != null
      ? pass
        ? `≥ ${num(current.threshold)}`
        : `< ${num(current.threshold)}`
      : pass
        ? "Pass"
        : "Fail";

  return (
    <AnalysisCard
      icon="mdi:scale-balance"
      title="Judge vs human"
      subtitle="Does the eval agree with your reviewers on the same rows? Click a cell to open its rows."
      actions={
        <Stack direction="row" gap={1} alignItems="center">
          {evals.length > 1 ? (
            <Select
              size="small"
              value={evalKeyOf(current)}
              inputProps={{ "aria-label": "Eval" }}
              onChange={(e) => {
                const next = defaultPair(
                  pairs.filter((p) => evalKeyOf(p) === e.target.value),
                );
                if (next) setSelected(pairKey(next));
              }}
              sx={{ ...selectSx, minWidth: 150 }}
              renderValue={(v) =>
                `Eval: ${evals.find(([id]) => id === v)?.[1] ?? ""}`
              }
            >
              {evals.map(([id, name]) => (
                <MenuItem key={id} value={id} sx={{ typography: "s2" }}>
                  {name}
                </MenuItem>
              ))}
            </Select>
          ) : null}
          {labels.length > 1 ? (
            <Select
              size="small"
              value={labelKeyOf(current)}
              inputProps={{ "aria-label": "Human label" }}
              onChange={(e) =>
                setSelected(`${evalKeyOf(current)}|${e.target.value}`)
              }
              sx={{ ...selectSx, minWidth: 150 }}
              renderValue={(v) =>
                `Label: ${labels.find((p) => labelKeyOf(p) === v)?.label_name ?? ""}`
              }
            >
              {labels.map((p) => (
                <MenuItem
                  key={labelKeyOf(p)}
                  value={labelKeyOf(p)}
                  sx={{ typography: "s2" }}
                >
                  {p.label_name}
                </MenuItem>
              ))}
            </Select>
          ) : null}
          {evals.length <= 1 && labels.length <= 1 ? (
            <Typography typography="s2" color="text.secondary" noWrap>
              {current.eval_name} · {current.label_name}
            </Typography>
          ) : null}
        </Stack>
      }
    >
      <Stack
        direction={{ xs: "column", md: "row" }}
        gap={3}
        alignItems="flex-start"
      >
        <Box sx={{ minWidth: 320, maxWidth: 380, flex: "0 0 auto" }}>
          <Stack direction="row" gap={2} sx={{ mb: 1.5 }} alignItems="baseline">
            <Typography typography="m3" fontWeight={600} sx={tabular}>
              {pct(current.agreement_pct, 0)}
            </Typography>
            <Typography typography="s2" color="text.secondary" sx={tabular}>
              agreement · κ {num(current.kappa)} · n={current.n}
            </Typography>
          </Stack>
          <Box
            sx={{
              display: "grid",
              gridTemplateColumns: "92px 1fr 1fr",
              gap: 0.75,
              alignItems: "center",
            }}
          >
            <span />
            <Typography typography="s3" color="text.secondary" align="center">
              Human 👍 {current.label_name}
            </Typography>
            <Typography typography="s3" color="text.secondary" align="center">
              Human 👎 {current.label_name}
            </Typography>
            {[true, false].map((pass) => (
              <React.Fragment key={String(pass)}>
                <Typography typography="s3" color="text.secondary">
                  {current.eval_name} {judgeText(pass)}
                </Typography>
                {["up", "down"].map((vote) => {
                  const cell =
                    current.matrix?.[`${pass ? "pass" : "fail"}|${vote}`];
                  return (
                    <MatrixCell
                      key={vote}
                      cell={cell}
                      agree={
                        (pass && vote === "up") || (!pass && vote === "down")
                      }
                      judge={judgeText(pass)}
                      human={vote}
                      evalName={current.eval_name}
                      labelName={current.label_name}
                      onOpen={open(cell)}
                    />
                  );
                })}
              </React.Fragment>
            ))}
          </Box>
          <JudgePerLabelLine pair={current} datasetId={datasetId} />
        </Box>
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Typography typography="s2" fontWeight={600} sx={{ mb: 0.5 }}>
            All pairs · lowest κ first
          </Typography>
          <Table
            size="small"
            sx={{ "& td, & th": { typography: "s2", borderColor: "divider" } }}
          >
            <TableHead>
              <TableRow sx={{ bgcolor: "background.neutral" }}>
                <TableCell sx={{ fontWeight: 600 }}>Eval</TableCell>
                <TableCell sx={{ fontWeight: 600 }}>Label</TableCell>
                <TableCell align="right" sx={{ fontWeight: 600 }}>
                  n
                </TableCell>
                <TableCell align="right" sx={{ fontWeight: 600 }}>
                  Agreement
                </TableCell>
                <TableCell align="right" sx={{ fontWeight: 600 }}>
                  κ
                </TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {ranked.map((p) => {
                const active = pairKey(p) === pairKey(current);
                return (
                  <TableRow
                    key={pairKey(p)}
                    hover
                    selected={active}
                    onClick={() => setSelected(pairKey(p))}
                    sx={{ cursor: "pointer" }}
                  >
                    <TableCell>
                      <Stack direction="row" gap={0.5} alignItems="center">
                        {active && (
                          <Iconify
                            icon="mingcute:right-fill"
                            width={14}
                            sx={{ color: "primary.main" }}
                          />
                        )}
                        {p.eval_name}
                      </Stack>
                    </TableCell>
                    <TableCell>{p.label_name}</TableCell>
                    <TableCell align="right" sx={tabular}>
                      {p.n}
                    </TableCell>
                    <TableCell align="right" sx={tabular}>
                      {pct(p.agreement_pct, 0)}
                    </TableCell>
                    <TableCell align="right" sx={tabular}>
                      {num(p.kappa)}
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </Box>
      </Stack>
    </AnalysisCard>
  );
};

JudgeHumanMatrix.propTypes = {
  pairs: PropTypes.array.isRequired,
  datasetId: PropTypes.string,
  version: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
  evalOrder: PropTypes.array,
};

export default JudgeHumanMatrix;
