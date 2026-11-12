import { Box, Stack, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React, { useEffect, useState } from "react";
import { getColorFromSeed } from "src/utils/utils";
import { erroredTriesText } from "../results/erroredTries";

/**
 * Results grid footer (PRODUCT-SPEC §3.10.9): `Rows 7 of 248 (filtered) ·
 * 2 errors · 9 flaky` from the R3 totals. Not-comparable counts stay in the
 * header tooltips.
 */
export const ResultsStatusLine = ({
  total,
  examplesTotal,
  errors,
  flaky,
  filtered,
  selected,
  onFilterErrors,
  onFilterFlaky,
  errorCauses = [],
  onFilterCause,
  errorPolicy,
  onErrorPolicy,
  erroredTries = 0,
  erroredTriesLeftOut = 0,
  erroredTriesTitle = null,
  ledger = null,
  flakyScope = null,
  ledgerParts = null,
  ledgerScope = null,
  onLedgerPart,
}) => {
  const parts = [];
  // W1-6: with the run's ledger parts the line prints the one
  // ledger, each number opening its rows, and none of its own error counts.
  const ownLedger = Array.isArray(ledgerParts) && ledgerParts.length > 0;
  // The ledger already names its lead run's causes; the footer adds only
  // the causes of the other runs in view (never the same cause twice).
  const ledgerCauseText = ownLedger
    ? ledgerParts
        .map((part) =>
          [part.text, part.label, ...(part.causes ?? []).map((c) => c?.cause)]
            .filter(Boolean)
            .join(" "),
        )
        .join(" ")
    : "";
  const rows =
    filtered && Number.isFinite(examplesTotal)
      ? `Rows ${total} of ${examplesTotal} (filtered)`
      : `Rows ${total ?? 0}`;
  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        gap: 1,
        px: 1.5,
        py: 0.75,
        minHeight: 36,
      }}
    >
      <Typography
        typography="s2"
        color="text.primary"
        sx={{ fontVariantNumeric: "tabular-nums" }}
      >
        {rows}
      </Typography>
      {!ownLedger && errors > 0 && (
        <>
          <Dot />
          <StatusLink color="error.main" onClick={onFilterErrors}>
            {`${errors} ${errors === 1 ? "error" : "errors"}`}
          </StatusLink>
        </>
      )}
      {/* W1-7: the errored rows by cause (every run in view); each opens
          just those rows. L7c g6 (status:error-cause, status:ledger): shown
          with the run's ledger too, so a run that is not the ledger's lead
          still shows its causes and its errored rows open from here. */}
      {onFilterCause &&
        errorCauses
          .filter((c) => !ledgerCauseText.includes(String(c.cause)))
          .map((c) => (
            <React.Fragment key={c.cause}>
              <Dot />
              <StatusLink
                color="error.dark"
                onClick={() => onFilterCause(c.cause)}
                testId="error-cause-chip"
                title={`Show the ${c.rows} ${c.rows === 1 ? "row" : "rows"} that errored with ${c.cause} on any run in view`}
              >
                {`${c.cause} ${c.rows}`}
              </StatusLink>
            </React.Fragment>
          ))}
      {/* L7c g6 (status:ledger): how errored tries count in scores, as
          plain text (one count per policy, the runs in the tooltip) when
          the re-score is not offered. */}
      {!onErrorPolicy && erroredTries + erroredTriesLeftOut > 0 && (
        <>
          <Dot />
          <Typography
            typography="s2"
            color="text.secondary"
            data-testid="errored-tries-count"
            title={erroredTriesTitle ?? undefined}
            sx={{ whiteSpace: "nowrap" }}
          >
            {erroredTriesText(erroredTries, erroredTriesLeftOut)}
          </Typography>
        </>
      )}
      {/* W1-7: how errored tries count in scores, changed as a re-score. */}
      {onErrorPolicy && erroredTries > 0 && (
        <>
          <Dot />
          <StatusLink
            color="text.secondary"
            testId="error-policy-link"
            onClick={() =>
              onErrorPolicy(errorPolicy === "exclude" ? "zero" : "exclude")
            }
            title={
              errorPolicy === "exclude"
                ? "Errored tries are left out of every score. Count them as 0 again in a new run."
                : "Errored tries count as 0 in every score. Leave them out in a new run."
            }
          >
            {`${erroredTries} errored ${erroredTries === 1 ? "try" : "tries"} ${
              errorPolicy === "exclude"
                ? "left out of scores · count as 0…"
                : "counted as 0 · leave out of scores…"
            }`}
          </StatusLink>
        </>
      )}
      {flaky > 0 && (
        <>
          <Dot />
          <StatusLink
            color="warning.dark"
            onClick={onFilterFlaky}
            testId="status-flaky"
            title="Rows whose tries disagree (some pass, some fail), the Matrix's Flaky rule"
          >
            {`${flaky} flaky${flakyScope ? ` on ${flakyScope}` : ""}`}
          </StatusLink>
        </>
      )}
      {selected > 0 && (
        <>
          <Dot />
          <Typography typography="s2" color="text.secondary">
            {`${selected} selected`}
          </Typography>
        </>
      )}
      {ownLedger ? (
        <>
          <Dot />
          <Box
            data-testid="results-ledger"
            title={`${ledgerScope ? `${ledgerScope}: ` : ""}${ledgerParts.map((p) => p.text).join(" · ")}`}
            sx={{
              display: "flex",
              alignItems: "center",
              gap: 0.75,
              minWidth: 0,
              overflow: "hidden",
              whiteSpace: "nowrap",
            }}
          >
            {ledgerScope ? (
              <Typography
                typography="s2"
                color="text.secondary"
                data-testid="results-ledger-scope"
              >
                {`${ledgerScope}:`}
              </Typography>
            ) : null}
            {ledgerParts.map((part, i) => (
              <React.Fragment key={part.key}>
                {i > 0 ? <Dot /> : null}
                <LedgerPart part={part} onOpen={onLedgerPart} />
              </React.Fragment>
            ))}
          </Box>
        </>
      ) : null}
      {/* W1-6: the run's one ledger line (runLedger.ledgerLine). */}
      {!ownLedger && ledger ? (
        <>
          <Dot />
          <Typography
            typography="s2"
            color="text.secondary"
            noWrap
            data-testid="results-ledger"
            title={ledger}
            sx={{ minWidth: 0, overflow: "hidden", textOverflow: "ellipsis" }}
          >
            {ledger}
          </Typography>
        </>
      ) : null}
      {parts}
    </Box>
  );
};

