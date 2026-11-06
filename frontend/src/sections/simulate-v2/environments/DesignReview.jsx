import PropTypes from "prop-types";
import { Children, isValidElement, useState } from "react";
import {
  Box,
  Button,
  ButtonBase,
  IconButton,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import SideDrawer from "../components/SideDrawer";
import AddSourceMenu from "../workspace/data/AddSourceMenu";
import { getActor } from "../_mock/actors";
import {
  leaveOutDepPatch,
  scenarioGroupsOf,
  simulateDepPatch,
  unsupportedOf,
} from "../_mock/scratchDesign";

/*
  Builder → Review design (builder.md). The proposal — world, data, tools,
  rules, people and scenario coverage — waits for approval; only approval
  starts the cloud build. Auto mode carries out approved steps without
  stopping, but never skips this approval. A question left past the waiting
  limit is Parked; Continue reopens the same proposal, not a new one.
*/
export default function DesignReview({
  env,
  draftCount,
  waiting,
  parked,
  since,
  autoMode,
  onApprove,
  onContinue,
  framed = false,
  sources = null,
  onAddFiles,
  onAddOrg,
  onChange,
  question = null,
}) {
  const [anchor, setAnchor] = useState(null);
  const [planOpen, setPlanOpen] = useState(false);
  /* The rule being reworded, and its text so far. */
  const [editing, setEditing] = useState(null);
  const [draft, setDraft] = useState("");
  /* The last removal, so it can be put back. */
  const [undo, setUndo] = useState(null);
  /* Built from scratch there's no agent: the world comes from the words. */
  const scratch = env?.builtFrom?.kind === "scratch";
  /* Files before the build (plan: "Attach a policy and optional CSV"):
     they shape this design and join the first version. */
  const canAdd = !!(onAddFiles && onAddOrg);
  const files = sources?.sources || [];
  const sourcesValue = (
    <>
      {files.length
        ? `${files.map((f) => f.name).join(", ")} · adds ${
            [
              sources.rules &&
                `${sources.rules} rule${sources.rules === 1 ? "" : "s"}`,
              sources.records &&
                `${sources.records.toLocaleString()} seeded records`,
            ]
              .filter(Boolean)
              .join(" and ") || "reference material"
          }${sources.needsInput ? ` · ${sources.needsInput} to answer after the build` : ""}`
        : scratch
          ? "Your description"
          : "The agent alone"}
      {canAdd && (
        <Button
          size="small"
          variant="outlined"
          onClick={(e) => setAnchor(e.currentTarget)}
          startIcon={<Iconify icon="mingcute:add-line" width={14} />}
          sx={{ ...actionSx, ml: 1, "& .MuiButton-startIcon": { mr: 0.5 } }}
        >
          {files.length ? "Add another" : "Add files"}
        </Button>
      )}
    </>
  );
  /* Where each planned item came from (builder.md §2: the builder records
     answers and sources). Generated values say so. */
  const origin = scratch
    ? "From your description"
    : env?.builtFrom?.kind === "template" || env?.builtFrom?.worldSource
      ? "From the ready world"
      : "From the agent's code";
  const tableSrc = (t) =>
    t.fromFile || t.source ? `From ${t.fromFile || t.source}` : "Generated";
  const ruleSrc = scratch
    ? "From your description"
    : origin === "From the agent's code"
      ? "From the agent's prompt"
      : origin;
  const testRequest = env?.builtFrom?.testRequest || null;
  const rows = [
    ...(testRequest ? [["Tests", testRequest]] : []),
    ...(canAdd || files.length ? [["Sources", sourcesValue]] : []),
    [
      "World",
      `${(env?.seed?.tables || []).length} tables · ${(env?.seed?.tables || []).reduce((n, t) => n + (t.rows || 0), 0).toLocaleString()} rows`,
    ],
    ["Tools", `${(env?.tools || []).length}`],
    [
      "Rules",
      `${(env?.rules || []).length}${sources?.rules ? ` + ${sources.rules} from your files` : ""}`,
    ],
    [
      "People",
      `${(env?.actors || []).length ? `Caller + ${(env?.actors || []).length}` : "Caller only"}`,
    ],
    /* The scenarios are saved as the first suite version, s1. */
    ["Scenarios", `${draftCount} to write · suite s1`],
  ];
  const status = parked ? "Parked" : waiting ? "Awaiting approval" : "Approved";
  const tools = env?.tools || [];
  const rules = env?.rules || [];
  const actors = env?.actors || [];
  const tables = env?.seed?.tables || [];
  /* Behaviors the description asks for that the world can't stand up. */
  const deps = unsupportedOf(env?.builtFrom?.intake?.flow);
  const choiceOf = (d) => env?.depChoices?.[d.id] || null;
  const pending = deps.filter((d) => !choiceOf(d));
  const groups = scenarioGroupsOf(env, draftCount);
  const saveRule = (i) => {
    const text = draft.trim();
    if (text && text !== rules[i])
      onChange({ rules: rules.map((r, j) => (j === i ? text : r)) });
    setEditing(null);
  };
  /* Removing a simulation's stand-in is leaving that behavior out. */
  const removing = (label, patch) => {
    setUndo({
      label,
      restore: { tools, rules, actors, depChoices: env?.depChoices || {} },
    });
    onChange(patch);
  };
  const removeTool = (t) => {
    const dep = deps.find((d) => d.alt.name === t.name);
    removing(
      t.name,
      dep
        ? leaveOutDepPatch(env, dep)
        : { tools: tools.filter((x) => x.name !== t.name) },
    );
  };
  /* Framed: the full-width card above every tab, so the one thing to do
     shows wherever the person is — chat folded or not, any tab. */
  if (framed)
    return (
      <Box sx={{ px: 2, pt: 1.5, flexShrink: 0 }}>
        <Box
          sx={{
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "8px",
            bgcolor: "background.paper",
            overflow: "hidden",
            "& > div": { borderBottom: 0 },
          }}
        >
          <DesignReview
            env={env}
            draftCount={draftCount}
            waiting={waiting}
            parked={parked}
            since={since}
            autoMode={autoMode}
            onApprove={onApprove}
            onContinue={onContinue}
            sources={sources}
            onAddFiles={onAddFiles}
            onAddOrg={onAddOrg}
            onChange={onChange}
            question={question}
          />
        </Box>
      </Box>
    );
  /* Compact: title, status and the one action on a row; the proposal as
     one wrapping line of facts; one line on what happens next. */
  const note = parked
    ? `Waiting on you since ${new Date(since).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })}.`
    : waiting && question
      ? `One question first, in the builder chat: ${question}`
      : waiting
        ? "Nothing is built until you approve. To change something first, ask the builder."
        : "Approved — the build is running from this design.";
  return (
    <Box
      sx={{
        flexShrink: 0,
        borderBottom: "1px solid",
        borderColor: "divider",
        px: 2,
        py: 1,
      }}
    >
      <Stack direction="row" alignItems="center" spacing={1}>
        <Typography sx={{ typography: "s2", fontWeight: 600 }}>
          Review design
        </Typography>
        <Typography
          sx={{
            typography: "s3",
            color: parked
              ? "status.warning.fg"
              : waiting
                ? "text.secondary"
                : "status.success.fg",
          }}
        >
          {status}
        </Typography>
        <Box flex={1} />
        {canAdd && (
          <AddSourceMenu
            anchorEl={anchor}
            onClose={() => setAnchor(null)}
            onAddFiles={(f) => onAddFiles(f)}
            onAddOrg={(o) => {
              setAnchor(null);
              onAddOrg(o);
            }}
            added={files.filter((f) => f.kb).map((f) => f.name)}
          />
        )}
        {parked ? (
          <Button
            size="small"
            variant="outlined"
            onClick={onContinue}
            sx={{ color: "text.primary", borderColor: "divider" }}
          >
            Continue
          </Button>
        ) : (
          waiting && (
            <Tooltip
              arrow
              title={
                question
                  ? "Answer the builder's question first — it won't guess a policy."
                  : autoMode
                    ? "Auto mode runs approved steps without stopping. It never approves a design for you."
                    : ""
              }
            >
              <span>
                <Button
                  size="small"
                  variant="contained"
                  color="primary"
                  disabled={!!question}
                  onClick={onApprove}
                  startIcon={
                    <Iconify icon="solar:check-circle-linear" width={14} />
                  }
                >
                  Approve and build
                </Button>
              </span>
            </Tooltip>
          )
        )}
      </Stack>

      {waiting && (
        <Stack
          direction="row"
          flexWrap="wrap"
          columnGap={2.5}
          rowGap={0.25}
          sx={{ mt: 0.75 }}
        >
          {rows.map(([k, v]) => (
            <Typography key={k} component="div" sx={{ typography: "s2" }}>
              <Box component="span" sx={{ color: "text.secondary", mr: 0.75 }}>
                {k}
              </Box>
              <Box component="span" sx={{ color: "text.primary" }}>
                {v}
              </Box>
            </Typography>
          ))}
        </Stack>
      )}

      {waiting && pending.length > 0 && (
        <Box sx={{ mt: 0.75 }}>
          <Typography sx={{ typography: "s2", fontWeight: 600 }}>
            Can&apos;t be built here — simulate it instead
          </Typography>
          {pending.map((d) => (
            <Stack
              key={d.id}
              direction="row"
              alignItems="center"
              spacing={1}
              sx={{ py: 0.25 }}
            >
              <Typography
                component="div"
                sx={{ typography: "s2", flex: 1, minWidth: 0 }}
              >
                <Box component="span" sx={{ fontWeight: 500 }}>
                  {d.request}
                </Box>
                <Box
                  component="span"
                  sx={{ color: "text.secondary", ml: 0.75 }}
                >
                  {`${d.why} Adds ${d.alt.name}: ${d.alt.desc.charAt(0).toLowerCase()}${d.alt.desc.slice(1)}.`}
                </Box>
              </Typography>
              {onChange && (
                <>
                  <Button
                    size="small"
                    variant="outlined"
                    onClick={() => onChange(simulateDepPatch(env, d))}
                    sx={{
                      color: "text.primary",
                      borderColor: "divider",
                      flexShrink: 0,
                    }}
                  >
                    Simulate it
                  </Button>
                  <Button
                    size="small"
                    variant="outlined"
                    onClick={() => onChange(leaveOutDepPatch(env, d))}
                    sx={actionSx}
                  >
                    Leave out
                  </Button>
                </>
              )}
            </Stack>
          ))}
        </Box>
      )}

      {waiting && (
        <Button
          size="small"
          variant="outlined"
          onClick={() => setPlanOpen(true)}
          sx={{ ...actionSx, mt: 0.75 }}
        >
          Show the plan
        </Button>
      )}
      {/* The plan opens beside the page, so the summary above the tabs
         stays one compact box. */}
      {waiting && (
        <SideDrawer
          open={planOpen}
          onClose={() => setPlanOpen(false)}
          width={{ xs: "100%", sm: 560 }}
        >
          <Stack sx={{ height: "100%" }}>
            <Stack
              direction="row"
              alignItems="center"
              spacing={1.5}
              sx={{
                px: 2.5,
                py: 2,
                borderBottom: "1px solid",
                borderColor: "divider",
              }}
            >
              <Box flex={1} minWidth={0}>
                <Typography sx={{ typography: "m2", fontWeight: 600 }}>
                  The plan
                </Typography>
                <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                  Nothing is built until you approve.
                </Typography>
              </Box>
              <IconButton
                size="small"
                onClick={() => setPlanOpen(false)}
                aria-label="Close"
              >
                <Iconify icon="solar:close-linear" width={16} />
              </IconButton>
            </Stack>
            <Box sx={{ px: 2.5, py: 1.5, flex: 1, overflowY: "auto" }}>
              <Stack spacing={2.5}>
                <>
                  <PlanRow label="Records">
                    {tables.length ? (
                      tables.map((t) => (
                        <PlanLine key={t.name} src={tableSrc(t)}>
                          <Box component="span" sx={{ fontWeight: 500 }}>
                            {t.name}
                          </Box>
                          <Box
                            component="span"
                            sx={{ color: "text.secondary", ml: 0.75 }}
                          >
                            {`${(t.rows || 0).toLocaleString()} rows${t.note ? ` · ${t.note}` : ""}`}
                          </Box>
                        </PlanLine>
                      ))
                    ) : (
                      <PlanLine empty>
                        No records — the description named none
                      </PlanLine>
                    )}
                  </PlanRow>
                  <PlanRow label="Tools">
                    {tools.length ? (
                      tools.map((t) => (
                        <PlanLine
                          key={t.name}
                          src={t.source || origin}
                          onRemove={onChange && (() => removeTool(t))}
                        >
                          <Box component="span" sx={{ fontWeight: 500 }}>
                            {t.name}
                          </Box>
                          {t.desc && (
                            <Box
                              component="span"
                              sx={{ color: "text.secondary", ml: 0.75 }}
                            >
                              {t.desc}
                            </Box>
                          )}
                        </PlanLine>
                      ))
                    ) : (
                      <PlanLine empty>None yet</PlanLine>
                    )}
                    {deps
                      .filter((d) => choiceOf(d) === "skip")
                      .map((d) => (
                        <PlanLine key={d.id}>
                          <Box
                            component="span"
                            sx={{ color: "text.secondary" }}
                          >
                            {`${d.request} — can't be built here, left out`}
                          </Box>
                        </PlanLine>
                      ))}
                  </PlanRow>
                  <PlanRow label="Rules">
                    {rules.length ? (
                      rules.map((r, i) =>
                        editing === i ? (
                          <Stack
                            key={`${i}-edit`}
                            direction="row"
                            alignItems="center"
                            spacing={1}
                            sx={{ px: 1.5, py: 0.75 }}
                          >
                            <TextField
                              size="small"
                              fullWidth
                              autoFocus
                              value={draft}
                              onChange={(e) => setDraft(e.target.value)}
                              onKeyDown={(e) => {
                                if (e.key === "Enter") saveRule(i);
                                if (e.key === "Escape") setEditing(null);
                              }}
                              sx={{
                                "& .MuiInputBase-input": {
                                  typography: "s2",
                                  py: 0.5,
                                },
                              }}
                            />
                            <Button
                              size="small"
                              variant="outlined"
                              onClick={() => saveRule(i)}
                              sx={{
                                color: "text.primary",
                                borderColor: "divider",
                                flexShrink: 0,
                              }}
                            >
                              Save
                            </Button>
                            <ButtonBase
                              onClick={() => setEditing(null)}
                              sx={{ ...linkSx, flexShrink: 0 }}
                            >
                              Cancel
                            </ButtonBase>
                          </Stack>
                        ) : (
                          <PlanLine
                            key={`${i}-${r}`}
                            src={ruleSrc}
                            onEdit={
                              onChange &&
                              (() => {
                                setEditing(i);
                                setDraft(r);
                              })
                            }
                            onRemove={
                              onChange &&
                              (() =>
                                removing("The rule", {
                                  rules: rules.filter((_, j) => j !== i),
                                }))
                            }
                          >
                            {r}
                          </PlanLine>
                        ),
                      )
                    ) : (
                      <PlanLine empty>None yet</PlanLine>
                    )}
                  </PlanRow>
                  <PlanRow label="People">
                    <PlanLine>
                      Caller
                      <Box
                        component="span"
                        sx={{ color: "text.secondary", ml: 0.75 }}
                      >
                        the person the agent serves
                      </Box>
                    </PlanLine>
                    {actors.map((id) => (
                      <PlanLine
                        key={id}
                        src="Proposed for the scenarios"
                        onRemove={
                          onChange &&
                          (() =>
                            removing(getActor(id)?.name || id, {
                              actors: actors.filter((x) => x !== id),
                            }))
                        }
                      >
                        {getActor(id)?.name || id}
                      </PlanLine>
                    ))}
                  </PlanRow>
                  <PlanRow
                    label="Scenarios"
                    count={groups.reduce((n, g) => n + g.n, 0) || draftCount}
                  >
                    {groups.length ? (
                      groups.map((g) => (
                        <PlanLine key={g.label}>
                          <Box
                            component="span"
                            sx={{ fontVariantNumeric: "tabular-nums" }}
                          >
                            {g.n}
                          </Box>
                          <Box component="span" sx={{ ml: 0.75 }}>
                            {g.label}
                          </Box>
                        </PlanLine>
                      ))
                    ) : (
                      <PlanLine
                        empty
                      >{`${draftCount} written after the build, one per use case`}</PlanLine>
                    )}
                  </PlanRow>
                </>
              </Stack>
              {undo && (
                <Stack
                  direction="row"
                  alignItems="center"
                  spacing={1}
                  sx={{ mt: 1.5 }}
                >
                  <Typography
                    sx={{ typography: "s2", color: "text.secondary" }}
                  >
                    {`${undo.label} removed from the plan.`}
                  </Typography>
                  <Button
                    size="small"
                    variant="outlined"
                    onClick={() => {
                      onChange(undo.restore);
                      setUndo(null);
                    }}
                    sx={actionSx}
                  >
                    Undo
                  </Button>
                </Stack>
              )}
            </Box>
          </Stack>
        </SideDrawer>
      )}

      <Typography sx={{ typography: "s3", color: "text.tertiary", mt: 0.5 }}>
        {note}
      </Typography>
    </Box>
  );
}

/* Small outlined actions beside a line, like Edit and Remove. */
const actionSx = {
  height: 24,
  px: 1,
  minWidth: 0,
  typography: "s3",
  color: "text.primary",
  borderColor: "divider",
  flexShrink: 0,
};

/* Text-only toggles and Cancel. */
const linkSx = {
  typography: "s2",
  fontWeight: 600,
  textDecoration: "underline",
  color: "text.secondary",
};

/* One section of the plan: a heading with its count, then its lines in a
   bordered list — the spec-sheet look the rest of the environment uses. */
function PlanRow({ label, count, children }) {
  const n =
    count ??
    Children.toArray(children).filter(
      (c) => isValidElement(c) && !c.props.empty,
    ).length;
  return (
    <Box>
      <Typography sx={{ typography: "s2", fontWeight: 600, mb: 0.75 }}>
        {label}
        {n > 0 && (
          <Box
            component="span"
            sx={{ color: "text.tertiary", fontWeight: 400, ml: 0.75 }}
          >
            {n}
          </Box>
        )}
      </Typography>
      <Box
        sx={{
          border: "1px solid",
          borderColor: "divider",
          borderRadius: "8px",
          "& > * + *": { borderTop: "1px solid", borderColor: "divider" },
        }}
      >
        {children}
      </Box>
    </Box>
  );
}
PlanRow.propTypes = {
  label: PropTypes.string,
  count: PropTypes.number,
  children: PropTypes.node,
};

/* A quiet icon action at the end of a line; the word is on hover. */
function LineAction({ icon, label, onClick }) {
  return (
    <Tooltip arrow title={label}>
      <IconButton
        size="small"
        aria-label={label}
        onClick={onClick}
        sx={{ color: "text.tertiary", "&:hover": { color: "text.primary" } }}
      >
        <Iconify icon={icon} width={16} />
      </IconButton>
    </Tooltip>
  );
}
LineAction.propTypes = {
  icon: PropTypes.string.isRequired,
  label: PropTypes.string.isRequired,
  onClick: PropTypes.func.isRequired,
};

function PlanLine({ children, onEdit, onRemove, src }) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={0.5}
      sx={{ px: 1.5, py: 0.5, minHeight: 36 }}
    >
      <Typography
        component="div"
        sx={{ typography: "s2", flex: 1, minWidth: 0 }}
      >
        {children}
      </Typography>
      {src && (
        <Typography
          noWrap
          sx={{ typography: "s3", color: "text.tertiary", flexShrink: 0 }}
        >
          {src}
        </Typography>
      )}
      {onEdit && (
        <LineAction icon="solar:pen-linear" label="Edit" onClick={onEdit} />
      )}
      {onRemove && (
        <LineAction
          icon="solar:trash-bin-minimalistic-linear"
          label="Remove"
          onClick={onRemove}
        />
      )}
    </Stack>
  );
}
PlanLine.propTypes = {
  children: PropTypes.node,
  empty: PropTypes.bool,
  onEdit: PropTypes.func,
  onRemove: PropTypes.func,
  src: PropTypes.string,
};
DesignReview.propTypes = {
  env: PropTypes.object,
  draftCount: PropTypes.number,
  waiting: PropTypes.bool,
  parked: PropTypes.bool,
  since: PropTypes.number,
  autoMode: PropTypes.bool,
  onApprove: PropTypes.func,
  onContinue: PropTypes.func,
  framed: PropTypes.bool,
  sources: PropTypes.object,
  onAddFiles: PropTypes.func,
  onAddOrg: PropTypes.func,
  /* The proposal changed: { tools?, rules?, actors?, depChoices? } — the
     fields to patch on the environment. Absent: the plan is read-only. */
  onChange: PropTypes.func,
  /* The builder's open question — Approve waits for its answer. */
  question: PropTypes.string,
};
