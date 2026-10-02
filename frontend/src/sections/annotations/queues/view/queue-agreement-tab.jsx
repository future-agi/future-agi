import PropTypes from "prop-types";
import {
  Box,
  Card,
  CardContent,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Typography,
} from "@mui/material";
import { LoadingScreen } from "src/components/loading-screen";
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
      <LoadingScreen variant="orbit" sx={{ minHeight: "50vh" }} />
    );
  }

  if (!agreement) return null;

  const { overall_agreement, labels, annotator_pairs } = agreement;
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
    </Box>
  );
}

QueueAgreementTab.propTypes = {
  queueId: PropTypes.string.isRequired,
};