const PART_COLOR = {
  errored: "error.main",
  excluded: "error.dark",
  skipped: "text.secondary",
  stopped: "warning.dark",
};

/**
 * One ledger number: a link to its rows when it lists them (an errored
 * part opens each cause on its own: `2 errored (RateLimitError 1,
 * timeout 1)`), else plain text.
 */
const LedgerPart = ({ part, onOpen }) => {
  const color = PART_COLOR[part.key] ?? "text.secondary";
  const can = (p) => Boolean(onOpen && (p.ids?.length || p.filter));
  if (part.key === "errored" && part.causes?.length > 1 && onOpen) {
    const head = part.text.replace(/\s*\(.*\)$/, "");
    return (
      <Stack direction="row" alignItems="center" sx={{ whiteSpace: "nowrap" }}>
        <StatusLink
          color={color}
          onClick={() => onOpen(part)}
          testId="ledger-part-errored"
          title={`Show the ${part.n} errored ${part.n === 1 ? "row" : "rows"}`}
        >
          {head}
        </StatusLink>
        <Typography typography="s2" color="text.secondary">
          {"\u00a0("}
        </Typography>
        {part.causes.map((c, i) => (
          <React.Fragment key={c.cause}>
            {i > 0 ? (
              <Typography typography="s2" color="text.secondary">
                {",\u00a0"}
              </Typography>
            ) : null}
            <StatusLink
              color="error.dark"
              onClick={() => onOpen({ ...part, cause: c.cause, ids: c.ids })}
              testId="error-cause-chip"
              title={`Show the ${c.n} ${c.n === 1 ? "row" : "rows"} that errored with ${c.cause}`}
            >
              {`${c.cause} ${c.n}`}
            </StatusLink>
          </React.Fragment>
        ))}
        <Typography typography="s2" color="text.secondary">
          )
        </Typography>
      </Stack>
    );
  }
  return (
    <StatusLink
      color={can(part) ? color : "text.secondary"}
      onClick={can(part) ? () => onOpen(part) : undefined}
      testId={`ledger-part-${part.key}`}
      title={can(part) ? `Show these ${part.n} rows` : undefined}
    >
      {part.text}
    </StatusLink>
  );
};

