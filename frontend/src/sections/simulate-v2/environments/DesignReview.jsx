import PropTypes from "prop-types";
import { Children, isValidElement, useEffect, useState } from "react";
import {
  Box,
  Button,
  ButtonBase,
  CircularProgress,
  IconButton,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import SideDrawer from "../components/SideDrawer";
import AddSourceMenu from "../workspace/data/AddSourceMenu";
import SourceDrawer from "../workspace/data/SourceDrawer";
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
  page = false,
  onOpenChat,
  /* With these, a file in Sources opens what the builder took from it. */
  envState = null,
  patch = null,
}) {
  const [anchor, setAnchor] = useState(null);
  const [planOpen, setPlanOpen] = useState(false);
  /* Page mode lists the cases themselves; the first few until asked. */
  const [allCases, setAllCases] = useState(false);
  /* The rule being reworded, and its text so far. */
  const [editing, setEditing] = useState(null);
  const [draft, setDraft] = useState("");
  /* The last removal, so it can be put back. */
  const [undo, setUndo] = useState(null);
  const [openSource, setOpenSource] = useState(null);
  /* Built from scratch there's no agent: the world comes from the words. */
  const scratch = env?.builtFrom?.kind === "scratch";
  /* Files before the build (plan: "Attach a policy and optional CSV"):
     they shape this design and join the first version. */
  const canAdd = !!(onAddFiles && onAddOrg);
  const files = sources?.sources || [];
  /* Reading the files: the parts fill in as they're found, in order, so the
     wait is visible (builder.md §1–3). The proposal is the quick step; the
     build after approval is the slow one. */
  const readUntil = waiting ? env?.designReadUntil || 0 : 0;
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (!readUntil || Date.now() >= readUntil) return undefined;
    const t = setInterval(() => setNow(Date.now()), 200);
    return () => clearInterval(t);
  }, [readUntil]);
  const READ_MS = 3600;
  const readLeft = Math.max(0, readUntil - now);
  const reading = readLeft > 0;
  const READ_AT = {
    Records: 0.2,
    Tools: 0.45,
    Rules: 0.7,
    People: 0.82,
    Scenarios: 0.97,
  };
  const shown = (part) =>
    !reading || READ_MS - readLeft >= READ_AT[part] * READ_MS;
  const readingNames =
    files
      .map((f) => f.name)
      .slice(0, 2)
      .join(", ") + (files.length > 2 ? ` and ${files.length - 2} more` : "");
  /* A file that couldn't be read is listed under Couldn't read, not here. */
  const unreadable = new Set((sources?.unusable || []).map((f) => f.name));
  const readable = files.filter((f) => !unreadable.has(f.name));
  const fileLinks = readable.map((f, i) => (
    <Box component="span" key={f.id || f.name}>
      {i > 0 && ", "}
      {envState ? (
        <Box
          component="button"
          type="button"
          onClick={() => setOpenSource(f.id)}
          title="What the builder took from it"
          sx={{
            ...linkSx,
            font: "inherit",
            border: 0,
            p: 0,
            bgcolor: "transparent",
            cursor: "pointer",
          }}
        >
          {f.name}
        </Box>
      ) : (
        f.name
      )}
    </Box>
  ));
  const sourcesValue = (
    <>
      {readable.length > 0 && fileLinks}
      {readable.length
        ? ` · adds ${
            [
              sources.rules &&
                `${sources.rules} rule${sources.rules === 1 ? "" : "s"}`,
              sources.records &&
                `${sources.records.toLocaleString()} seeded records`,
            ]
              .filter(Boolean)
              .join(" and ") || "background for the agent"
          }${sources.needsInput ? ` · ${sources.needsInput} to answer after the build` : ""}`
        : files.length
          ? "None readable yet"
          : scratch
            ? "Your description"
            : env?.builtFrom?.worldSource?.kind === "template" &&
                !env?.builtFrom?.connectedAgent
              ? `The ready world${env.builtFrom.request ? " and your change" : ""}`
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
  /* With no description, what the builder proposed came from the files. */
  const fromFilesOnly =
    scratch && !env?.builtFrom?.intake?.flow?.trim() && files.length > 0;
  const origin = scratch
    ? fromFilesOnly
      ? "From your files"
      : "From your description"
    : env?.builtFrom?.kind === "template" || env?.builtFrom?.worldSource
      ? "From the ready world"
      : "From the agent's code";
  /* A ready world's records come from it, like its tools and rules. */
  const tableSrc = (t) =>
    t.fromFile || t.source
      ? `From ${t.fromFile || t.source}`
      : origin === "From the ready world" && !t.fromRequest
        ? origin
        : "Generated";
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
  /* Nothing to build: the description named no records and no actions.
     The builder asks rather than inventing a world (builder.md §2). */
  /* What the attached files propose, each item citing its file. */
  const fileTables = sources?.tableItems || [];
  const fileRules = sources?.ruleItems || [];
  const unusable = sources?.unusable || [];
  /* Nothing to build until it knows what the agent does: no tools means no
     actions to test, whatever records or rules the files hold. The builder
     asks instead of inventing a world (builder.md §2). */
  const emptyWorld = scratch && !(env?.tools || []).length;
  const status = parked
    ? "Parked"
    : waiting && emptyWorld
      ? "Needs detail"
      : waiting
        ? "Awaiting approval"
        : "Approved";
  const tools = env?.tools || [];
  const rules = env?.rules || [];
  const actors = env?.actors || [];
  const tables = env?.seed?.tables || [];
  /* Behaviors the description asks for that the world can't stand up. */
  const deps = unsupportedOf(env?.builtFrom?.intake?.flow);
  const choiceOf = (d) => env?.depChoices?.[d.id] || null;
  const pending = deps.filter((d) => !choiceOf(d));
  const groups = scenarioGroupsOf(env, draftCount);
  const cases = env?.starterScenarios || [];
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
  /* Approve (or Continue, once parked): the page's one primary action. */
  const approveControl = (
    <>
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
                : emptyWorld
                  ? "Tell the builder what the agent works with first — there is nothing to build yet."
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
                disabled={!!question || emptyWorld || reading}
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
    </>
  );
  /* What blocks Approve, said once, with the action that clears it
     (design system: a disabled main button gets a banner that says why). */
  const blocker =
    !waiting || reading
      ? null
      : parked
        ? {
            text: `Waiting on you since ${new Date(since).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })}. Continue picks up the same design.`,
            cta: "Continue",
            onClick: onContinue,
          }
        : question
          ? {
              text: `Answer the builder's question first: ${question}`,
              cta: "Answer in builder",
              onClick: onOpenChat,
            }
          : emptyWorld
            ? {
                text: files.length
                  ? `${readable.map((f) => f.name).join(", ") || readingNames} ${readable.length === 1 ? "doesn't" : "don't"} say what the agent does. Describe it — for example “it triages new issues: labels, assigns and closes duplicates” — or add a file that does.`
                  : "There's nothing to build yet. Say what the agent does and works with — for example “customers and their orders; it can look up an order and refund up to $200”.",
                cta: "Describe in builder",
                onClick: onOpenChat,
              }
            : null;

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
  /* The whole plan: shown in a drawer from the compact strip, or as the page
     itself while the design waits (page mode) — nothing is built yet, so the
     proposal is all there is to look at. */
  const planBody = (
    <>
      <Stack spacing={page ? 3 : 2.5}>
        <>
          <PlanRow
            label="Records"
            pending={!shown("Records")}
            reading={readingNames}
          >
            {tables.length || fileTables.length ? (
              [
                ...tables.map((t) => (
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
                )),
                ...fileTables.map((t) => (
                  <PlanLine key={`file-${t.name}`} src={`From ${t.src}`}>
                    <Box component="span" sx={{ fontWeight: 500 }}>
                      {t.name}
                    </Box>
                    <Box
                      component="span"
                      sx={{ color: "text.secondary", ml: 0.75 }}
                    >
                      {`${(t.rows || 0).toLocaleString()} rows`}
                    </Box>
                  </PlanLine>
                )),
              ]
            ) : (
              <PlanLine empty>
                {files.length
                  ? "None proposed — neither your files nor a description name any"
                  : "No records — the description named none"}
              </PlanLine>
            )}
          </PlanRow>
          <PlanRow
            label="Tools"
            pending={!shown("Tools")}
            reading={readingNames}
          >
            {tools.length ? (
              tools.map((t) => (
                <PlanLine
                  key={t.name}
                  src={t.designSrc || t.source || origin}
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
                  <Box component="span" sx={{ color: "text.secondary" }}>
                    {`${d.request} — can't be built here, left out`}
                  </Box>
                </PlanLine>
              ))}
          </PlanRow>
          <PlanRow
            label="Rules"
            pending={!shown("Rules")}
            reading={readingNames}
          >
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
                    src={
                      (env?.requestRules || []).includes(r)
                        ? "From your change"
                        : ruleSrc
                    }
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
            ) : !fileRules.length ? (
              <PlanLine empty>None yet</PlanLine>
            ) : null}
            {fileRules.map((r) => (
              <PlanLine key={r.id} src={`From ${r.src}`}>
                {r.text}
              </PlanLine>
            ))}
          </PlanRow>
          <PlanRow
            label="People"
            pending={!shown("People")}
            reading={readingNames}
          >
            <PlanLine>
              Caller
              <Box component="span" sx={{ color: "text.secondary", ml: 0.75 }}>
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
            pending={!shown("Scenarios")}
            reading={readingNames}
            count={
              emptyWorld ? 0 : groups.reduce((n, g) => n + g.n, 0) || draftCount
            }
          >
            {emptyWorld ? (
              <PlanLine empty>
                Written once it knows what the agent does
              </PlanLine>
            ) : page && cases.length ? (
              [
                ...cases.slice(0, allCases ? cases.length : 6).map((r) => (
                  <PlanLine key={r.id || r.key} src={r.useCase || r.branch}>
                    {r.summary || r.task || r.title}
                  </PlanLine>
                )),
                cases.length > 6 && (
                  <PlanLine key="more" empty>
                    <Box
                      component="button"
                      type="button"
                      onClick={() => setAllCases((v) => !v)}
                      sx={{
                        ...linkSx,
                        typography: "s1",
                        border: 0,
                        p: 0,
                        bgcolor: "transparent",
                        cursor: "pointer",
                      }}
                    >
                      {allCases
                        ? "Show fewer"
                        : `Show all ${cases.length} — written and checked when it builds`}
                    </Box>
                  </PlanLine>
                ),
              ]
            ) : groups.length ? (
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
        <Stack direction="row" alignItems="center" spacing={1} sx={{ mt: 1.5 }}>
          <Typography sx={{ typography: "s2", color: "text.secondary" }}>
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
    </>
  );
  /* Page: the design before anything exists (builder.md §3). A header with
     the one action, what blocks it, the brief as key-value facts, then each
     part of the proposal as an unboxed list (design system: Page, Panel,
     Table, Banner). */
  if (page) {
    const bf = env?.builtFrom || {};
    /* Built for an agent: what to test is its description, and the agent
       is named — only a scratch build has none yet. */
    const flow = (bf.intake?.flow || bf.testRequest || bf.request || "").trim();
    const agentName = bf.agentName || bf.connectedAgent?.name || null;
    const agentVersion = bf.agentVersion?.label || bf.agentVersion || null;
    const brief = [
      [
        "Description",
        flow ||
          (files.length ? "None — the design is read from your files" : "—"),
      ],
      ["Sources", sourcesValue],
      ...(unusable.length
        ? [
            [
              "Couldn't read",
              unusable
                .map((f) => `${f.name} — ${f.statusNote || "unreadable"}`)
                .join(" · "),
            ],
          ]
        : []),
      ["Channel", env?.surface === "voice" ? "Voice" : "Chat"],
      [
        "Agent",
        agentName
          ? [agentName, typeof agentVersion === "string" ? agentVersion : null]
              .filter(Boolean)
              .join(" · ")
          : "None yet — connect one after you save",
      ],
    ];
    return (
      <Box sx={{ px: 3, py: 2.5 }}>
        <Stack direction="row" alignItems="flex-start" spacing={2}>
          <Box flex={1} minWidth={0}>
            <Typography sx={{ typography: "m2", fontWeight: 600 }}>
              Review the design
            </Typography>
            <Typography
              sx={{ typography: "s1", color: "text.tertiary", mt: 0.25 }}
            >
              {reading
                ? `Reading ${readingNames} — the design fills in as it's read.`
                : "Nothing is built until you approve — a clean build saves as v1."}
            </Typography>
          </Box>
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
          <Box sx={{ flexShrink: 0 }}>{approveControl}</Box>
        </Stack>

        {blocker && (
          <Stack
            direction="row"
            alignItems="center"
            spacing={1.25}
            sx={{
              mt: 2,
              px: 1.5,
              py: 1,
              borderRadius: "8px",
              border: "1px solid",
              borderColor: "status.warning.border",
              bgcolor: "status.warning.bg",
            }}
          >
            <Iconify
              icon="solar:danger-triangle-linear"
              width={16}
              sx={{ color: "status.warning.fg", flexShrink: 0 }}
            />
            <Typography
              sx={{ typography: "s1", color: "text.primary", flex: 1 }}
            >
              {blocker.text}
            </Typography>
            {blocker.onClick && (
              <Button
                size="small"
                variant="outlined"
                onClick={blocker.onClick}
                sx={{
                  color: "text.primary",
                  borderColor: "divider",
                  bgcolor: "background.paper",
                  flexShrink: 0,
                }}
              >
                {blocker.cta}
              </Button>
            )}
          </Stack>
        )}

        <Box
          sx={{
            mt: 2,
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "8px",
            bgcolor: "background.paper",
          }}
        >
          <Stack
            direction="row"
            alignItems="center"
            sx={{
              height: 36,
              px: 1.5,
              borderBottom: "1px solid",
              borderColor: "divider",
            }}
          >
            <Typography sx={{ typography: "m3", fontWeight: 500, flex: 1 }}>
              Brief
            </Typography>
            <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
              What the design is built from
            </Typography>
          </Stack>
          <Box
            component="table"
            sx={{ width: 1, borderCollapse: "collapse", my: 0.5 }}
          >
            <Box component="tbody">
              {brief.map(([k, v]) => (
                <Box component="tr" key={k}>
                  <Box
                    component="td"
                    sx={{
                      typography: "s1",
                      color: "text.tertiary",
                      width: 120,
                      px: 1.5,
                      py: 0.75,
                      verticalAlign: "top",
                    }}
                  >
                    {k}
                  </Box>
                  <Box
                    component="td"
                    sx={{
                      typography: "s1",
                      color: "text.primary",
                      px: 1.5,
                      py: 0.75,
                    }}
                  >
                    {v}
                  </Box>
                </Box>
              ))}
            </Box>
          </Box>
        </Box>

        {pending.length > 0 && (
          <Box sx={{ mt: 3 }}>
            <PlanRow
              label="Can't be built here — simulate it instead"
              count={pending.length}
            >
              {pending.map((d) => (
                <PlanLine key={d.id}>
                  <Stack direction="row" alignItems="center" spacing={1}>
                    <Box sx={{ flex: 1, minWidth: 0 }}>
                      <Box component="span" sx={{ fontWeight: 500 }}>
                        {d.request}
                      </Box>
                      <Box
                        component="span"
                        sx={{ color: "text.secondary", ml: 0.75 }}
                      >
                        {`${d.why} Adds ${d.alt.name}.`}
                      </Box>
                    </Box>
                    {onChange && (
                      <>
                        <Button
                          size="small"
                          variant="outlined"
                          onClick={() => onChange(simulateDepPatch(env, d))}
                          sx={actionSx}
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
                </PlanLine>
              ))}
            </PlanRow>
          </Box>
        )}

        <Box sx={{ mt: 3 }}>{planBody}</Box>
        {envState && (
          <SourceDrawer
            open={!!openSource}
            onClose={() => setOpenSource(null)}
            env={env}
            envState={envState}
            patch={patch}
            sourceId={openSource}
            readOnly={!patch}
          />
        )}
      </Box>
    );
  }

  /* Compact: title, status and the one action on a row; the proposal as
     one wrapping line of facts; one line on what happens next. */
  const note = parked
    ? `Waiting on you since ${new Date(since).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })}.`
    : waiting && question
      ? `One question first, in the builder chat: ${question}`
      : waiting && emptyWorld
        ? "Nothing to build yet: your description names no records and no actions. Tell the builder what the agent works with — for example “customers and their orders; it can look up and refund an order” — or add a file."
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
        {approveControl}
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

      {page && (
        <Typography
          sx={{
            typography: "s3",
            color: emptyWorld ? "status.warning.fg" : "text.tertiary",
            mt: 0.75,
          }}
        >
          {note}
        </Typography>
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

      {waiting && !page && (
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
      {waiting && page && (
        <Box
          sx={{
            mt: 1.5,
            pt: 1.5,
            borderTop: "1px solid",
            borderColor: "divider",
          }}
        >
          {planBody}
        </Box>
      )}
      {waiting && !page && (
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
              {planBody}
            </Box>
          </Stack>
        </SideDrawer>
      )}

      {!page && (
        <Typography sx={{ typography: "s3", color: "text.tertiary", mt: 0.5 }}>
          {note}
        </Typography>
      )}
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
function PlanRow({ label, count, children, pending = false, reading = "" }) {
  const n = pending
    ? ""
    : count ??
      Children.toArray(children).filter(
        (c) => isValidElement(c) && !c.props.empty,
      ).length;
  /* Design system list rows: the heading carries the count; rows sit on the
     page with a hairline under each one and no box around them. */
  return (
    <Box>
      <Typography sx={{ typography: "m3", fontWeight: 500, mb: 0.5 }}>
        {label}
        <Box
          component="span"
          sx={{
            color: "text.tertiary",
            fontWeight: 400,
            ml: 0.75,
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {n}
        </Box>
      </Typography>
      <Box
        sx={{
          borderTop: "1px solid",
          borderColor: "divider",
          "& > *": { borderBottom: "1px solid", borderColor: "divider" },
        }}
      >
        {pending ? (
          <Stack
            direction="row"
            alignItems="center"
            spacing={1}
            sx={{ px: 1.5, py: 1.25 }}
          >
            <CircularProgress size={12} thickness={5} />
            <Typography sx={{ typography: "s1", color: "text.tertiary" }}>
              {reading ? `Reading ${reading}…` : "Reading…"}
            </Typography>
          </Stack>
        ) : (
          children
        )}
      </Box>
    </Box>
  );
}
PlanRow.propTypes = {
  label: PropTypes.string,
  count: PropTypes.number,
  children: PropTypes.node,
  /* Still being read from the files. */
  pending: PropTypes.bool,
  reading: PropTypes.string,
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

function PlanLine({ children, onEdit, onRemove, src, empty = false }) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={0.5}
      sx={{
        px: 1.5,
        minHeight: 36,
        /* Actions stay quiet until the row is pointed at or focused. */
        "& .pl-actions": { opacity: 0 },
        "&:hover": { bgcolor: "action.hover" },
        "&:hover .pl-actions, &:focus-within .pl-actions": { opacity: 1 },
      }}
    >
      <Typography
        component="div"
        noWrap={!empty}
        sx={{
          typography: "s1",
          color: empty ? "text.tertiary" : "text.primary",
          flex: 1,
          minWidth: 0,
        }}
      >
        {children}
      </Typography>
      {src && (
        <Typography
          noWrap
          sx={{
            typography: "s3",
            color: "text.tertiary",
            flexShrink: 0,
            pl: 1.5,
          }}
        >
          {src}
        </Typography>
      )}
      {/* A fixed slot on every row, so the source column lines up whether
          or not the row can be edited. */}
      <Stack
        direction="row"
        justifyContent="flex-end"
        className="pl-actions"
        sx={{ flexShrink: 0, width: 64 }}
      >
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
  envState: PropTypes.object,
  patch: PropTypes.func,
  env: PropTypes.object,
  draftCount: PropTypes.number,
  waiting: PropTypes.bool,
  parked: PropTypes.bool,
  since: PropTypes.number,
  autoMode: PropTypes.bool,
  onApprove: PropTypes.func,
  /* The proposal as the page itself, not a strip with a drawer. */
  page: PropTypes.bool,
  /* Opens the builder chat (the banner's "Describe in builder"). */
  onOpenChat: PropTypes.func,
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
