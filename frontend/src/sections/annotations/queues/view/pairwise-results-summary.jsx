import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Typography,
} from "@mui/material";
import { usePairwiseResults } from "src/api/annotation-queues/annotation-queues";

const percent = new Intl.NumberFormat(undefined, {
  style: "percent",
  maximumFractionDigits: 1,
});

export default function PairwiseResultsSummary({ queueId }) {
  const { data, isLoading, isError } = usePairwiseResults(queueId);

  if (isLoading) {
    return <Typography role="status">Loading pairwise results...</Typography>;
  }
  if (isError || !data?.overall) {
    return <Alert severity="error">Couldn&apos;t load pairwise results.</Alert>;
  }

  const { total_votes, tallies, win_rates } = data.overall;
  return (
    <Box>
      <Typography variant="subtitle1">Pairwise results</Typography>
      <Typography variant="body2">Total votes: {total_votes}</Typography>
      <Table size="small" aria-label="Pairwise results">
        <TableHead>
          <TableRow>
            <TableCell>Choice</TableCell>
            <TableCell align="right">Votes</TableCell>
            <TableCell align="right">Win rate</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {["A", "B", "tie"].map((choice) => (
            <TableRow key={choice}>
              <TableCell component="th" scope="row">
                {choice === "tie" ? "Tie" : choice}
              </TableCell>
              <TableCell align="right">{tallies[choice]}</TableCell>
              <TableCell align="right">{percent.format(win_rates[choice])}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      <Typography variant="caption" color="text.secondary">
        Win rates are shares of all votes, including ties.
      </Typography>
    </Box>
  );
}

PairwiseResultsSummary.propTypes = {
  queueId: PropTypes.string.isRequired,
};
