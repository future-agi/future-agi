import PropTypes from "prop-types";
import {
  Box,
  DialogContent,
  DialogTitle,
  Stack,
  Typography,
} from "@mui/material";

import { ClosableDialog } from "src/components/custom-dialog";
import { useSourceChanges } from "src/api/harness/scenarios";
import { SCENARIOS_COPY } from "./scenarios.constants";

// The cited words, marked inside a rule or a file's text, so the reader sees what the case relies on.
function Cited({ text, quotes }) {
  if (text == null) {
    return (
      <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
        {SCENARIOS_COPY.compareMissing}
      </Typography>
    );
  }
  const found = quotes
    .map((quote) => ({
      quote,
      at: quote ? text.toLowerCase().indexOf(quote.toLowerCase()) : -1,
    }))
    .find((one) => one.at >= 0);
  const body = found ? (
    <>
      {text.slice(0, found.at)}
      <Box
        component="mark"
        data-testid="compare-cited"
        sx={{ bgcolor: "warning.lighter", px: 0.25 }}
      >
        {text.slice(found.at, found.at + found.quote.length)}
      </Box>
      {text.slice(found.at + found.quote.length)}
    </>
  ) : (
    text
  );
  return (
    <Typography
      component="pre"
      sx={{
        typography: "s2",
        whiteSpace: "pre-wrap",
        m: 0,
        fontFamily: "inherit",
      }}
    >
      {body}
    </Typography>
  );
}

Cited.propTypes = {
  text: PropTypes.string,
  quotes: PropTypes.arrayOf(PropTypes.string).isRequired,
};

// T21: compare the rules and files a version changed with its parent's, the cited words marked, and the
// cases that cite each one.
export default function SourceCompareDialog({
  open,
  onClose,
  jobId,
  versionId,
  sources = [],
}) {
  const { data, isLoading, isError } = useSourceChanges(jobId, versionId, {
    enabled: open,
  });
  const quotesFor = (ref) =>
    sources
      .filter((one) => one.ref === ref && one.quote)
      .map((one) => one.quote);
  return (
    <ClosableDialog open={open} onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle>
        {SCENARIOS_COPY.compareTitle(
          data?.from_version ?? "",
          data?.to_version ?? "",
        )}
      </DialogTitle>
      <DialogContent>
        {isLoading && (
          <Typography sx={{ typography: "s2" }}>
            {SCENARIOS_COPY.compareLoading}
          </Typography>
        )}
        {isError && (
          <Typography sx={{ typography: "s2" }}>
            {SCENARIOS_COPY.compareFailed}
          </Typography>
        )}
        <Stack spacing={3} data-testid="source-compare">
          {(data?.changes ?? []).map((change) => (
            <Stack key={`${change.kind}-${change.ref}`} spacing={1}>
              <Typography
                sx={{ typography: "s1", fontWeight: "fontWeightMedium" }}
              >
                {change.kind === "rule"
                  ? SCENARIOS_COPY.compareRule
                  : change.ref.replace(/^file:/, "")}
              </Typography>
              <Stack direction={{ xs: "column", md: "row" }} spacing={2}>
                {[
                  [
                    SCENARIOS_COPY.compareBefore(data.from_version),
                    change.before,
                  ],
                  [SCENARIOS_COPY.compareAfter(data.to_version), change.after],
                ].map(([label, text]) => (
                  <Box
                    key={label}
                    sx={{
                      flex: 1,
                      border: 1,
                      borderColor: "divider",
                      borderRadius: 1,
                      p: 1.5,
                    }}
                  >
                    <Typography
                      sx={{ typography: "s3", color: "text.subtitle", mb: 0.5 }}
                    >
                      {label}
                    </Typography>
                    <Cited text={text} quotes={quotesFor(change.ref)} />
                  </Box>
                ))}
              </Stack>
              <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
                {SCENARIOS_COPY.compareCitedBy(change.cited_by)}
              </Typography>
            </Stack>
          ))}
        </Stack>
      </DialogContent>
    </ClosableDialog>
  );
}

SourceCompareDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  jobId: PropTypes.string,
  versionId: PropTypes.string,
  sources: PropTypes.arrayOf(PropTypes.object),
};
