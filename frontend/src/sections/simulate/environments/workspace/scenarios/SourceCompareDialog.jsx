import PropTypes from "prop-types";
import { useState } from "react";
import {
  Box,
  Button,
  Collapse,
  DialogActions,
  DialogContent,
  DialogTitle,
  Stack,
  Typography,
  useMediaQuery,
} from "@mui/material";

import { ClosableDialog } from "src/components/custom-dialog";
import { useSourceChanges } from "src/api/simulate-environments/scenariosHooks";
import { VALIDATE_KEYS_MAX } from "src/api/simulate-environments/scenarios";
import ValidateScenariosButton from "./ValidateScenariosButton";
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

const CITED_SHOWN = 3;

// The scenarios that cite a change: the first few, the rest one click away.
function CitedBy({ keys }) {
  const reduceMotion = useMediaQuery("(prefers-reduced-motion: reduce)");
  const [showAll, setShowAll] = useState(false);
  const rest = keys.slice(CITED_SHOWN);
  return (
    <Box sx={{ typography: "s3", color: "text.secondary" }}>
      <Typography sx={{ typography: "s3" }}>
        {COPY.citedBy(keys.length, keys.slice(0, CITED_SHOWN))}
      </Typography>
      <Collapse
        in={showAll}
        timeout={reduceMotion ? 0 : undefined}
        unmountOnExit
      >
        <Typography sx={{ typography: "s3", overflowWrap: "anywhere" }}>
          {rest.join(", ")}
        </Typography>
      </Collapse>
      {rest.length > 0 && (
        <Button
          size="small"
          color="inherit"
          aria-expanded={showAll}
          sx={{ mt: 0.5, ml: -0.75 }}
          onClick={() => setShowAll((open) => !open)}
        >
          {showAll ? COPY.hide : COPY.showMore(rest.length)}
        </Button>
      )}
    </Box>
  );
}
CitedBy.propTypes = { keys: PropTypes.arrayOf(PropTypes.string).isRequired };

// The rules and files an environment version changed against its parent, the cited words marked, and the
// scenarios that cite each one.
export default function SourceCompareDialog({
  open,
  onClose,
  envId,
  versionId,
  sources = [],
  locked = false,
}) {
  const { data, isLoading, isError } = useSourceChanges(envId, versionId, {
    enabled: open,
  });
  // A rule change is named by its words and a scenario's own source by its number, so a rule's quote is looked for in
  // the text itself; a file keeps its path on both sides.
  const quotesFor = ({ kind, ref }) =>
    sources
      .filter(
        (one) =>
          one.quote &&
          one.kind === kind &&
          (kind === "rule" || one.ref === ref),
      )
      .map((one) => one.quote);
  const changes = data?.changes ?? [];
  const affected = [...new Set(changes.flatMap((one) => one.cited_by))];
  // Past the server's limit, or when not every change is listed, every scenario that needs it is proved instead.
  const byName = affected.length <= VALIDATE_KEYS_MAX && !data?.truncated;
  return (
    <ClosableDialog open={open} onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle>
        {COPY.title(data?.from_version, data?.to_version)}
      </DialogTitle>
      <DialogContent>
        {isLoading && (
          <Typography sx={{ typography: "s1" }}>{COPY.loading}</Typography>
        )}
        {isError && (
          <Typography sx={{ typography: "s1" }}>{COPY.failed}</Typography>
        )}
        <Stack spacing={3}>
          {changes.map((change) => (
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
                    <Cited text={text} quotes={quotesFor(change)} />
                  </Box>
                ))}
              </Stack>
              <CitedBy keys={change.cited_by} />
            </Stack>
          ))}
          {data?.truncated && (
            <Typography sx={{ typography: "s3", color: "text.secondary" }}>
              {COPY.truncated}
            </Typography>
          )}
        </Stack>
      </DialogContent>
      {!locked && affected.length > 0 && (
        <DialogActions>
          <ValidateScenariosButton
            envId={envId}
            {...(byName && {
              keys: affected,
              label: COPY.validate(affected.length),
            })}
          />
        </DialogActions>
      )}
    </ClosableDialog>
  );
}

SourceCompareDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  envId: PropTypes.string,
  versionId: PropTypes.string,
  sources: PropTypes.arrayOf(PropTypes.object),
  // Someone who can't edit reads the change without a next step.
  locked: PropTypes.bool,
};
