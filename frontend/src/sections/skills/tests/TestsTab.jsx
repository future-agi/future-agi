import { useState } from "react";
import PropTypes from "prop-types";
import { addCollection } from "@iconify/react";
import {
  Link as RouterLink,
  useNavigate,
  useSearchParams,
} from "react-router-dom";
import {
  Alert,
  Box,
  Button,
  Checkbox,
  IconButton,
  ListItemIcon,
  Menu,
  MenuItem,
  Pagination,
  Stack,
  Tab,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TextField,
  Typography,
} from "@mui/material";

import { getSkillErrorCode, useSkillDraft } from "src/api/skills";
import {
  useSaveSkillTestSuite,
  useSkillTestingOptions,
  useSkillTestRuns,
  useSkillTestSuite,
  useSkillV11Scope,
} from "src/api/skills/testing";
import ContentSkeleton from "src/components/content-skeleton";
import EmptyState from "src/components/empty-state";
import ErrorState from "src/components/error-state";
import Iconify from "src/components/iconify";
import Section from "src/components/section";
import StatusBadge from "src/components/status-badge";
import { ConfirmDialog } from "src/components/custom-dialog";
import { CustomTabs } from "src/components/tabs/tabs";
import { ROW_HEIGHT, TABLE_HEADER_HEIGHT } from "src/theme/sizes";
import { paths } from "src/routes/paths";
import { fDateTime } from "src/utils/format-time";
import TestCaseEditor from "./TestCaseEditor";
import TestRunSetup from "./TestRunSetup";
import TestRunResults from "./TestRunResults";

// Official Solar icons used by the new case actions; no network needed to find them.
addCollection({
  prefix: "solar",
  width: 24,
  height: 24,
  icons: {
    "menu-dots-bold": {
      body: '<g fill="currentColor"><path d="M7 12C7 13.1046 6.10457 14 5 14C3.89543 14 3 13.1046 3 12C3 10.8954 3.89543 10 5 10C6.10457 10 7 10.8954 7 12Z"/><path d="M14 12C14 13.1046 13.1046 14 12 14C10.8954 14 10 13.1046 10 12C10 10.8954 10.8954 10 12 10C13.1046 10 14 10.8954 14 12Z"/><path d="M21 12C21 13.1046 20.1046 14 19 14C17.8954 14 17 13.1046 17 12C17 10.8954 17.8954 10 19 10C20.1046 10 21 10.8954 21 12Z"/></g>',
    },
    "pen-linear": {
      body: '<path fill="none" stroke="currentColor" stroke-linecap="round" stroke-width="1.5" d="M14.3601 4.07866L15.2869 3.15178C16.8226 1.61607 19.3125 1.61607 20.8482 3.15178C22.3839 4.68748 22.3839 7.17735 20.8482 8.71306L19.9213 9.63993M14.3601 4.07866C14.3601 4.07866 14.4759 6.04828 16.2138 7.78618C17.9517 9.52407 19.9213 9.63993 19.9213 9.63993M19.9213 9.63993L11.4001 18.1612C10.8229 18.7383 10.5344 19.0269 10.2162 19.2751C9.84082 19.5679 9.43469 19.8189 9.00498 20.0237C8.6407 20.1973 8.25352 20.3263 7.47918 20.5844L4.19792 21.6782L3.39584 21.9456C3.01478 22.0726 2.59466 21.9734 2.31063 21.6894C2.0266 21.4053 1.92743 20.9852 2.05445 20.6042L2.32181 19.8021L3.41556 16.5208C3.67368 15.7465 3.80273 15.3593 3.97634 14.995C4.18114 14.5653 4.43213 14.1592 4.7249 13.7838C4.97308 13.4656 5.26166 13.1771 5.83882 12.5999L14.3601 4.07866M4.19792 21.6782L2.32181 19.8021"/>',
    },
    "trash-bin-trash-linear": {
      body: '<g fill="none" stroke="currentColor" stroke-linecap="round" stroke-width="1.5"><path d="M20.5001 6H3.5"/><path d="M18.8332 8.5L18.3732 15.3991C18.1962 18.054 18.1077 19.3815 17.2427 20.1907C16.3777 21 15.0473 21 12.3865 21H11.6132C8.95235 21 7.62195 21 6.75694 20.1907C5.89194 19.3815 5.80344 18.054 5.62644 15.3991L5.1665 8.5"/><path d="M9.5 11L10 16"/><path d="M14.5 11L14 16"/><path d="M6.5 6C6.55588 6 6.58382 6 6.60915 5.99936C7.43259 5.97849 8.15902 5.45491 8.43922 4.68032C8.44784 4.65649 8.45667 4.62999 8.47434 4.57697L8.57143 4.28571C8.65431 4.03708 8.69575 3.91276 8.75071 3.8072C8.97001 3.38607 9.37574 3.09364 9.84461 3.01877C9.96213 3 10.0932 3 10.3553 3H13.6447C13.9068 3 14.0379 3 14.1554 3.01877C14.6243 3.09364 15.03 3.38607 15.2493 3.8072C15.3043 3.91276 15.3457 4.03708 15.4286 4.28571L15.5257 4.57697C15.5433 4.62992 15.5522 4.65651 15.5608 4.68032C15.841 5.45491 16.5674 5.97849 17.3909 5.99936C17.4162 6 17.4441 6 17.5 6"/></g>',
    },
  },
});

