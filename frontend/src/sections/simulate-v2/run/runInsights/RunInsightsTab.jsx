import PropTypes from "prop-types";
import { useEffect, useState } from "react";
import {
  Box,
  Button,
  ButtonBase,
  LinearProgress,
  Stack,
  Typography,
} from "@mui/material";
import { useTheme } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import {
  REST_KEY,
  SCOPE_LABEL,
  TEMPLATES,
  buildRunReport,
  whenOf,
} from "../../_mock/runInsightReports";
import NewRunReport from "./NewRunReport";
import RunReportPage from "./RunReportPage";

/*
  The run's Insights tab — Observe's Insights, over this run's trials:
  saved reports and templates on the left, a report or the builder on the
  right. A report starts only when the user runs it.
*/
const READ_MS = 2200;

export default function RunInsightsTab({
  envState,
  patch,
  runId,
  runLabel,
  tasks,
  insights,
  onOpenTask,
  debug,
}) {
  const theme = useTheme();
  const reports = envState.insightReports?.[runId] || [];
  const [openId, setOpenId] = useState(() => reports[0]?.id || null);
  const [building, setBuilding] = useState(() =>
    reports.length ? null : { template: "failures" },
  );
  const [reading, setReading] = useState(null);
  const open = reports.find((r) => r.id === openId) || null;

  useEffect(() => {
    if (!reading) return undefined;
    const id = setTimeout(() => setReading(null), READ_MS);
    return () => clearTimeout(id);
  }, [reading]);

  const save = (next) =>
    patch({
      insightReports: { ...(envState.insightReports || {}), [runId]: next },
    });
  const run = (draft) => {
    const report = buildRunReport({ ...draft, insights, tasks, runId });
    save([report, ...reports]);
    setOpenId(report.id);
    setBuilding(null);
    setReading({ id: report.id, n: report.analysed });
  };

  return (
    <Box
      sx={{
        display: "grid",
        gridTemplateColumns: "224px minmax(0, 1fr)",
        columnGap: 3,
        alignItems: "stretch",
      }}
    >
      {/* ── reports and templates ── */}
      <Box
        sx={{
          borderRight: "1px solid",
          borderColor: "divider",
          pr: 1.5,
          /* The divider runs to the bottom of the window, however short the page. */
          minHeight: "calc(100vh - 142px)",
        }}
      >
        <Stack direction="row" alignItems="center">
          <Typography sx={{ typography: "m3", fontWeight: 500, flex: 1 }}>
            Insights
          </Typography>
          <Button
            size="small"
            startIcon={<Iconify icon="solar:add-linear" width={14} />}
            onClick={() => setBuilding({ template: "failures" })}
            sx={{ color: "text.primary" }}
          >
            New report
          </Button>
        </Stack>
        <Typography sx={{ typography: "s3", color: "text.tertiary", mb: 1.5 }}>
          Find where the agent fails in this run, with the trials behind every
          number.
        </Typography>

        <Stack spacing={0.25}>
          {reports.map((r) => {
            const selected = !building && r.id === openId;
            const sum = r.categories.reduce((a, c) => a + c.ids.length, 0) || 1;
            return (
              <ButtonBase
                key={r.id}
                onClick={() => {
                  setOpenId(r.id);
                  setBuilding(null);
                }}
                sx={{
                  display: "block",
                  textAlign: "left",
                  width: "100%",
                  px: 1,
                  py: 0.75,
                  borderRadius: "6px",
                  bgcolor: selected ? "action.selected" : "transparent",
                  "&:hover": {
                    bgcolor: selected ? "action.selected" : "action.hover",
                  },
                }}
              >
                <Typography
                  noWrap
                  sx={{
                    typography: "s1",
                    fontWeight: 500,
                    color: "text.primary",
                  }}
                >
                  {r.title}
                </Typography>
                <Typography
                  noWrap
                  sx={{ typography: "s3", color: "text.tertiary" }}
                >
                  {SCOPE_LABEL[r.scope]} · {r.analysed} trials · {whenOf(r.at)}
                </Typography>
                {r.categories.length > 0 && (
                  <Stack
                    direction="row"
                    sx={{
                      height: 4,
                      mt: 0.75,
                      gap: "2px",
                      borderRadius: "2px",
                      overflow: "hidden",
                    }}
                  >
                    {r.categories.map((c, i) => (
                      <Box
                        key={c.key}
                        sx={{
                          flex: c.ids.length / sum,
                          bgcolor: (theme.palette.data || [])[
                            c.key === REST_KEY ? 7 : i % 7
                          ],
                        }}
                      />
                    ))}
                  </Stack>
                )}
              </ButtonBase>
            );
          })}
        </Stack>

        <Typography
          sx={{
            typography: "s3",
            fontWeight: 500,
            color: "text.tertiary",
            mt: 2,
            mb: 0.5,
          }}
        >
          Start from a template
        </Typography>
        {TEMPLATES.map((t) => (
          <ButtonBase
            key={t.id}
            onClick={() => setBuilding({ template: t.id })}
            sx={{
              width: "100%",
              justifyContent: "flex-start",
              gap: 1,
              px: 1,
              minHeight: 28,
              borderRadius: "6px",
              "&:hover": { bgcolor: "action.hover" },
            }}
          >
            <Iconify icon={t.icon} width={14} sx={{ color: "text.tertiary" }} />
            <Typography
              noWrap
              sx={{ typography: "s1", color: "text.secondary" }}
            >
              {t.title}
            </Typography>
          </ButtonBase>
        ))}
      </Box>

      {/* ── a report, or the builder ── */}
      <Box sx={{ minWidth: 0 }}>
        {building || !open ? (
          <NewRunReport
            key={`${building?.template}-${building?.id || ""}`}
            tasks={tasks}
            insights={insights}
            runLabel={runLabel}
            initial={building}
            onCancel={reports.length ? () => setBuilding(null) : undefined}
            onRun={run}
          />
        ) : reading?.id === open.id ? (
          <Box sx={{ maxWidth: 520, pt: 1 }}>
            <Typography sx={{ typography: "m1", fontWeight: 600 }}>
              {open.title}
            </Typography>
            <Typography
              sx={{
                typography: "s1",
                color: "text.secondary",
                mt: 0.5,
                mb: 1.5,
              }}
            >
              Reading {reading.n} trials from {runLabel} and grouping what it
              finds…
            </Typography>
            <LinearProgress />
          </Box>
        ) : (
          <RunReportPage
            key={open.id}
            report={open}
            tasks={tasks}
            runLabel={runLabel}
            onRunAgain={() => setBuilding({ ...open, id: Date.now() })}
            onOpenTask={onOpenTask}
            debug={debug}
            onSaveAsk={(answer) =>
              save(
                reports.map((r) =>
                  r.id === open.id ? { ...r, asks: [...r.asks, answer] } : r,
                ),
              )
            }
          />
        )}
      </Box>
    </Box>
  );
}
RunInsightsTab.propTypes = {
  envState: PropTypes.object.isRequired,
  patch: PropTypes.func.isRequired,
  runId: PropTypes.string.isRequired,
  runLabel: PropTypes.string.isRequired,
  tasks: PropTypes.array.isRequired,
  insights: PropTypes.object.isRequired,
  onOpenTask: PropTypes.func,
  debug: PropTypes.object,
};
