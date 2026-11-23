import PropTypes from "prop-types";
import {
  Box,
  DialogContent,
  DialogTitle,
  Stack,
  Typography,
} from "@mui/material";

import { ClosableDialog } from "src/components/custom-dialog";
import { useSourceChanges } from "src/api/simulate-environments/scenariosHooks";
import { LIFECYCLE_COPY } from "./scenarios.constants";

const COPY = LIFECYCLE_COPY.sourceChanged;

// The cited words marked inside a rule or a file's text, so the reader sees what the scenario relies on.
function Cited({ text, quotes }) {
  if (text == null) {
    return (
      <Typography sx={{ typography: "s1", color: "text.secondary" }}>
        {COPY.missing}
      </Typography>
    );
  }
  const found = quotes
    .map((quote) => ({
      quote,
      at: quote ? text.toLowerCase().indexOf(quote.toLowerCase()) : -1,
    }))
    .find((one) => one.at >= 0);
  return (
    <Typography
      component="pre"
      sx={{
        typography: "s1",
        whiteSpace: "pre-wrap",
        m: 0,
        fontFamily: "inherit",
      }}
    >
      {found ? (
        <>
          {text.slice(0, found.at)}
          <Box
            component="mark"
            data-testid="compare-cited"
            sx={{ bgcolor: "status.warning.bg", color: "inherit", px: 0.25 }}
          >
            {text.slice(found.at, found.at + found.quote.length)}
          </Box>
          {text.slice(found.at + found.quote.length)}
        </>
      ) : (
        text
      )}
    </Typography>
  );
}
Cited.propTypes = {
  text: PropTypes.string,
  quotes: PropTypes.arrayOf(PropTypes.string).isRequired,
};

// The rules and files an environment version changed against its parent, the cited words marked, and the
// scenarios that cite each one.
export default function SourceCompareDialog({
  open,
  onClose,
  envId,
  versionId,
  sources = [],
}) {
  const { data, isLoading, isError } = useSourceChanges(envId, versionId, {
    enabled: open,
  });
  const quotesFor = (ref) =>
    sources
      .filter((one) => one.ref === ref && one.quote)
      .map((one) => one.quote);
  return (
    <ClosableDialog open={open} onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle>
        {COPY.title(data?.from_version ?? "", data?.to_version ?? "")}
      </DialogTitle>
      <DialogContent>
        {isLoading && (
          <Typography sx={{ typography: "s1" }}>{COPY.loading}</Typography>
        )}
        {isError && (
          <Typography sx={{ typography: "s1" }}>{COPY.failed}</Typography>
        )}
        <Stack spacing={3}>
          {(data?.changes ?? []).map((change) => (
            <Stack key={`${change.kind}-${change.ref}`} spacing={1}>
              <Typography sx={{ typography: "m3" }}>
                {change.kind === "rule"
                  ? COPY.rule
                  : change.ref.replace(/^file:/, "")}
              </Typography>
              <Stack direction={{ xs: "column", md: "row" }} spacing={2}>
                {[
                  [COPY.before(data.from_version), change.before],
                  [COPY.after(data.to_version), change.after],
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
                      sx={{
                        typography: "s3",
                        color: "text.secondary",
                        mb: 0.5,
                      }}
                    >
                      {label}
                    </Typography>
                    <Cited text={text} quotes={quotesFor(change.ref)} />
                  </Box>
                ))}
              </Stack>
              <Typography sx={{ typography: "s3", color: "text.secondary" }}>
                {COPY.citedBy(change.cited_by)}
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
  envId: PropTypes.string,
  versionId: PropTypes.string,
  sources: PropTypes.arrayOf(PropTypes.object),
};