const STARTERS = [
  {
    title: "Refund policy boundary",
    prompt:
      "An unopened item was delivered exactly 30 days ago. Is it eligible for a refund? Return JSON with an eligible boolean.",
    fixture:
      "Illustrative policy — replace with approved policy before reviewing: Unopened items are eligible through day 30, inclusive. Opened items are not eligible.",
    checks: [
      { kind: "json_equals", config: { pointer: "/eligible", value: true } },
    ],
  },
  {
    title: "Tenant isolation in a supplied patch",
    prompt:
      "Review this supplied patch for tenant isolation. Identify a missing workspace constraint without claiming to run the repository.",
    fixture:
      "Illustrative tenant rule: every Document read must filter by both organization_id and workspace_id. Patch: Document.objects.filter(organization_id=organization.id, id=document_id).first()",
    checks: [
      { kind: "contains", config: { text: "workspace", caseSensitive: false } },
    ],
  },
  {
    title: "Structured output with a missing value",
    prompt:
      "Extract amount and invoice_id from the supplied text. Return only JSON. Use null when a value is absent.",
    fixture: "Amount: 12.50 USD. No invoice identifier was supplied.",
    checks: [
      { kind: "json_equals", config: { pointer: "/amount", value: 12.5 } },
      { kind: "json_equals", config: { pointer: "/invoice_id", value: null } },
    ],
  },
];
const starterCase = (starter) => ({
  id: crypto.randomUUID(),
  sourceGroupId: crypto.randomUUID(),
  title: starter.title,
  prompt: starter.prompt,
  expectedTrigger: true,
  fixtures: [
    {
      id: crypto.randomUUID(),
      name: "Illustrative supplied context",
      text: starter.fixture,
    },
  ],
  checks: starter.checks.map((check) => ({
    ...check,
    id: crypto.randomUUID(),
    critical: true,
  })),
  reviewed: false,
});
const PREVIEW_STATES = {
  "missing-key": "Connect a supported workspace model to run tests.",
  private:
    "Draft runs stay private until you explicitly share their frozen inputs, answers and receipts.",
  stale: "For earlier content. The draft changed after this check.",
  incomplete:
    "Comparison incomplete. Missing work is not a passing expectation.",
  expired:
    "Detailed evidence expired. Summaries remain, but payloads are no longer inspectable.",
  forbidden:
    "Workspace write access is required to save examples and run tests.",
};

