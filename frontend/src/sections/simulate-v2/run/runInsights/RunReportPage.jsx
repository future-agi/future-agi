import PropTypes from "prop-types";
import { Fragment, useState } from "react";
import {
  Box,
  Button,
  Chip,
  IconButton,
  InputBase,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Tooltip,
  Typography,
} from "@mui/material";
import { useTheme } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import { ROW_HEIGHT, TABLE_HEADER_HEIGHT } from "src/theme/sizes";
import { statusColors } from "src/utils/runStatus";
import {
  ASK_SUGGESTIONS,
  REST_KEY,
  SCOPE_LABEL,
  answerAsk,
  templateOf,
  trialNoOf,
  whenOf,
} from "../../_mock/runInsightReports";
import { aboutCredits, estimateOf } from "../../_mock/trialQuestions";

/*
  One Insights report on a run — Observe's report page, over trials: the
  category share, the findings with the trials they rest on and a fix,
  highlighted examples, every category, then questions about the report.
*/
const cellSx = { typography: "s1", color: "text.secondary", py: 0, px: 1.5 };
const headSx = {
  typography: "s2",
  fontWeight: 500,
  color: "text.tertiary",
  bgcolor: "transparent",
  height: TABLE_HEADER_HEIGHT.default,
  py: 0,
  px: 1.5,
};

/* Category colours: the design system's data colours, in order. */
const colorAt = (theme, i, key) => {
  const data = theme.palette.data || [];
  return key === REST_KEY ? data[7] : data[i % 7];
};

function Cite({ tasks, id, onOpen }) {
  const n = trialNoOf(tasks, id);
  return (
    <Box
      component="button"
      type="button"
      onClick={() => onOpen(id)}
      sx={{
        border: 0,
        p: 0,
        mx: 0.25,
        bgcolor: "transparent",
        color: "text.primary",
        typography: "s3",
        fontWeight: 500,
        textDecoration: "underline",
        textDecorationColor: (t) => t.palette.divider,
        textUnderlineOffset: 2,
        cursor: "pointer",
        fontVariantNumeric: "tabular-nums",
      }}
    >
      #{n}
    </Box>
  );
}
Cite.propTypes = {
  tasks: PropTypes.array.isRequired,
  id: PropTypes.string.isRequired,
  onOpen: PropTypes.func.isRequired,
};

const SectionHead = ({ title, sub, right }) => (
  <Stack direction="row" alignItems="flex-end" spacing={2} sx={{ mb: 1 }}>
    <Box sx={{ flex: 1, minWidth: 0 }}>
      <Typography sx={{ typography: "m3", fontWeight: 500 }}>
        {title}
      </Typography>
      {sub && (
        <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
          {sub}
        </Typography>
      )}
    </Box>
    {right}
  </Stack>
);
SectionHead.propTypes = {
  title: PropTypes.node,
  sub: PropTypes.node,
  right: PropTypes.node,
};

