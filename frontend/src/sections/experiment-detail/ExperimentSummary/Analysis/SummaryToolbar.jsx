import React, { useEffect, useMemo, useRef, useState } from "react";
import { compareFileBase } from "src/sections/common/Runs/runLabel";
import PropTypes from "prop-types";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import {
  Box,
  Chip,
  Divider,
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
  Stack,
  Typography,
} from "@mui/material";
import axios, { endpoints } from "src/utils/axios";
import { trackEvent, Events } from "src/utils/Mixpanel";
import Iconify from "src/components/iconify";
import BaselinePicker from "src/sections/common/Runs/BaselinePicker";
import RunPickerPopover from "src/sections/common/Runs/RunPickerPopover";
import { compareSummaryHref } from "src/sections/common/Analysis/analysisLinks";
import {
  analysisCsv,
  csvDataLines,
  csvFileName,
  fileSizeText,
  worseTasksCsv,
  rowPreviewsOf,
} from "src/sections/common/Analysis/analysisCsv";
import { openFalconAnalysis } from "src/sections/falcon-ai/helpers/openFalconAnalysis";
import { useExperimentReviewQueues } from "src/api/develop/review";
import StartReviewDialog from "src/sections/develop-detail/Review/StartReviewDialog";
import { NO_REVIEW_FILTER } from "../../reviewDoor";
import { useStartRegressionReview } from "src/sections/develop-detail/Review/useStartRegressionReview";
import {
  matchingReviewQueues,
  regressionReviewLabel,
  reviewPageUrl,
} from "src/sections/develop-detail/Review/reviewModel";
import { copyText } from "src/sections/common/ux/CopyButton";
import notify from "src/sections/common/ux/toast";
import useShortcuts from "src/sections/common/ux/useShortcuts";
import { ActionBar, ItemsMenu } from "src/sections/common/ux/oneview";
import { useRegisterCommands } from "src/sections/common/CommandPalette/commandRegistry";
import { fmtCount } from "src/sections/common/format/fmt";
import { useExperimentDetailContext } from "../../experiment-context";
import { candidateItem, falconQuestion } from "./verdictItems";
import { ResultsExportDialog } from "src/sections/common/export/ExportDialog";
import { compareWithPlan } from "./compareWithModel";
import { useDatasetRuns } from "src/api/develop/runs";

const VIEWER_TIP = "You need edit access to this dataset";

function saveText(text, name) {
  const blob = new Blob([text], { type: "text/csv" });
  const url = window.URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.setAttribute("download", name);
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.URL.revokeObjectURL(url);
  return blob.size;
}

/** Find a shown (not off-screen measuring) ActionBar button by action id. */
const shownActionButton = (root, id) =>
  [...(root?.querySelectorAll(`[data-action="${id}"]`) ?? [])].find(
    (el) => !el.closest('[aria-hidden="true"]'),
  ) ?? null;

/**
 * The Summary toolbar (one view A5-04/A5-05): one row. Left `Overview ·
 * Workbench` · `Baseline #2B ▾` (one run: `Compare with…`); right an
 * ActionBar: `Choose winner` (primary, experiment pages) · `Review ▾` ·
 * `Ask Falcon ▾` (F) · `Download CSV ▾` (D) · `Copy link` (C). The bar
 * folds what does not fit into `More ▾`; every action is in ⌘K by its
 * label. R toggles the All runs `Table | Radar`.
 */