LedgerPart.propTypes = {
  part: PropTypes.object.isRequired,
  onOpen: PropTypes.func,
};

const Dot = () => (
  <Typography typography="s2" color="text.disabled">
    ·
  </Typography>
);

const StatusLink = ({ children, color, onClick, testId, title }) => (
  <Typography
    typography="s2"
    color={color}
    fontWeight={500}
    onClick={onClick}
    data-testid={testId}
    title={title}
    sx={{
      cursor: onClick ? "pointer" : "default",
      fontVariantNumeric: "tabular-nums",
      "&:hover": onClick ? { textDecoration: "underline" } : undefined,
    }}
  >
    {children}
  </Typography>
);

StatusLink.propTypes = {
  children: PropTypes.node,
  color: PropTypes.string,
  onClick: PropTypes.func,
  testId: PropTypes.string,
  title: PropTypes.string,
};

ResultsStatusLine.propTypes = {
  total: PropTypes.number,
  examplesTotal: PropTypes.number,
  errors: PropTypes.number,
  flaky: PropTypes.number,
  filtered: PropTypes.bool,
  selected: PropTypes.number,
  onFilterErrors: PropTypes.func,
  onFilterFlaky: PropTypes.func,
  errorCauses: PropTypes.arrayOf(
    PropTypes.shape({ cause: PropTypes.string, rows: PropTypes.number }),
  ),
  onFilterCause: PropTypes.func,
  errorPolicy: PropTypes.oneOf(["zero", "exclude", "fail"]),
  onErrorPolicy: PropTypes.func,
  erroredTries: PropTypes.number,
  erroredTriesLeftOut: PropTypes.number,
  erroredTriesTitle: PropTypes.string,
  ledger: PropTypes.string,
  flakyScope: PropTypes.string,
  ledgerParts: PropTypes.arrayOf(PropTypes.object),
  ledgerScope: PropTypes.string,
  onLedgerPart: PropTypes.func,
};

const ExperimentStatusBar = ({ api, columnAvgs }) => {
  const [count, setCount] = useState(0);

  const updateStatusBar = () => {
    setCount(api.getDisplayedRowCount());
  };

  useEffect(() => {
    api.addEventListener("modelUpdated", updateStatusBar);

    return () => {
      if (!api.isDestroyed()) {
        api.removeEventListener("modelUpdated", updateStatusBar);
      }
    };
  }, []);

  return (
    <Box sx={{ display: "flex", alignItems: "center", gap: 2, padding: 1 }}>
      {/* Total Rows */}
      <Typography
        variant="body2"
        sx={{
          marginRight: 2,
        }}
      >
        Rows: {count}
      </Typography>

      {/* Iterate over dummy_avg array */}
      {columnAvgs?.map((item) => (
        <Box
          key={item.id}
          sx={{
            display: "flex",
            alignItems: "center",
            gap: 0.5,
            border: "1px solid",
            borderColor: "divider",
            paddingX: 1.5,
            paddingY: 0.5,
            borderRadius: "8px",
          }}
        >
          <Box
            sx={{
              width: "8px",
              height: "8px",
              borderRadius: "50%",
              marginRight: 0.5,
              backgroundColor: getColorFromSeed(item.id),
            }}
          />
          <Typography variant="body2">{item.value.toFixed(2)}%</Typography>
        </Box>
      ))}
    </Box>
  );
};

ExperimentStatusBar.propTypes = {
  api: PropTypes.object.isRequired,
  columnAvgs: PropTypes.arrayOf(
    PropTypes.shape({
      id: PropTypes.string.isRequired,
      value: PropTypes.number.isRequired,
    }),
  ),
};

export default ExperimentStatusBar;