export default function RunReportPage({
  report,
  tasks,
  runLabel,
  onRunAgain,
  onOpenTask,
  debug,
  onSaveAsk,
}) {
  const theme = useTheme();
  const [open, setOpen] = useState(() => new Set());
  const [focusKey, setFocusKey] = useState(null);
  const [draft, setDraft] = useState("");
  const byId = new Map(tasks.map((t) => [t.id, t]));
  const openId = (id) => {
    const t = byId.get(id);
    if (t) onOpenTask?.(t);
  };
  const colorOf = (key) =>
    colorAt(
      theme,
      Math.max(
        0,
        report.categories.findIndex((c) => c.key === key),
      ),
      key,
    );
  const catOf = (key) => report.categories.find((c) => c.key === key);
  const sum = report.categories.reduce((a, c) => a + c.ids.length, 0) || 1;
  const share = (c) => Math.round((c.ids.length / report.analysed) * 100);
  const ask = (q) => {
    if (!q.trim()) return;
    onSaveAsk(answerAsk(report, q.trim(), tasks));
    setDraft("");
  };
  const toggle = (k) =>
    setOpen((s) => {
      const n = new Set(s);
      if (n.has(k)) n.delete(k);
      else n.add(k);
      return n;
    });

  return (
    <Box sx={{ pb: 2 }}>
      {/* ── header ── */}
      <Stack direction="row" alignItems="flex-start" spacing={2}>
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Typography sx={{ typography: "m1", fontWeight: 600 }}>
            {report.title}
          </Typography>
          <Typography
            sx={{ typography: "s3", color: "text.tertiary", mt: 0.25 }}
          >
            Trials · {SCOPE_LABEL[report.scope]} · {runLabel} ·{" "}
            {report.analysed} of {report.total} trials analysed · by {report.by}
            , {whenOf(report.at)} · {aboutCredits(report.credits)}
          </Typography>
        </Box>
        <Button
          size="small"
          variant="outlined"
          startIcon={<Iconify icon="solar:restart-linear" width={14} />}
          onClick={onRunAgain}
        >
          Run again
        </Button>
      </Stack>

      {!report.categories.length ? (
        <Typography sx={{ typography: "s1", color: "text.secondary", mt: 3 }}>
          Nothing to group:{" "}
          {report.analysed
            ? "these trials share no pattern"
            : "no trials matched"}
          . Try all trials, or another template.
        </Typography>
      ) : (
        <>
          {/* ── category share ── */}
          <Box
            sx={{
              mt: 2,
              p: 1.5,
              border: "1px solid",
              borderColor: "divider",
              borderRadius: "8px",
              bgcolor: "background.paper",
            }}
          >
            <Typography sx={{ typography: "m3", fontWeight: 500, mb: 1 }}>
              Category share
            </Typography>
            <Stack
              direction="row"
              sx={{
                height: 8,
                borderRadius: "2px",
                overflow: "hidden",
                gap: "2px",
              }}
            >
              {report.categories.map((c) => (
                <Tooltip key={c.key} title={`${c.label} · ${share(c)}%`}>
                  <Box
                    onClick={() =>
                      setFocusKey((k) => (k === c.key ? null : c.key))
                    }
                    sx={{
                      flex: c.ids.length / sum,
                      bgcolor: colorOf(c.key),
                      cursor: "pointer",
                      opacity: focusKey && focusKey !== c.key ? 0.3 : 1,
                    }}
                  />
                </Tooltip>
              ))}
            </Stack>
            <Stack
              direction="row"
              flexWrap="wrap"
              columnGap={2}
              rowGap={0.5}
              sx={{ mt: 1 }}
            >
              {report.categories.map((c) => (
                <Stack
                  key={c.key}
                  direction="row"
                  alignItems="center"
                  spacing={0.75}
                >
                  <Box
                    sx={{
                      width: 8,
                      height: 8,
                      borderRadius: "2px",
                      bgcolor: colorOf(c.key),
                    }}
                  />
                  <Typography
                    noWrap
                    sx={{
                      typography: "s1",
                      color: "text.primary",
                      maxWidth: 260,
                    }}
                  >
                    {c.label}
                  </Typography>
                  <Typography
                    sx={{
                      typography: "s3",
                      color: "text.tertiary",
                      fontVariantNumeric: "tabular-nums",
                    }}
                  >
                    {share(c)}%
                  </Typography>
                </Stack>
              ))}
            </Stack>
            <Typography
              sx={{ typography: "s3", color: "text.tertiary", mt: 1 }}
            >
              One run so far. Run this report on the next run to see how it
              moves.
            </Typography>
          </Box>

          {/* ── findings ── */}
          <Box sx={{ mt: 3 }}>
            <Typography sx={{ typography: "m1", fontWeight: 600 }}>
              {report.heading}
            </Typography>
            <Typography
              sx={{ typography: "s3", color: "text.tertiary", mb: 1 }}
            >
              {report.analysed} trials read · {report.categories.length}{" "}
              categories
              {report.leftOver
                ? ` · ${report.leftOver} trials fit no category`
                : ""}
              {" · "}what was seen, then the fix to try
            </Typography>
            {report.findings.map((f, i) => {
              /* A failures finding is its category — no need to name it twice. */
              const named = catOf(f.categoryKey);
              const cat =
                named && !f.title.startsWith(named.label) ? named : null;
              return (
                <Stack
                  key={f.key}
                  direction="row"
                  spacing={1.5}
                  sx={{
                    py: 1.25,
                    borderBottom: "1px solid",
                    borderColor: "divider",
                  }}
                >
                  <Typography
                    sx={{
                      typography: "s1",
                      color: "text.tertiary",
                      width: 16,
                      flexShrink: 0,
                      fontVariantNumeric: "tabular-nums",
                    }}
                  >
                    {i + 1}
                  </Typography>
                  <Box sx={{ flex: 1, minWidth: 0 }}>
                    <Stack direction="row" alignItems="center" spacing={1}>
                      <Box
                        sx={{
                          width: 8,
                          height: 8,
                          borderRadius: "50%",
                          flexShrink: 0,
                          bgcolor: statusColors(theme, f.role).solid,
                        }}
                      />
                      <Typography
                        sx={{
                          typography: "s1",
                          fontWeight: 500,
                          color: "text.primary",
                        }}
                      >
                        {f.title}
                      </Typography>
                    </Stack>
                    <Typography
                      sx={{
                        typography: "s1",
                        color: "text.secondary",
                        mt: 0.5,
                        pl: 2,
                      }}
                    >
                      {f.observed}
                      {f.cites.map((id) => (
                        <Cite key={id} tasks={tasks} id={id} onOpen={openId} />
                      ))}
                    </Typography>
                    {f.fix && (
                      <Typography
                        sx={{
                          typography: "s1",
                          color: "text.primary",
                          mt: 0.5,
                          pl: 2,
                        }}
                      >
                        → {f.fix}
                      </Typography>
                    )}
                  </Box>
                  <Stack
                    alignItems="flex-end"
                    spacing={0.75}
                    sx={{ width: 300, flexShrink: 0 }}
                  >
                    {cat && (
                      <Stack
                        direction="row"
                        alignItems="center"
                        spacing={0.75}
                        sx={{ maxWidth: "100%" }}
                      >
                        <Box
                          sx={{
                            width: 8,
                            height: 8,
                            borderRadius: "2px",
                            flexShrink: 0,
                            bgcolor: colorOf(cat.key),
                          }}
                        />
                        <Typography
                          noWrap
                          sx={{ typography: "s3", color: "text.secondary" }}
                        >
                          {cat.label}
                        </Typography>
                      </Stack>
                    )}
                    {debug &&
                      (() => {
                        /* The finding's failing trials, debugged on their own. */
                        const ids = debug.scenarioIdsOf(
                          (named || cat)?.ids || f.cites,
                        );
                        if (!ids.length) return null;
                        const credits = debug.creditsOf(ids);
                        return (
                          <Stack alignItems="flex-end" spacing={0.25}>
                            <Stack
                              direction="row"
                              flexWrap="wrap"
                              justifyContent="flex-end"
                              gap={0.75}
                            >
                              {/* These trials failed — improve from them without a diagnosis first. */}
                              {debug.improve && (
                                <Button
                                  size="small"
                                  variant="outlined"
                                  onClick={() => debug.improve(ids)}
                                >
                                  Improve agent
                                </Button>
                              )}
                              <Button
                                size="small"
                                variant="outlined"
                                onClick={() => debug.open(ids)}
                              >
                                {`Debug ${ids.length === 1 ? "this scenario" : `these ${ids.length} scenarios`}`}
                              </Button>
                            </Stack>
                            <Typography
                              sx={{ typography: "s3", color: "text.tertiary" }}
                            >
                              {credits
                                ? `${credits} credits`
                                : "Already read — no charge"}
                            </Typography>
                          </Stack>
                        );
                      })()}
                  </Stack>
                </Stack>
              );
            })}
          </Box>

          {/* ── highlighted examples ── */}
          {report.highlights.length > 0 && (
            <Box sx={{ mt: 3 }}>
              <SectionHead
                title="Highlighted examples"
                sub="One trial from each category"
              />
              <Box
                sx={{
                  display: "grid",
                  gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))",
                  gap: 1.5,
                }}
              >
                {report.highlights.map((h) => {
                  const cat = catOf(h.categoryKey);
                  return (
                    <Box
                      key={h.key}
                      sx={{
                        p: 1.5,
                        border: "1px solid",
                        borderColor: "divider",
                        borderRadius: "8px",
                        bgcolor: "background.paper",
                        display: "flex",
                        flexDirection: "column",
                        gap: 0.75,
                      }}
                    >
                      <Stack direction="row" spacing={1}>
                        <Typography
                          sx={{
                            typography: "s1",
                            fontWeight: 500,
                            flex: 1,
                            minWidth: 0,
                          }}
                        >
                          {h.title}
                        </Typography>
                        <Typography
                          sx={{ typography: "s3", color: "text.tertiary" }}
                        >
                          #{trialNoOf(tasks, h.taskId)}
                        </Typography>
                      </Stack>
                      <Typography
                        sx={{
                          typography: "s1",
                          color: "text.secondary",
                          flex: 1,
                        }}
                      >
                        {h.body}
                      </Typography>
                      {cat && (
                        <Stack
                          direction="row"
                          alignItems="center"
                          spacing={0.75}
                        >
                          <Box
                            sx={{
                              width: 8,
                              height: 8,
                              borderRadius: "2px",
                              flexShrink: 0,
                              bgcolor: colorOf(cat.key),
                            }}
                          />
                          <Typography
                            noWrap
                            sx={{ typography: "s3", color: "text.tertiary" }}
                          >
                            {cat.label}
                          </Typography>
                        </Stack>
                      )}
                      <Stack direction="row" spacing={1} sx={{ pt: 0.5 }}>
                        <Button
                          size="small"
                          variant="outlined"
                          onClick={() => {
                            setOpen((s) => new Set(s).add(h.categoryKey));
                            document
                              .getElementById(`cat-${h.categoryKey}`)
                              ?.scrollIntoView({
                                behavior: "smooth",
                                block: "center",
                              });
                          }}
                        >
                          View category
                        </Button>
                        <Button
                          size="small"
                          variant="outlined"
                          onClick={() => openId(h.taskId)}
                        >
                          Open trial
                        </Button>
                      </Stack>
                    </Box>
                  );
                })}
              </Box>
            </Box>
          )}

          {/* ── categories ── */}
          <Box sx={{ mt: 3 }}>
            <SectionHead
              title={`Categories ${report.categories.length}`}
              sub="Click a category to see its trials"
              right={
                <Button
                  variant="outlined"
                  size="small"
                  onClick={() =>
                    setOpen((s) =>
                      s.size
                        ? new Set()
                        : new Set(report.categories.map((c) => c.key)),
                    )
                  }
                >
                  {open.size ? "Collapse all" : "Expand all"}
                </Button>
              }
            />
            <Table size="small" sx={{ tableLayout: "fixed" }}>
              <TableHead>
                <TableRow>
                  <TableCell sx={{ ...headSx, width: 32, px: 0 }} />
                  <TableCell sx={headSx}>Category</TableCell>
                  <TableCell align="right" sx={{ ...headSx, width: 120 }}>
                    Share
                  </TableCell>
                  <TableCell align="right" sx={{ ...headSx, width: 90 }}>
                    Trials
                  </TableCell>
                  <TableCell sx={{ ...headSx, width: "30%" }}>
                    Stands out
                  </TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {report.categories.map((c) => {
                  const isOpen = open.has(c.key);
                  const rows = c.subs.length
                    ? c.subs.map((s) => ({
                        key: s.label,
                        label: s.label,
                        ids: s.ids,
                      }))
                    : c.ids.map((id) => ({
                        key: id,
                        trial: byId.get(id),
                        ids: [id],
                      }));
                  return (
                    <Fragment key={c.key}>
                      <TableRow
                        id={`cat-${c.key}`}
                        hover
                        onClick={() => toggle(c.key)}
                        sx={{ height: ROW_HEIGHT.twoLine, cursor: "pointer" }}
                      >
                        <TableCell
                          sx={{ ...cellSx, px: 0, textAlign: "center" }}
                        >
                          <Iconify
                            icon={
                              isOpen
                                ? "solar:alt-arrow-down-linear"
                                : "solar:alt-arrow-right-linear"
                            }
                            width={14}
                          />
                        </TableCell>
                        <TableCell sx={cellSx}>
                          <Stack
                            direction="row"
                            alignItems="center"
                            spacing={1}
                          >
                            <Box
                              sx={{
                                width: 8,
                                height: 8,
                                borderRadius: "2px",
                                flexShrink: 0,
                                bgcolor: colorOf(c.key),
                              }}
                            />
                            <Box sx={{ minWidth: 0 }}>
                              <Typography
                                noWrap
                                sx={{
                                  typography: "s1",
                                  fontWeight: 500,
                                  color: "text.primary",
                                }}
                              >
                                {c.label}
                              </Typography>
                              <Typography
                                noWrap
                                title={c.about}
                                sx={{
                                  typography: "s3",
                                  color: "text.tertiary",
                                }}
                              >
                                {c.about}
                              </Typography>
                            </Box>
                          </Stack>
                        </TableCell>
                        <TableCell
                          align="right"
                          sx={{ ...cellSx, fontVariantNumeric: "tabular-nums" }}
                        >
                          {share(c)}%
                        </TableCell>
                        <TableCell
                          align="right"
                          sx={{ ...cellSx, fontVariantNumeric: "tabular-nums" }}
                        >
                          {c.ids.length}
                        </TableCell>
                        <TableCell sx={cellSx}>
                          <Typography
                            noWrap
                            title={c.stands.join(" · ")}
                            sx={{ typography: "s3", color: "text.tertiary" }}
                          >
                            {c.stands.join(" · ") || "—"}
                          </Typography>
                        </TableCell>
                      </TableRow>
                      {isOpen &&
                        rows.map((r) => (
                          <TableRow
                            key={r.key}
                            hover
                            onClick={() => openId(r.ids[0])}
                            sx={{
                              height: ROW_HEIGHT.default,
                              cursor: "pointer",
                            }}
                          >
                            <TableCell sx={{ ...cellSx, px: 0 }} />
                            <TableCell sx={{ ...cellSx, pl: 4 }}>
                              <Typography noWrap sx={{ typography: "s1" }}>
                                {r.trial
                                  ? `#${trialNoOf(tasks, r.trial.id)} · ${r.trial.persona?.name || r.trial.id}`
                                  : r.label}
                              </Typography>
                            </TableCell>
                            <TableCell
                              align="right"
                              sx={{
                                ...cellSx,
                                fontVariantNumeric: "tabular-nums",
                              }}
                            >
                              {r.trial
                                ? ""
                                : `${Math.round((r.ids.length / report.analysed) * 100)}%`}
                            </TableCell>
                            <TableCell
                              align="right"
                              sx={{
                                ...cellSx,
                                fontVariantNumeric: "tabular-nums",
                              }}
                            >
                              {r.trial ? "" : r.ids.length}
                            </TableCell>
                            <TableCell sx={cellSx}>
                              <Typography
                                noWrap
                                sx={{
                                  typography: "s3",
                                  color: "text.tertiary",
                                }}
                              >
                                {r.trial ? r.trial.title : ""}
                              </Typography>
                            </TableCell>
                          </TableRow>
                        ))}
                    </Fragment>
                  );
                })}
              </TableBody>
            </Table>
          </Box>
        </>
      )}

      {/* ── questions about the report ── */}
      {report.asks.length > 0 && (
        <Box sx={{ mt: 3 }}>
          <SectionHead title="Questions about this report" />
          <Stack spacing={1.5}>
            {report.asks.map((a, i) => (
              <Box
                key={i}
                sx={{
                  p: 1.5,
                  border: "1px solid",
                  borderColor: "divider",
                  borderRadius: "8px",
                  bgcolor: "background.paper",
                }}
              >
                <Typography sx={{ typography: "s1", fontWeight: 500 }}>
                  {a.question}
                </Typography>
                <Typography
                  sx={{ typography: "s3", color: "text.tertiary", mb: 1 }}
                >
                  Covers {a.scope}
                </Typography>
                {a.tooLittle ? (
                  <Typography
                    sx={{ typography: "s1", color: "text.secondary" }}
                  >
                    {a.tooLittle}
                  </Typography>
                ) : (
                  <>
                    <Typography
                      sx={{
                        typography: "s3",
                        fontWeight: 500,
                        color: "text.tertiary",
                      }}
                    >
                      Seen in the trials
                    </Typography>
                    {a.observed.map((o) => (
                      <Typography
                        key={o}
                        sx={{ typography: "s1", color: "text.secondary" }}
                      >
                        {o}
                      </Typography>
                    ))}
                    {a.hypothesis && (
                      <>
                        <Typography
                          sx={{
                            typography: "s3",
                            fontWeight: 500,
                            color: "text.tertiary",
                            mt: 1,
                          }}
                        >
                          Likely why — not yet checked
                        </Typography>
                        <Typography
                          sx={{ typography: "s1", color: "text.secondary" }}
                        >
                          {a.hypothesis}
                        </Typography>
                      </>
                    )}
                    {a.cites.length > 0 && (
                      <Typography
                        sx={{ typography: "s3", color: "text.tertiary", mt: 1 }}
                      >
                        Trials
                        {a.cites.map((id) => (
                          <Cite
                            key={id}
                            tasks={tasks}
                            id={id}
                            onOpen={openId}
                          />
                        ))}
                      </Typography>
                    )}
                  </>
                )}
              </Box>
            ))}
          </Stack>
        </Box>
      )}

      <Box
        sx={{
          position: "sticky",
          bottom: 0,
          mt: 3,
          pt: 1.5,
          bgcolor: "background.default",
        }}
      >
        <Box
          sx={{
            p: 1.5,
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "8px",
            bgcolor: "background.paper",
          }}
        >
          <Typography
            sx={{ typography: "s3", color: "text.tertiary", mb: 0.75 }}
          >
            Each question reads this report&apos;s {report.analysed} trials ·{" "}
            {aboutCredits(estimateOf(report.analysed, 1))}
          </Typography>
          <Stack direction="row" flexWrap="wrap" gap={0.75} sx={{ mb: 1 }}>
            {ASK_SUGGESTIONS.map((s) => (
              <Chip
                key={s}
                size="small"
                variant="outlined"
                label={s}
                onClick={() => ask(s)}
              />
            ))}
          </Stack>
          <Stack direction="row" alignItems="center" spacing={1}>
            <Iconify
              icon="solar:magic-stick-3-linear"
              width={16}
              sx={{ color: "text.tertiary" }}
            />
            <InputBase
              fullWidth
              value={draft}
              placeholder={`Ask anything about these ${report.analysed} trials`}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && ask(draft)}
              sx={{ typography: "s1" }}
            />
            <IconButton
              size="small"
              aria-label="Ask"
              disabled={!draft.trim()}
              onClick={() => ask(draft)}
            >
              <Iconify icon="solar:arrow-up-linear" width={16} />
            </IconButton>
          </Stack>
        </Box>
        <Typography sx={{ typography: "s3", color: "text.tertiary", mt: 0.5 }}>
          Answers read only this report&apos;s trials ·{" "}
          {templateOf(report.template).title}
        </Typography>
      </Box>
    </Box>
  );
}
RunReportPage.propTypes = {
  report: PropTypes.object.isRequired,
  tasks: PropTypes.array.isRequired,
  runLabel: PropTypes.string.isRequired,
  onRunAgain: PropTypes.func,
  onOpenTask: PropTypes.func,
  debug: PropTypes.object,
  onSaveAsk: PropTypes.func.isRequired,
};
