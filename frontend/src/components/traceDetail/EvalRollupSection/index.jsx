import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import { Alert, Box, Stack, Typography } from "@mui/material";

import EvalRollupRow from "./EvalRollupRow";
import SearchBar from "./SearchBar";
import { evalRollupShape } from "./shapes";
import { matchesEvalSearch } from "./utils";

const EvalRollupSection = ({ rollup, onSelectSpan }) => {
  const [search, setSearch] = useState("");
  const evals = rollup?.evals || [];
  const visibleEvals = useMemo(
    () => evals.filter((evalRow) => matchesEvalSearch(evalRow, search)),
    [evals, search],
  );

  if (rollup?.error) {
    return <Alert severity="error">Evaluations are temporarily unavailable.</Alert>;
  }
  if (!evals.length) {
    return <Typography color="text.secondary">No evaluations for this span.</Typography>;
  }

  return (
    <Stack spacing={1.25}>
      <SearchBar value={search} onChange={setSearch} />
      {visibleEvals.length ? (
        visibleEvals.map((evalRow) => (
          <EvalRollupRow
            key={evalRow.eval_config_id}
            evalRow={evalRow}
            onSelectSpan={onSelectSpan}
          />
        ))
      ) : (
        <Box><Typography color="text.secondary">No matching evaluations.</Typography></Box>
      )}
    </Stack>
  );
};

EvalRollupSection.propTypes = { rollup: evalRollupShape, onSelectSpan: PropTypes.func };

export default EvalRollupSection;
