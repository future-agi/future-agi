import React, { useState } from "react";
import PropTypes from "prop-types";
import { Box, Button, Stack, Typography } from "@mui/material";
import ValueViewer from "src/sections/common/ValueViewer/ValueViewer";
import TextDiff from "src/sections/common/ValueViewer/TextDiff";
import {
  correctedByLine,
  correctedRunsOf,
  correctionDiffers,
} from "./correctedOutputModel";

const OneCorrection = ({ run, corrected, output, color, mode }) => {
  const [diff, setDiff] = useState(false);
  const differs = correctionDiffers(corrected, output);
  return (
    <Box
      data-testid="drawer-corrected-output"
      data-run-id={run.run_id}
      sx={{
        p: 1.25,
        border: "1px solid",
        borderColor: "green.200",
        borderLeft: "3px solid",
        borderLeftColor: color?.solid ?? color ?? "green.500",
        borderRadius: 0.5,
      }}
    >
      <Stack direction="row" alignItems="center" gap={1} sx={{ mb: 0.5 }}>
        <Typography typography="s2" fontWeight={600}>
          {`${run.label} · corrected answer`}
        </Typography>
        <Typography typography="s3" color="text.secondary" noWrap>
          {correctedByLine(corrected)}
        </Typography>
        <Box sx={{ flex: 1 }} />
        {differs && typeof output === "string" && (
          <Button
            size="small"
            onClick={() => setDiff((v) => !v)}
            sx={{ typography: "s2", px: 0.5, minWidth: 0 }}
          >
            {diff ? "Hide diff" : "Diff vs output"}
          </Button>
        )}
      </Stack>
      {diff ? (
        <TextDiff from={output} to={corrected.value} />
      ) : (
        <ValueViewer value={corrected.value} mode={mode} emptyLabel="empty" />
      )}
    </Box>
  );
};
OneCorrection.propTypes = {
  run: PropTypes.object.isRequired,
  corrected: PropTypes.object.isRequired,
  output: PropTypes.any,
  color: PropTypes.any,
  mode: PropTypes.string,
};

/**
 * HO results R4 (W4-3): in the result drawer's Compare tab, the answers a
 * reviewer corrected on this row (R3 `corrected_output` per run), with who
 * and when and a diff against the run's own output. Nothing when none.
 */
const CorrectedOutputsBlock = ({ row, runs, colors, mode }) => {
  const list = correctedRunsOf(row, runs);
  if (!list.length) return null;
  return (
    <Stack gap={1} data-testid="drawer-corrected-outputs">
      {list.map(({ run, corrected }) => {
        const r = row?.results?.[run.run_id];
        const output = r?.trials_detail?.[0]?.output ?? r?.output ?? null;
        return (
          <OneCorrection
            key={run.run_id}
            run={run}
            corrected={corrected}
            output={output}
            color={colors?.[run.run_id]}
            mode={mode}
          />
        );
      })}
    </Stack>
  );
};

CorrectedOutputsBlock.propTypes = {
  row: PropTypes.object,
  runs: PropTypes.array,
  colors: PropTypes.object,
  mode: PropTypes.string,
};

export default CorrectedOutputsBlock;
