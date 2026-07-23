import PropTypes from "prop-types";
import {
  Box,
  Card,
  CardContent,
  CircularProgress,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Typography,
} from "@mui/material";
import { useQueueAgreement } from "src/api/annotation-queues/annotation-queues";

function getAgreementColor(pct) {
  if (pct === null || pct === undefined) return "text.secondary";
  if (pct >= 0.8) return "success.main";
  if (pct >= 0.6) return "warning.main";
  return "error.main";
}

function formatPct(val) {
  if (val === null || val === undefined) return "N/A";
  return `${(val * 100).toFixed(1)}%`;
}

export default function QueueAgreementTab({ queueId }) {
  const { data: agreement, isLoading } = useQueueAgreement(queueId);

  if (isLoading) {
    return (
      <Box sx={{ display: "flex", justifyContent: "center", py: 8 }}>
        <CircularProgress />
      </Box>
    );
  }

  if (!agreement) return null;

  const { overall_agreement, labels, annotator_pairs, judge_vs_human } =
    agreement;
  const overallAgreement = overall_agreement;
  const labelEntries = Object.entries(labels || {});
  const pairs = annotator_pairs || [];

  return (
    <Box sx={{ p: 3 }}>
      {/* Overall Agreement */}
      <Card sx={{ mb: 3 }}>
        <CardContent sx={{ textAlign: "center" }}>
          <Typography variant="caption" color="text.secondary">
            Overall Agreement
          </Typography>
          <Typography variant="h2" color={getAgreementColor(overallAgreement)}>
            {formatPct(overallAgreement)}
          </Typography>
          {overallAgreement == null && (
            <Typography variant="body2" color="text.secondary">
              Need at least 2 annotators per item to calculate agreement
            </Typography>
          )}
        </CardContent>
      </Card>

      {/* Per-Label Agreement */}
      {labelEntries.length > 0 && (
        <Box sx={{ mb: 3 }}>
          <Typography variant="subtitle2" sx={{ mb: 1 }}>
            Per-Label Agreement
          </Typography>
          <TableContainer>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>Label</TableCell>
                  <TableCell>Type</TableCell>
                  <TableCell align="right">Agreement</TableCell>
                  <TableCell align="right">Cohen&apos;s Kappa</TableCell>
                  <TableCell align="right">Disagreements</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {labelEntries.map(([id, label]) => (
                  <TableRow key={id}>
                    <TableCell>{label.label_name}</TableCell>
                    <TableCell>
                      <Typography variant="caption">
                        {label.label_type}
                      </Typography>
                    </TableCell>
                    <TableCell align="right">
                      <Typography
                        color={getAgreementColor(label.agreement_pct)}
                        fontWeight={600}
                      >
                        {formatPct(label.agreement_pct)}
                      </Typography>
                    </TableCell>
                    <TableCell align="right">
                      {label.cohens_kappa != null
                        ? label.cohens_kappa.toFixed(3)
                        : "—"}
                    </TableCell>
                    <TableCell align="right">
                      {label.disagreement_count ?? 0}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
        </Box>
      )}

      {/* Annotator Pairs */}
      {pairs.length > 0 && (
        <Box>
          <Typography variant="subtitle2" sx={{ mb: 1 }}>
            Annotator Pair Agreement
          </Typography>
          <TableContainer>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>Annotator 1</TableCell>
                  <TableCell>Annotator 2</TableCell>
                  <TableCell align="right">Agreement</TableCell>
                  <TableCell align="right">Comparisons</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {pairs.map((pair, i) => (
                  <TableRow key={i}>
                    <TableCell>{pair.annotator_1_id}</TableCell>
                    <TableCell>{pair.annotator_2_id}</TableCell>
                    <TableCell align="right">
                      <Typography
                        color={getAgreementColor(pair.agreement_pct)}
                        fontWeight={600}
                      >
                        {formatPct(pair.agreement_pct)}
                      </Typography>
                    </TableCell>
                    <TableCell align="right">
                      {pair.total_comparisons ?? 0}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
        </Box>
      )}

      {/* Judge vs Human Agreement */}
      {judge_vs_human ? (
        <Box>
          <Typography variant="subtitle2" sx={{ mb: 1 }}>
            Judge vs Human Agreement
          </Typography>
          <Typography variant="caption" color="text.secondary">
            Evaluator: {judge_vs_human.evaluator_name}
          </Typography>

          {/* Overall */}
          <Card sx={{ my: 2 }}>
            <CardContent sx={{ textAlign: "center" }}>
              <Typography variant="caption" color="text.secondary">
                Overall Judge-Human Agreement
              </Typography>
              <Typography
                variant="h2"
                color={getAgreementColor(judge_vs_human.overall_agreement)}
              >
                {formatPct(judge_vs_human.overall_agreement)}
              </Typography>
              {judge_vs_human.overall_agreement == null && (
                <Typography variant="body2" color="text.secondary">
                  Not enough overlapping items with both judge scores and human
                  labels to calculate agreement
                </Typography>
              )}
            </CardContent>
          </Card>

          {/* Per-Label */}
          {Object.keys(judge_vs_human.labels || {}).length > 0 && (
            <TableContainer>
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell>Label</TableCell>
                    <TableCell>Type</TableCell>
                    <TableCell align="right">Agreement</TableCell>
                    <TableCell align="right">Comparisons</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {Object.entries(judge_vs_human.labels).map(([id, label]) => (
                    <TableRow key={id}>
                      <TableCell>{label.label_name}</TableCell>
                      <TableCell>
                        <Typography variant="caption">
                          {label.label_type}
                        </Typography>
                      </TableCell>
                      <TableCell align="right">
                        <Typography
                          color={getAgreementColor(label.judge_human_agreement)}
                          fontWeight={600}
                        >
                          {formatPct(label.judge_human_agreement)}
                        </Typography>
                      </TableCell>
                      <TableCell align="right">
                        {label.total_comparisons ?? 0}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </TableContainer>
          )}
        </Box>
      ) : (
        <Box sx={{ mt: 2 }}>
          <Typography variant="subtitle2" sx={{ mb: 1 }}>
            Judge vs Human Agreement
          </Typography>
          <Typography variant="body2" color="text.secondary">
            Link an evaluator to this queue to compare judge scores against
            human labels.
          </Typography>
        </Box>
      )}
    </Box>
  );
}

QueueAgreementTab.propTypes = {
  queueId: PropTypes.string.isRequired,
};