const SummaryToolbar = ({
  mode,
  datasetId,
  analysis,
  runIds = [],
  baselineId,
  onBaselineChange,
  filters,
  canEdit = true,
  verdicts = [],
  candidateId,
  onToggleChart,
  copySummary,
}) => {
  const { experimentId } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const { setChooseWinnerOpen } = useExperimentDetailContext();
  const here = `${location.pathname}${location.search}`;
  const runs = analysis?.runs ?? [];
  const single = runIds.length === 1;
  // L7c g6 (sum:compare-with): run progress, so Compare with… never picks
  // a waiting run as the baseline.
  const { data: datasetRuns } = useDatasetRuns(datasetId, {
    enabled: Boolean(datasetId) && single,
  });
  const barRef = useRef(null);
  const falconRef = useRef(null);
  const [falconAnchor, setFalconAnchor] = useState(null);
  const [keyMenu, setKeyMenu] = useState(null); // {anchor, items}
  const [reviewDialog, setReviewDialog] = useState(null);
  const [downloading, setDownloading] = useState(null);
  // W3-6 B3: `Download…` (CSV / JSON / JSONL of the rows in this view).
  const [exportOpen, setExportOpen] = useState(false);
  // The ActionBar measures its buttons when it renders; a web font that
  // lands later widens them. Render once more when fonts are ready so the
  // bar re-fits (otherwise the left-most action can be clipped at 1280).
  const [, setFontsReady] = useState(false);
  useEffect(() => {
    let live = true;
    document.fonts?.ready?.then(() => live && setFontsReady(true));
    return () => {
      live = false;
    };
  }, []);

  // ---------------------------------------------------------------- review
  // R3: `Start here` reviews the page's candidate (the run the verdict,
  // worklist and header strip are about), never a different run.
  const candidate = candidateItem(verdicts, candidateId);
  const regression = candidate?.worseIds.length ? candidate : null;
  const startReview = useStartRegressionReview(datasetId, {
    enabled: Boolean(datasetId) && !single,
  });
  const reuse = regression
    ? startReview.existing({
        runA: regression.base.run_id,
        runB: regression.run.run_id,
      })
    : null;
  const startHere = regression
    ? {
        label: reuse
          ? `Continue the regressions review (${(reuse.item_count ?? 0) - (reuse.completed_count ?? 0)} left)`
          : regressionReviewLabel(regression.worseIds.length),
        secondary: `Blind pairwise · ${regression.run.label} vs ${regression.base.label}, biggest drop first`,
        disabledReason: canEdit ? null : VIEWER_TIP,
        busy: startReview.busy,
        onClick: () =>
          startReview.start({
            runA: regression.base.run_id,
            runB: regression.run.run_id,
            labelA: regression.base.label,
            labelB: regression.run.label,
            rowIds: regression.worseIds,
            from: here,
          }),
      }
    : candidate
      ? {
          label: "No regressions to review",
          disabledReason: `No task got worse on ${candidate.run.label} than on ${candidate.base.label}. Pick another candidate, or use Pairwise to compare any two runs.`,
        }
      : null;
  // Pairwise / Rubric (StartReviewDialog) and Continue my review: the runs
  // in view, A = the baseline, B = the candidate (else the first other run).
  const reviewA =
    analysis?.baseline_run_id && runIds.includes(analysis.baseline_run_id)
      ? analysis.baseline_run_id
      : runIds[0];
  const reviewB =
    (candidate?.run.run_id !== reviewA ? candidate?.run.run_id : null) ??
    runIds.find((id) => id !== reviewA) ??
    null;
  const queuesQ = useExperimentReviewQueues(datasetId);
  const continueQueue = useMemo(() => {
    const queues = queuesQ.data ?? [];
    const pair =
      reviewA && reviewB
        ? matchingReviewQueues(queues, {
            mode: "pairwise",
            runA: reviewA,
            runB: reviewB,
          })
        : [];
    const rubric = matchingReviewQueues(queues, {
      mode: "rubric",
      runId: reviewB ?? reviewA,
    });
    return (
      [...pair, ...rubric].find(
        (q) => (q.item_count ?? 0) > (q.completed_count ?? 0),
      ) ?? null
    );
  }, [queuesQ.data, reviewA, reviewB]);
  const reviewItems = [
    startHere && {
      id: "start",
      label: startHere.label,
      icon: startHere.busy
        ? "mdi:loading"
        : "mdi:arrow-down-bold-circle-outline",
      secondary: startHere.secondary,
      disabled: Boolean(startHere.disabledReason) || Boolean(startHere.busy),
      reason: startHere.disabledReason ?? undefined,
      keywords: ["regressions", "start here", "blind"],
      run: () => startHere.onClick?.(),
    },
    {
      id: "pairwise",
      label: "Pairwise…",
      icon: "mdi:compare-horizontal",
      secondary:
        reviewA && reviewB
          ? "Which of two runs is better, row by row"
          : "Pick the run to compare against in the next step",
      disabled: !reviewA,
      reason: "No run to review yet",
      run: () => setReviewDialog("pairwise"),
    },
    {
      id: "rubric",
      label: "Rubric…",
      icon: "mdi:star-outline",
      secondary: "Rate one run's outputs with labels",
      disabled: !reviewA,
      reason: "No run to review yet",
      run: () => setReviewDialog("rubric"),
    },
    continueQueue && {
      id: "continue",
      label: "Continue my review",
      icon: "mdi:play-circle-outline",
      secondary: `${continueQueue.name} · ${Math.max(0, (continueQueue.item_count ?? 0) - (continueQueue.completed_count ?? 0))} left`,
      run: () =>
        navigate(reviewPageUrl(datasetId, continueQueue.id, { from: here })),
    },
  ].filter(Boolean);

  // --------------------------------------------------------------- falcon
  const questions = [
    ...verdicts.slice(0, 2).map(falconQuestion),
    "Summarize this comparison in three bullets",
  ].filter(Boolean);
  const evalCount = (analysis?.metrics ?? []).filter(
    (m) => m.family === "eval",
  ).length;
  const attached = `${fmtCount(runs.length, "run")} · ${fmtCount(analysis?.examples_in_view ?? 0, "task")} · ${fmtCount(evalCount, "eval")}`;
  const falconContext = {
    datasetId,
    experimentId: mode === "experiment" ? experimentId : undefined,
    runs,
    baselineId: analysis?.baseline_run_id,
    filters,
    url: here,
  };
  const ask = (question) => {
    setFalconAnchor(null);
    openFalconAnalysis({ question, context: falconContext });
  };
  // Under its button when it shows; else at the end of the bar (folded
  // into More, or opened with F / ⌘K).
  const openFalcon = () =>
    setFalconAnchor(
      falconRef.current?.isConnected ? falconRef.current : barRef.current,
    );

  // ------------------------------------------------------------------ CSV
  const baseName =
    mode === "experiment"
      ? runs[0]?.experiment_name ?? "experiment"
      : compareFileBase(runs.map((r) => r.label));
  const saved = (name, text) => {
    const size = saveText(text, name);
    const rows = csvDataLines(text);
    notify(`Saved ${name} · ${fmtCount(rows, "row")} · ${fileSizeText(size)}`, {
      variant: "success",
      key: "summary-csv",
    });
  };
  const worseTotal = verdicts.reduce((n, v) => n + v.worseIds.length, 0);
  const download = async (scope) => {
    if (!analysis) return;
    if (scope === "summary") {
      saved(csvFileName(`${baseName}-summary`), analysisCsv(analysis));
      return;
    }
    if (scope === "worse") {
      // L7c g6 (sum:export): read the worse rows themselves so every line
      // carries its row number and input.
      const ids = [...new Set(verdicts.flatMap((v) => v.worseIds ?? []))];
      let previews = {};
      setDownloading(scope);
      try {
        const res = await axios.get(
          endpoints.develop.datasetRuns.results(datasetId),
          {
            params: {
              runs: runIds.join(","),
              filters: JSON.stringify([{ type: "rows", ids }]),
              page: 0,
              page_size: Math.min(1000, Math.max(1, ids.length)),
            },
          },
        );
        previews = rowPreviewsOf(res?.data?.result?.rows);
      } catch {
        previews = {};
      } finally {
        setDownloading(null);
      }
      saved(
        csvFileName(`${baseName}-got-worse`),
        worseTasksCsv(analysis, verdicts, previews),
      );
      return;
    }
    if (!experimentId) return;
    setDownloading(scope);
    trackEvent(Events.expDownloadClicked);
    try {
      const res = await axios.get(
        endpoints.develop.experiment.downloadExperiment(experimentId),
        { responseType: "text" },
      );
      saved(csvFileName(`${baseName}-rows`), String(res.data ?? ""));
    } catch {
      notify("Couldn't download the rows. Try again.", {
        variant: "error",
        key: "summary-csv",
      });
    } finally {
      setDownloading(null);
    }
  };
  const csvItems = [
    {
      id: "summary",
      label: "Summary table",
      icon: "mdi:table-large",
      secondary: `${fmtCount(runs.length, "run")} · every metric with its delta`,
      run: () => download("summary"),
    },
    {
      id: "rows",
      label: "All rows with eval results",
      icon: "mdi:table-arrow-down",
      secondary: `${fmtCount(analysis?.examples_total ?? 0, "row")} · inputs, outputs and eval results`,
      disabled: mode !== "experiment" || !experimentId,
      reason: "Open one experiment to download its rows",
      run: () => download("rows"),
    },
    {
      id: "worse",
      label: "Tasks that got worse",
      icon: "mdi:arrow-down-bold-box-outline",
      secondary: `${fmtCount(worseTotal, "line")} · run, baseline, row id and input`,
      disabled: !worseTotal,
      reason: "No task got worse than on the baseline",
      run: () => download("worse"),
    },
  ];

  // -------------------------------------------------------------- link
  const copyLink = async () => {
    const ok = await copyText(window.location.href);
    notify(
      ok
        ? "Link copied: this view, with its baseline and filters"
        : "Couldn't copy. Your browser blocked the clipboard.",
      {
        variant: ok ? "success" : "error",
        key: "summary-copy-link",
      },
    );
  };

  const winnerBlocked = !canEdit
    ? VIEWER_TIP
    : runIds.length < 2
      ? "Choose winner ranks two or more runs of this experiment"
      : null;
  const openWinner = () => {
    if (winnerBlocked) return;
    trackEvent(Events.expWinnerClick);
    setChooseWinnerOpen(true);
  };

  // A menu action opened by its key: under its button when the bar shows
  // it, else (folded into More) at the end of the bar.
  const openExplore = () => {
    const sp = new URLSearchParams(location.search);
    sp.set("view", "workbench");
    navigate(`${location.pathname}?${sp.toString()}`, { replace: true });
  };
  const openActionMenu = (id, items) => {
    const btn = shownActionButton(barRef.current, id);
    if (btn) btn.click();
    else setKeyMenu({ anchor: barRef.current, items });
  };

  useShortcuts("summary", {
    download: () => openActionMenu("csv", csvItems),
    falcon: openFalcon,
    toggleChart: () => onToggleChart?.(),
    copyLink,
  });

  useRegisterCommands(
    "summary-toolbar",
    [
      {
        id: "summary.falcon",
        label: "Ask Falcon about this verdict",
        keywords: ["why", "explain", "falcon"],
        icon: "mdi:creation-outline",
        run: () => ask(questions[0] ?? "Summarize this comparison"),
      },
      {
        id: "summary.workbench",
        label: "Explore: pivot and chart",
        keywords: ["pivot", "workbench", "analyze", "explore", "chart"],
        icon: "mdi:table-pivot",
        run: () => {
          const sp = new URLSearchParams(location.search);
          sp.set("view", "workbench");
          navigate(`${location.pathname}?${sp.toString()}`, { replace: true });
        },
      },
    ],
    [questions[0], location.search],
  );

  // IT-R4 G6 P5: `Choose winner` lives in More (the verdict is the page's
  // first answer; the bar keeps Explore and More).
  const actions = [
    // IT-R3 G6 P3 (rule 26): the pivot / chart tool is a side sheet, not a
    // second layout.
    {
      id: "explore",
      label: "Explore",
      icon: "mdi:table-pivot",
      priority: 1,
      variant: "outlined",
      keywords: ["pivot", "chart", "workbench", "slice", "explore"],
      run: openExplore,
    },
    // IT-R3 G6 P4: Review / Ask Falcon / Download CSV fold into one More
    // menu (their keys still work: F asks Falcon, D downloads).
    {
      id: "more",
      label: "More",
      icon: "mdi:dots-horizontal",
      priority: 1,
      variant: "outlined",
      keywords: ["review", "falcon", "csv", "download", "export", "winner"],
      menu: [
        ...(mode === "experiment"
          ? [
              {
                id: "winner",
                label: "Choose winner…",
                icon: "mdi:crown-outline",
                secondary: "Rank the runs and pick one to promote",
                disabled: Boolean(winnerBlocked),
                reason: winnerBlocked ?? undefined,
                run: openWinner,
              },
            ]
          : []),
        ...reviewItems.map((m) => ({
          ...m,
          id: `review-${m.id}`,
          label: `Review: ${m.label}`,
        })),
        {
          id: "falcon",
          label: "Ask Falcon about this verdict…",
          icon: "mdi:creation-outline",
          secondary: `Reads ${attached} · key F`,
          run: openFalcon,
        },
        ...csvItems.map((m) => ({
          ...m,
          id: `csv-${m.id}`,
          label: `Download CSV: ${m.label}`,
          disabled: m.disabled || !analysis || Boolean(downloading),
          reason: downloading ? "Preparing the file" : m.reason,
        })),
        {
          id: "export-rows",
          label: "Download…",
          icon: "material-symbols:download",
          secondary:
            "Rows with outputs, eval reasons, trials and trace ids · CSV, JSON or JSONL",
          disabled: !runIds.length || !datasetId,
          reason: "No run to export yet",
          keywords: ["export", "json", "jsonl", "csv", "rows"],
          run: () => setExportOpen(true),
        },
        // W5 B-H1: the printable Summary (Explore's print view, the same
        // R5 / R1 reads as this page).
        {
          id: "print",
          label: "Print or save as PDF",
          icon: "mdi:printer-outline",
          secondary:
            "Verdicts, metrics with intervals, config differences, worst rows · A4 landscape",
          disabled: !analysis,
          reason: "The summary is still loading",
          run: () => {
            const sp = new URLSearchParams(location.search);
            sp.set("view", "workbench");
            sp.set("print", "1");
            navigate(`${location.pathname}?${sp.toString()}`);
          },
        },
        // IT-R5 G6 P3: the verdict card's copy, when the bar sits in it.
        ...(copySummary
          ? [
              {
                id: "copy-summary",
                label: "Copy summary",
                icon: "mdi:content-copy",
                secondary: "Markdown with the numbers",
                run: async () => {
                  const ok = await copyText(copySummary());
                  notify(
                    ok
                      ? "Summary copied"
                      : "Couldn't copy. Your browser blocked the clipboard.",
                    {
                      variant: ok ? "success" : "error",
                      key: "summary-copy",
                    },
                  );
                },
              },
            ]
          : []),
      ],
    },
    // Compare pages already carry a labelled `Copy link` in their header
    // (CompareWrapperView), so the bar shows it only on experiment pages; the
    // C key (useShortcuts above) copies the same link on both.
    mode !== "compare" && {
      id: "copyLink",
      label: "Copy link",
      icon: "mdi:link-variant",
      priority: 2,
      variant: "icon",
      shortcut: "copyLink",
      keywords: ["share", "url", "link", "this view"],
      run: copyLink,
    },
  ].filter(Boolean);

  const datasetBaseline = analysis?.dataset_baseline;
  const onlyRun = single ? runs[0] : null;

  return (
    <Stack
      direction="row"
      alignItems="center"
      gap={1.5}
      sx={{ width: "100%", minWidth: 0 }}
      data-testid="summary-toolbar"
    >
      <Stack direction="row" alignItems="center" gap={1} sx={{ flexShrink: 0 }}>
        {single ? (
          <>
            <Typography typography="s2" color="text.secondary" noWrap>
              {onlyRun
                ? `${onlyRun.label} · absolute values`
                : "Absolute values"}
            </Typography>
            {datasetBaseline && datasetBaseline.run_id !== onlyRun?.run_id && (
              <Chip
                size="small"
                variant="outlined"
                icon={<Iconify icon="mdi:pin-outline" width={16} />}
                label={`Compare with dataset baseline ${datasetBaseline.label}`}
                clickable
                onClick={() =>
                  navigate(
                    compareSummaryHref({
                      datasetId,
                      runIds: [datasetBaseline.run_id, onlyRun.run_id],
                      baselineId: datasetBaseline.run_id,
                      from: here,
                    }),
                  )
                }
                sx={{ height: 30, typography: "s2", fontWeight: 500 }}
              />
            )}
            {onlyRun && (
              <RunPickerPopover
                datasetId={datasetId}
                excludeIds={[onlyRun.run_id]}
                groupBySameTarget
                max={8}
                label="Compare with…"
                confirmLabel="Compare"
                onPick={(picked) => {
                  if (!picked?.length) return;
                  // L7c g6: the run you came from is the candidate; a
                  // waiting or empty run is never the baseline.
                  const plan = compareWithPlan(
                    picked,
                    onlyRun.run_id,
                    datasetRuns?.results ?? [],
                    datasetBaseline?.run_id ?? null,
                  );
                  const href = compareSummaryHref({
                    datasetId,
                    runIds: plan.runIds,
                    baselineId: plan.baselineId ?? "none",
                    from: here,
                  });
                  navigate(
                    `${href}${href.includes("?") ? "&" : "?"}cand=${encodeURIComponent(plan.cand)}`,
                  );
                }}
              />
            )}
          </>
        ) : mode === "compare" ? null : (
          <BaselinePicker
            datasetId={datasetId}
            value={baselineId ?? analysis?.baseline_run_id ?? "none"}
            pinnedRunId={datasetBaseline?.run_id ?? null}
            runsInView={runs}
            restrictTo={mode === "experiment" ? runIds : undefined}
            allowNone
            onChange={(id) => onBaselineChange?.(id)}
          />
        )}
      </Stack>
      <Box ref={barRef} sx={{ flex: 1, minWidth: 0, display: "flex" }}>
        <ActionBar surface="summary" actions={actions} />
      </Box>
      <Menu
        anchorEl={falconAnchor}
        open={Boolean(falconAnchor)}
        onClose={() => setFalconAnchor(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
        slotProps={{ paper: { sx: { width: 360 } } }}
      >
        <Typography
          typography="s3"
          color="text.secondary"
          sx={{ px: 2, pt: 0.5, pb: 1 }}
        >
          Falcon reads what is on screen: {attached}, baseline{" "}
          {analysis?.baseline_label ?? "none"}.
        </Typography>
        {questions.map((q) => (
          <MenuItem key={q} onClick={() => ask(q)}>
            <ListItemIcon>
              <Iconify icon="mdi:message-question-outline" width={18} />
            </ListItemIcon>
            <ListItemText
              primary={q}
              primaryTypographyProps={{
                typography: "s1",
                sx: { whiteSpace: "normal" },
              }}
            />
          </MenuItem>
        ))}
        <Divider />
        <MenuItem onClick={() => ask("")}>
          <ListItemIcon>
            <Iconify icon="mdi:pencil-outline" width={18} />
          </ListItemIcon>
          <ListItemText primaryTypographyProps={{ typography: "s1" }}>
            Ask your own question
          </ListItemText>
        </MenuItem>
      </Menu>
      <ItemsMenu
        open={Boolean(keyMenu)}
        anchorEl={keyMenu?.anchor}
        onClose={() => setKeyMenu(null)}
        items={(keyMenu?.items ?? []).map((m) => ({
          ...m,
          secondary: m.disabled ? m.reason : m.secondary,
        }))}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
      />
      <ResultsExportDialog
        open={exportOpen}
        onClose={() => setExportOpen(false)}
        datasetId={datasetId}
        view={{
          runIds,
          baselineId: analysis?.baseline_run_id ?? baselineId ?? undefined,
          pills: filters ?? [],
          show: "all",
          search: "",
          sort: "order",
        }}
      />
      <StartReviewDialog
        open={Boolean(reviewDialog)}
        onClose={() => setReviewDialog(null)}
        datasetId={datasetId}
        mode={reviewDialog ?? "pairwise"}
        runA={reviewA}
        runB={
          reviewDialog === "rubric" ? reviewB ?? reviewA : reviewB ?? undefined
        }
        // L7c g6 (grid:review-rows, HIDE): no `Current filter` source
        // until g8's dialog counts it against the right run.
        filters={NO_REVIEW_FILTER}
        returnTo={here}
      />
    </Stack>
  );
};

SummaryToolbar.propTypes = {
  mode: PropTypes.oneOf(["experiment", "compare"]),
  datasetId: PropTypes.string,
  analysis: PropTypes.object,
  runIds: PropTypes.arrayOf(PropTypes.string),
  baselineId: PropTypes.string,
  onBaselineChange: PropTypes.func,
  filters: PropTypes.array,
  canEdit: PropTypes.bool,
  verdicts: PropTypes.array,
  // R3: the page's candidate run (`analysis.candidate_run_id`); its picker
  // is in the Tasks to review header (one view A5-12)
  candidateId: PropTypes.string,
  onToggleChart: PropTypes.func,
  // IT-R5 G6 P3: () => markdown; adds `Copy summary` to More.
  copySummary: PropTypes.func,
};

export default SummaryToolbar;
