import PropTypes from "prop-types";
import { Box, Button, Stack, Tooltip, Typography } from "@mui/material";
import { StatusDot } from "../components/primitives";
import { READY_STATUS, readinessOf } from "../_mock/readiness";

/*
  Ready to run (plan T06; runs.md step 1): the short panel in Run setup for
  this agent version on this environment. Six rows; a blocker names the
  scenarios it affects and carries its fix. Not a report, no score, no cost.
*/
const namesOf = (ids, titleOf) => {
  /* Two scenarios can share a title; name each title once. */
  const names = [...new Set(ids.map(titleOf).filter(Boolean))].map((t) =>
    t.replace(/\.$/, ""),
  );
  if (names.length <= 3) return names.join(", ");
  return `${names.slice(0, 3).join(", ")} and ${names.length - 3} more`;
};

export default function ReadyToRun({
  report,
  scenarios,
  evalCount,
  onFix,
  onCheck,
  checking = false,
  children,
}) {
  const areas = readinessOf(report, scenarios, evalCount);
  const titleOf = (id) => scenarios.find((s) => s.id === id)?.title || null;
  const { requested, eligible, blocked } = report.scope;
  const unchecked = areas.some((a) => a.status === "unchecked");
  const blockers = areas.filter((a) => a.status === "blocked").length;
  const open = areas.filter((a) => a.status !== "ready");
  const ready = areas.filter((a) => a.status === "ready");
  return (
    <Box
      sx={{ border: "1px solid", borderColor: "divider", borderRadius: "8px" }}
    >
      <Stack
        direction="row"
        alignItems="baseline"
        spacing={1}
        sx={{ px: 1.5, py: 1 }}
      >
        <Typography sx={{ typography: "s2", fontWeight: 600 }}>
          Ready to run
        </Typography>
        <Typography
          sx={{
            typography: "s3",
            color: "text.tertiary",
            flex: 1,
            minWidth: 0,
          }}
        >
          {[
            `${eligible} of ${requested} eligible`,
            `${blocked.length} blocked`,
            blockers
              ? `${blockers} blocker${blockers === 1 ? "" : "s"} to fix`
              : "no blocker",
          ].join(" · ")}
        </Typography>
        {unchecked && onCheck && (
          <Button
            size="small"
            variant="outlined"
            disabled={checking}
            onClick={onCheck}
            sx={{ py: 0, flexShrink: 0 }}
          >
            {checking ? "Checking…" : "Run the check"}
          </Button>
        )}
      </Stack>
      {/* Short by design (runs.md: "a short Ready to run panel"): one line
          per blocker — its reason and the scenarios it hits on hover — and
          every ready area folded into one quiet line. */}
      {open.map((a) => {
        const st = READY_STATUS[a.status];
        const names = a.affected.length ? namesOf(a.affected, titleOf) : "";
        return (
          <Tooltip
            key={a.id}
            arrow
            placement="top-start"
            title={
              <Box sx={{ whiteSpace: "pre-line" }}>
                {[a.detail, names && `Affects ${a.affected.length}: ${names}`]
                  .filter(Boolean)
                  .join("\n")}
              </Box>
            }
          >
            <Stack
              direction="row"
              alignItems="center"
              spacing={1}
              sx={{
                px: 1.5,
                py: 0.625,
                borderTop: "1px solid",
                borderColor: "divider",
              }}
            >
              <StatusDot
                status={checking ? "running" : st.dot}
                size={6}
                live={checking}
                hollow={!checking && a.status === "unchecked"}
              />
              <Typography
                noWrap
                sx={{ typography: "s2", flexShrink: 0, maxWidth: "45%" }}
              >
                {a.label}
              </Typography>
              <Typography
                noWrap
                sx={{
                  typography: "s3",
                  color: "text.tertiary",
                  flex: 1,
                  minWidth: 0,
                }}
              >
                {checking
                  ? "Checking…"
                  : a.status === "unchecked"
                    ? "Not checked yet"
                    : `${String(a.detail || "")
                        .split(/\s+—\s+|(?<=\.)\s/)[0]
                        .replace(
                          /\.$/,
                          "",
                        )}${a.affected.length ? ` · ${a.affected.length} affected` : ""}`}
              </Typography>
              {a.fixRow && onFix && !checking && (
                <Button
                  size="small"
                  variant="outlined"
                  onClick={() => onFix(a.fixRow)}
                  sx={{ py: 0, minWidth: 0, flexShrink: 0 }}
                >
                  Fix
                </Button>
              )}
            </Stack>
          </Tooltip>
        );
      })}
      {ready.length > 0 && (
        <Tooltip
          arrow
          placement="top-start"
          title={ready.map((a) => a.label).join(" · ")}
        >
          <Stack
            direction="row"
            alignItems="center"
            spacing={1}
            sx={{
              px: 1.5,
              py: 0.625,
              borderTop: "1px solid",
              borderColor: "divider",
            }}
          >
            <StatusDot
              status={checking ? "running" : "passed"}
              size={6}
              live={checking}
            />
            <Typography
              noWrap
              sx={{ typography: "s3", color: "text.tertiary", minWidth: 0 }}
            >
              {open.length
                ? `${ready.length} more ready — ${ready.map((a) => a.label.toLowerCase()).join(", ")}`
                : `All ${ready.length} ready — ${ready.map((a) => a.label.toLowerCase()).join(", ")}`}
            </Typography>
          </Stack>
        </Tooltip>
      )}
      {children && (
        <Box
          sx={{
            px: 1.5,
            py: 1.25,
            borderTop: "1px solid",
            borderColor: "divider",
          }}
        >
          {children}
        </Box>
      )}
    </Box>
  );
}
ReadyToRun.propTypes = {
  report: PropTypes.object.isRequired,
  scenarios: PropTypes.array.isRequired,
  evalCount: PropTypes.number,
  onFix: PropTypes.func,
  onCheck: PropTypes.func,
  checking: PropTypes.bool,
  children: PropTypes.node,
};