function RunHistory({ skillId, onOpen }) {
  const [page, setPage] = useState(1);
  const query = useSkillTestRuns(skillId, { page, pageSize: 25 });
  if (query.isLoading)
    return (
      <ContentSkeleton variant="rows" rows={5} label="Loading test history" />
    );
  if (query.isError)
    return (
      <ErrorState title="Could not load test history" onRetry={query.refetch} />
    );
  if (!query.data?.results.length)
    return (
      <EmptyState
        title="No test runs yet"
        description="Review an example and a spend preview before starting your first comparison."
      />
    );
  return (
    <Stack spacing={1.5}>
      <Typography variant="s3" color="text.secondary">
        Your private runs and workspace-shared evidence. Reruns never replace
        historical results.
      </Typography>
      <TableContainer>
        <Table size="small" aria-label="Test run history">
          <TableHead>
            <TableRow sx={{ height: TABLE_HEADER_HEIGHT.default }}>
              <TableCell>Created</TableCell>
              <TableCell>Candidate</TableCell>
              <TableCell>Status</TableCell>
              <TableCell>Coverage</TableCell>
              <TableCell>Visibility</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {query.data.results.map((run) => (
              <TableRow
                key={run.id}
                hover
                tabIndex={0}
                aria-label={`Open run from ${fDateTime(run.createdAt)}`}
                sx={{ height: ROW_HEIGHT.default, cursor: "pointer" }}
                onClick={() => onOpen(run.id)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    onOpen(run.id);
                  }
                }}
              >
                <TableCell>{fDateTime(run.createdAt)}</TableCell>
                <TableCell>
                  {run.candidate.draftRevision === null
                    ? `v${run.candidate.version}`
                    : `Draft r${run.candidate.draftRevision}`}
                </TableCell>
                <TableCell>
                  <StatusBadge status={run.status} />
                </TableCell>
                <TableCell>
                  {run.counts.graded}/{run.counts.planned} graded ·{" "}
                  {run.validity}
                </TableCell>
                <TableCell>
                  {run.visibility}
                  {run.payloadExpired ? " · payload expired" : ""}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </TableContainer>
      {query.data.totalPages > 1 && (
        <Pagination
          page={page}
          count={query.data.totalPages}
          onChange={(_, next) => setPage(next)}
        />
      )}
    </Stack>
  );
}
RunHistory.propTypes = {
  skillId: PropTypes.string.isRequired,
  onOpen: PropTypes.func.isRequired,
};

function TestsContent({ skill }) {
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const suiteQuery = useSkillTestSuite(skill.id);
  const draftQuery = useSkillDraft(
    skill.permissions?.canRunTests ? skill.id : undefined,
  );
  const save = useSaveSkillTestSuite(skill.id);
  const [view, setView] = useState(null);
  const [editor, setEditor] = useState(null);
  const [selected, setSelected] = useState(null);
  const [menu, setMenu] = useState(null);
  const [remove, setRemove] = useState(null);
  const [saveError, setSaveError] = useState("");
  const [candidateKind, setCandidateKind] = useState(
    skill.hasDraft ? "draft" : "version",
  );
  const [retry, setRetry] = useState(null);
  const suite = suiteQuery.data;
  const state = params.get("state");
  const runId = params.get("run");
  const currentView = view ?? (runId ? "results" : "cases");
  const canWrite = skill.permissions?.canRunTests === true;
  const options = useSkillTestingOptions();
  const caseIds =
    selected === null
      ? suite?.cases.map((item) => item.id) ?? []
      : selected.filter((id) => suite?.cases.some((item) => item.id === id));
  const updateResultLink = (run, selection) =>
    setParams((previous) => {
      const next = new URLSearchParams(previous);
      next.set("tab", "tests");
      next.set("run", run);
      ["case", "attempt"].forEach((key) => next.delete(key));
      if (selection?.caseId) next.set("case", selection.caseId);
      if (selection?.attemptId) next.set("attempt", selection.attemptId);
      return next;
    });
  const openRun = (run) => {
    setView("results");
    updateResultLink(run);
  };
  const saveCases = async (cases) => {
    try {
      setSaveError("");
      await save.mutateAsync({ revision: suite.revision, cases });
      return true;
    } catch (error) {
      setSaveError(
        getSkillErrorCode(error) === "test_suite_conflict"
          ? "Someone changed the saved examples. Your open example is preserved. Reload the latest set, review it, then save again."
          : "Could not save examples. Your inputs are preserved; check workspace access and try again.",
      );
      return false;
    }
  };
  if (state === "loading" || suiteQuery.isLoading)
    return (
      <ContentSkeleton variant="rows" rows={5} label="Loading skill tests" />
    );
  if (state === "error" || suiteQuery.isError)
    return (
      <ErrorState
        title="Could not load saved examples"
        onRetry={suiteQuery.refetch}
      />
    );
  if (!suite)
    return (
      <EmptyState
        title="Select a workspace"
        description="Saved examples and test evidence are workspace-scoped."
      />
    );
  const empty = state === "empty" || suite.cases.length === 0;
  return (
    <Stack spacing={2} sx={{ minWidth: 0 }}>
      {PREVIEW_STATES[state] && (
        <Alert severity="info">Preview state: {PREVIEW_STATES[state]}</Alert>
      )}
      <CustomTabs
        value={currentView}
        onChange={(_, next) => setView(next)}
        aria-label="Skill tests views"
      >
        <Tab value="cases" label="Cases" />
        <Tab value="results" label="Results" />
        <Tab value="history" label="History" />
      </CustomTabs>
      {currentView === "history" && (
        <RunHistory skillId={skill.id} onOpen={openRun} />
      )}
      {currentView === "results" &&
        (runId ? (
          <TestRunResults
            key={runId}
            skillId={skill.id}
            runId={runId}
            canWrite={canWrite}
            initialCaseId={params.get("case")}
            initialAttemptId={params.get("attempt")}
            onSelectionChange={(selection) =>
              updateResultLink(runId, selection)
            }
            onOpenFile={(path) => {
              const next = new URLSearchParams(params);
              next.set("tab", "files");
              next.set("path", path);
              setParams(next);
            }}
            onImproveSelection={
              canWrite && options.data?.assistEnabled
                ? (caseId) =>
                    navigate(
                      `${paths.dashboard.skills.edit(skill.id)}?${new URLSearchParams({ assist: "description", run: runId, case: caseId })}`,
                    )
                : undefined
            }
            onRetryCases={
              canWrite
                ? (ids, parentRunId) => {
                    setSelected(ids);
                    setRetry({ parentRunId });
                    setView("cases");
                  }
                : undefined
            }
          />
        ) : (
          <EmptyState
            title="No run selected"
            description="Start a reviewed comparison or open a previous run from History."
          />
        ))}
      {currentView === "cases" && (
        <>
          <Typography variant="s3" color="text.secondary">
            Saved examples are visible to your workspace. Draft runs stay
            private until you share them.
          </Typography>
          {saveError && (
            <Alert
              severity="error"
              action={
                <Button
                  color="inherit"
                  size="small"
                  onClick={() => suiteQuery.refetch()}
                >
                  Reload latest set
                </Button>
              }
            >
              {saveError}
            </Alert>
          )}
          {empty ? (
            <>
              <EmptyState
                title="Does this skill handle your real requests?"
                description="Add a request, what should happen, and when this skill should be used."
                action={
                  <Button
                    variant="contained"
                    disabled={!canWrite}
                    onClick={() => {
                      setEditor({ value: null });
                      setSaveError("");
                    }}
                  >
                    Add first example
                  </Button>
                }
              />
              <Section
                title="Start from an editable example"
                description="These facts and policies are illustrative. Replace them with approved context before reviewing."
              >
                <Stack spacing={1} alignItems="start">
                  {STARTERS.map((starter) => (
                    <Button
                      key={starter.title}
                      variant="outlined"
                      size="small"
                      disabled={!canWrite}
                      onClick={() => {
                        setEditor({ value: starterCase(starter) });
                        setSaveError("");
                      }}
                    >
                      {starter.title}
                    </Button>
                  ))}
                </Stack>
              </Section>
            </>
          ) : (
            <>
              <Stack
                direction="row"
                spacing={1}
                flexWrap="wrap"
                alignItems="center"
              >
                <Typography variant="m3" sx={{ flex: 1 }}>
                  {suite.cases.length} saved examples · {caseIds.length}{" "}
                  selected
                </Typography>
                <Button
                  variant="outlined"
                  disabled={!canWrite || suite.cases.length >= 20}
                  onClick={() => {
                    setEditor({ value: null });
                    setSaveError("");
                  }}
                >
                  Add example
                </Button>
              </Stack>
              <TableContainer>
                <Table size="small" aria-label="Saved examples">
                  <TableHead>
                    <TableRow sx={{ height: TABLE_HEADER_HEIGHT.default }}>
                      <TableCell padding="checkbox">
                        <Checkbox
                          checked={caseIds.length === suite.cases.length}
                          indeterminate={
                            caseIds.length > 0 &&
                            caseIds.length < suite.cases.length
                          }
                          inputProps={{ "aria-label": "Select all examples" }}
                          onChange={(event) =>
                            setSelected(
                              event.target.checked
                                ? suite.cases.map((item) => item.id)
                                : [],
                            )
                          }
                        />
                      </TableCell>
                      <TableCell>Example</TableCell>
                      <TableCell>Use skill?</TableCell>
                      <TableCell>Checks</TableCell>
                      <TableCell>Review</TableCell>
                      <TableCell>Actions</TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {suite.cases.map((item) => (
                      <TableRow
                        key={item.id}
                        hover
                        sx={{ height: ROW_HEIGHT.default }}
                      >
                        <TableCell padding="checkbox">
                          <Checkbox
                            checked={caseIds.includes(item.id)}
                            inputProps={{
                              "aria-label": `Select ${item.title}`,
                            }}
                            onChange={(event) =>
                              setSelected(
                                event.target.checked
                                  ? [...caseIds, item.id]
                                  : caseIds.filter((id) => id !== item.id),
                              )
                            }
                          />
                        </TableCell>
                        <TableCell sx={{ maxWidth: 300 }}>
                          <Typography
                            variant="s1"
                            fontWeight={500}
                            noWrap
                            title={item.title}
                          >
                            {item.title}
                          </Typography>
                          {item.exposedToAssist && (
                            <Typography variant="s3" color="text.secondary">
                              Used in recorded assistance
                            </Typography>
                          )}
                        </TableCell>
                        <TableCell>
                          {item.expectedTrigger === null
                            ? "Either"
                            : item.expectedTrigger
                              ? "Yes"
                              : "No"}
                        </TableCell>
                        <TableCell>
                          {item.checks.length}
                          {item.checks.some(
                            (check) => check.kind === "semantic",
                          )
                            ? " · AI judged"
                            : ""}
                        </TableCell>
                        <TableCell>
                          <StatusBadge
                            status={
                              item.reviewed ? "completed" : "needs_review"
                            }
                            label={item.reviewed ? "Reviewed" : "Needs review"}
                          />
                        </TableCell>
                        <TableCell>
                          <IconButton
                            size="small"
                            aria-label={`Actions for ${item.title}`}
                            aria-haspopup="menu"
                            onClick={(event) =>
                              setMenu({ anchor: event.currentTarget, item })
                            }
                          >
                            <Iconify icon="solar:menu-dots-bold" />
                          </IconButton>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </TableContainer>
              {canWrite && (
                <Section title="Compare reviewed examples">
                  <Stack spacing={2}>
                    <TextField
                      size="small"
                      select
                      label="Candidate"
                      value={candidateKind}
                      onChange={(event) => setCandidateKind(event.target.value)}
                    >
                      <MenuItem value="draft" disabled={!draftQuery.data}>
                        Your saved draft
                      </MenuItem>
                      <MenuItem value="version" disabled={!skill.latest}>
                        Latest published version
                        {skill.latest ? ` v${skill.latest.number}` : ""}
                      </MenuItem>
                    </TextField>
                    {candidateKind === "draft" && draftQuery.isError ? (
                      <ErrorState
                        title="Could not load your saved draft"
                        onRetry={draftQuery.refetch}
                      />
                    ) : candidateKind === "draft" && draftQuery.isLoading ? (
                      <ContentSkeleton
                        variant="rows"
                        rows={2}
                        label="Loading saved draft"
                      />
                    ) : (
                        candidateKind === "draft"
                          ? draftQuery.data
                          : skill.latest
                      ) ? (
                      <TestRunSetup
                        key={`${candidateKind}:${retry?.parentRunId ?? "new"}`}
                        skill={skill}
                        draft={
                          candidateKind === "draft"
                            ? draftQuery.data
                            : undefined
                        }
                        candidate={
                          candidateKind === "version"
                            ? {
                                kind: "version",
                                version: skill.latest.number,
                                digest: skill.latest.digest,
                              }
                            : undefined
                        }
                        suite={suite}
                        caseIds={caseIds}
                        parentRunId={retry?.parentRunId}
                        onStarted={(run) => {
                          setRetry(null);
                          openRun(run.id);
                        }}
                      />
                    ) : (
                      <Alert severity="info">
                        Open the editor and save a draft before running tests.{" "}
                        <Button
                          size="small"
                          component={RouterLink}
                          to={paths.dashboard.skills.edit(skill.id)}
                        >
                          Open editor
                        </Button>
                      </Alert>
                    )}
                  </Stack>
                </Section>
              )}
            </>
          )}
        </>
      )}
      <Menu
        anchorEl={menu?.anchor}
        open={Boolean(menu)}
        onClose={() => setMenu(null)}
      >
        <MenuItem
          disabled={!canWrite}
          onClick={() => {
            setEditor({ value: menu.item });
            setSaveError("");
            setMenu(null);
          }}
        >
          <ListItemIcon>
            <Iconify icon="solar:pen-linear" width={16} />
          </ListItemIcon>
          Review or edit example
        </MenuItem>
        <MenuItem
          disabled={!canWrite}
          onClick={() => {
            setRemove(menu.item);
            setMenu(null);
          }}
        >
          <ListItemIcon>
            <Iconify icon="solar:trash-bin-trash-linear" width={16} />
          </ListItemIcon>
          Remove example
        </MenuItem>
      </Menu>
      {editor && (
        <TestCaseEditor
          key={editor.value?.id ?? "new"}
          open
          initialCase={editor.value}
          onClose={() => setEditor(null)}
          isSaving={save.isPending}
          canSave={canWrite}
          error={saveError}
          onSave={async (value) => {
            const cases = suite.cases.some((item) => item.id === value.id)
              ? suite.cases.map((item) => (item.id === value.id ? value : item))
              : [...suite.cases, value];
            if (await saveCases(cases)) {
              setEditor(null);
              if (selected !== null && !selected.includes(value.id))
                setSelected([...selected, value.id]);
            }
          }}
        />
      )}
      <ConfirmDialog
        open={Boolean(remove)}
        onClose={() => {
          if (!save.isPending) setRemove(null);
        }}
        closeDisabled={save.isPending}
        title="Remove example?"
        content="This removes it from the saved suite. Historical runs retain their frozen example and evidence."
        action={
          <Button
            variant="contained"
            color="error"
            disabled={save.isPending || !canWrite}
            onClick={async () => {
              if (
                await saveCases(
                  suite.cases.filter((item) => item.id !== remove.id),
                )
              )
                setRemove(null);
            }}
          >
            Remove example
          </Button>
        }
      />
    </Stack>
  );
}
TestsContent.propTypes = { skill: PropTypes.object.isRequired };

export default function TestsTab({ skill }) {
  const { identity, enabled } = useSkillV11Scope();
  if (!enabled)
    return (
      <Alert severity="info">
        Select an authenticated workspace to view skill tests.
      </Alert>
    );
  return (
    <Box sx={{ minWidth: 0 }}>
      <TestsContent key={`${identity}:${skill.id}`} skill={skill} />
    </Box>
  );
}
TestsTab.propTypes = { skill: PropTypes.object.isRequired };
