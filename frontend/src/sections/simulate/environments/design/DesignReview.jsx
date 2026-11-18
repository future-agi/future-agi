import PropTypes from "prop-types";
import { Children, isValidElement, useState } from "react";
import {
  Box,
  Button,
  ButtonBase,
  CircularProgress,
  Stack,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import PageHeader from "src/components/page-header";
import { formatFileSize } from "src/utils/utils";
import SectionCard from "../components/SectionCard";
import SideDrawer from "../components/SideDrawer";
import UiOnlyFlag from "../components/UiOnlyFlag";
import BuildFilesField from "../buildDrawer/forms/shared/BuildFilesField";
import { uiOnlyToast } from "../buildDrawer/starts/startUtils";
import { extOf } from "../buildDrawer/forms/shared/buildFiles";
import { ROLES } from "../workspace/sources/sources.constants";

const linkSx = {
  typography: "s1",
  p: 0,
  verticalAlign: "baseline",
  textDecoration: "underline",
  textUnderlineOffset: 2,
  color: "text.primary",
  "&:hover": { color: "text.secondary" },
};

/* One part of the design: a heading with its count, then its lines on
   hairlines. While the files are read it shows a spinner instead. */
function PlanRow({ label, count, reading, children }) {
  const n = reading
    ? ""
    : count ??
      Children.toArray(children).filter(
        (c) => isValidElement(c) && !c.props.empty,
      ).length;
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
        {reading ? (
          <Stack
            direction="row"
            alignItems="center"
            spacing={1}
            sx={{ px: 1.5, py: 1.25 }}
          >
            <CircularProgress size={12} thickness={5} />
            <Typography sx={{ typography: "s1", color: "text.tertiary" }}>
              {`Reading ${reading}…`}
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
  label: PropTypes.string.isRequired,
  count: PropTypes.number,
  // The files still being read, named; empty once read.
  reading: PropTypes.string,
  children: PropTypes.node,
};

/* One line of a part, with where it came from at the end. */
function PlanLine({ children, src, empty = false }) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={0.5}
      sx={{ px: 1.5, minHeight: 36, "&:hover": { bgcolor: "action.hover" } }}
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
    </Stack>
  );
}
PlanLine.propTypes = {
  children: PropTypes.node,
  src: PropTypes.string,
  empty: PropTypes.bool,
};

const Quiet = ({ children }) => (
  <Box component="span" sx={{ color: "text.secondary", ml: 0.75 }}>
    {children}
  </Box>
);
Quiet.propTypes = { children: PropTypes.node };

// Who sees a file in a run, by what it's read as.
const READS = {
  Records: "The agent sees these rows only through the world's tools.",
  Rules:
    "The agent never sees the file, only what the world's tools return from it.",
  Background: "Yes, it reads the file directly.",
  "Grader answers": "No. Only the grader reads it.",
};

/* What the builder took from one file. The file drawer replaces this once it
   exists, opening the file itself. */
function FilePreview({ file, summary, onClose }) {
  const from = `From ${file?.name}`;
  const fromIt = (x) => x.src === from || x.src.startsWith(`${from} ·`);
  const gave = [
    ...(summary.records || [])
      .filter(fromIt)
      .map((t) => `${t.name} · ${t.rows.toLocaleString()} rows`),
    ...(summary.rules || []).filter(fromIt).map((r) => r.text),
  ];
  const facts = file
    ? [
        [
          "Used as",
          ROLES.find((r) => r.label === file.role)?.option || file.role,
        ],
        ["Agent reads it", READS[file.role]],
      ]
    : [];
  return (
    <SideDrawer open={!!file} onClose={onClose} width={480}>
      {file && (
        <Stack spacing={2} sx={{ p: 2.5 }}>
          <Box sx={{ pr: 4 }}>
            <Typography sx={{ typography: "m1" }}>{file.name}</Typography>
            <Typography sx={{ typography: "s1", color: "text.secondary" }}>
              {`${extOf(file.name).toUpperCase()} · ${formatFileSize(file.size)} · not in the environment yet`}
            </Typography>
          </Box>
          <Box>
            {facts.map(([k, v]) => (
              <Stack key={k} direction="row" sx={{ py: 0.5 }}>
                <Typography
                  sx={{
                    typography: "s1",
                    color: "text.tertiary",
                    width: 120,
                    flexShrink: 0,
                  }}
                >
                  {k}
                </Typography>
                <Typography sx={{ typography: "s1", flex: 1, minWidth: 0 }}>
                  {v}
                </Typography>
              </Stack>
            ))}
          </Box>
          <PlanRow
            label="What the builder took from it"
            reading={summary.readingParts.length ? file.name : ""}
          >
            {gave.length ? (
              gave.map((g) => <PlanLine key={g}>{g}</PlanLine>)
            ) : (
              <PlanLine empty>
                Nothing is graded from it. The agent reads it as background.
              </PlanLine>
            )}
          </PlanRow>
        </Stack>
      )}
    </SideDrawer>
  );
}
FilePreview.propTypes = {
  file: PropTypes.object,
  summary: PropTypes.object.isRequired,
  onClose: PropTypes.func.isRequired,
};

/**
 * The proposed design, before anything is built: the brief, then the
 * records, tools, rules, people and scenarios, each line citing where it came
 * from. Parts fill in as the files are read. "Approve and build" is the one
 * action; it waits for the reading and for something to build.
 */
export default function DesignReview({
  draft,
  summary,
  onAddFiles,
  onDepChoice,
}) {
  const [adding, setAdding] = useState(false);
  const [openFile, setOpenFile] = useState(null);
  // The first few cases, until asked for all of them.
  const [allCases, setAllCases] = useState(false);
  const {
    brief,
    sources,
    unusable,
    readingParts,
    readingNames,
    approveBlocked,
    deps,
  } = summary;
  const reading = readingParts.length > 0;
  const pendingDeps = deps.filter((d) => !d.choice);
  const pending = (part) => (readingParts.includes(part) ? readingNames : "");
  const forAgent = !!draft.agent;

  const addButton = (
    <Button
      size="small"
      variant="outlined"
      onClick={() => setAdding(true)}
      startIcon={<Iconify icon="solar:add-circle-linear" width={14} />}
      sx={{ ml: 1, color: "text.primary", borderColor: "divider" }}
    >
      {draft.files.length ? "Add another" : "Add files"}
    </Button>
  );
  const rows = [
    [
      "Description",
      brief.description ||
        (draft.files.length
          ? "None. The design is read from your files."
          : "None"),
    ],
    [
      "Sources",
      <>
        {sources.map((f, i) => (
          <Box component="span" key={f.id}>
            {i > 0 && ", "}
            <ButtonBase
              onClick={() => setOpenFile(f)}
              aria-label={`What the builder took from ${f.name}`}
              sx={linkSx}
            >
              {f.name}
            </ButtonBase>
          </Box>
        ))}
        {sources.length ? ` · ${brief.sourcesLine}` : brief.sourcesLine}
        {addButton}
      </>,
    ],
    ...(unusable.length
      ? [
          [
            "Couldn't read",
            unusable.map((f) => `${f.name}: ${f.note}`).join(" · "),
          ],
        ]
      : []),
    ["Channel", brief.channel],
    ["Agent", brief.agent || "None yet. Connect one after you save."],
  ];

  const readable = sources.map((f) => f.name).join(", ");
  const blocker =
    !reading && approveBlocked
      ? draft.files.length && readable
        ? `${readable} ${sources.length === 1 ? "doesn't" : "don't"} say what the agent does. Describe it, for example "it triages new issues: labels, assigns and closes duplicates", or add a file that does.`
        : `There's nothing to build yet. Say what the agent does and works with, for example "customers and their orders; it can look up an order and refund up to $200".`
      : null;

  const { records, tools, rules, people, scenarios } = summary;
  const extraScenarios = Math.max(
    0,
    (brief.scenarioCount || 0) - (scenarios?.length || 0),
  );

  return (
    <Box sx={{ px: 3, py: 2.5 }}>
      <PageHeader
        title="Review the design"
        description={
          reading
            ? `Reading ${readingNames}. The design fills in as it's read.`
            : "Nothing is built until you approve. A clean build saves as v1."
        }
        actions={
          <>
            <UiOnlyFlag feature="designReview" />
            <Button
              size="small"
              variant="contained"
              color="primary"
              disabled={reading || approveBlocked}
              onClick={() => uiOnlyToast("building from a design")}
              startIcon={
                <Iconify icon="solar:check-circle-linear" width={14} />
              }
            >
              Approve and build
            </Button>
          </>
        }
        sx={{ mb: 0 }}
      />

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
          <Typography sx={{ typography: "s1", color: "text.primary", flex: 1 }}>
            {blocker}
          </Typography>
          <Button
            size="small"
            variant="outlined"
            onClick={() => setAdding(true)}
            sx={{
              color: "text.primary",
              borderColor: "divider",
              bgcolor: "background.paper",
              flexShrink: 0,
            }}
          >
            Add files
          </Button>
        </Stack>
      )}

      <SectionCard
        title="Brief"
        action={
          <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
            What the design is built from
          </Typography>
        }
        sx={{ mt: 2 }}
      >
        <Box sx={{ py: 0.5 }}>
          {rows.map(([k, v]) => (
            <Stack key={k} direction="row" sx={{ px: 1.5, py: 0.75 }}>
              <Typography
                sx={{
                  typography: "s1",
                  color: "text.tertiary",
                  width: 120,
                  flexShrink: 0,
                }}
              >
                {k}
              </Typography>
              <Typography
                component="div"
                sx={{ typography: "s1", flex: 1, minWidth: 0 }}
              >
                {v}
              </Typography>
            </Stack>
          ))}
        </Box>
      </SectionCard>

      {adding && (
        <Box sx={{ mt: 2 }}>
          {/* The page's own Add source menu replaces this picker later. */}
          <BuildFilesField
            files={[]}
            onChange={(rows) => {
              onAddFiles(rows);
              setAdding(false);
            }}
          />
          <Button
            size="small"
            onClick={() => setAdding(false)}
            sx={{ mt: 0.5, color: "text.secondary" }}
          >
            Cancel
          </Button>
        </Box>
      )}

      <Stack spacing={3} sx={{ mt: 3 }}>
        {pendingDeps.length > 0 && (
          <PlanRow
            label="Can't be built here, simulate it instead"
            count={pendingDeps.length}
          >
            {pendingDeps.map((d) => (
              <PlanLine key={d.id}>
                <Stack direction="row" alignItems="center" spacing={1}>
                  <Box sx={{ flex: 1, minWidth: 0 }}>
                    <Box component="span" sx={{ fontWeight: 500 }}>
                      {d.request}
                    </Box>
                    <Quiet>{`${d.why} Adds ${d.alt.name}.`}</Quiet>
                  </Box>
                  <Button
                    size="small"
                    variant="outlined"
                    onClick={() => onDepChoice(d.id, "simulate")}
                    sx={{ color: "text.primary", borderColor: "divider" }}
                  >
                    Simulate it
                  </Button>
                  <Button
                    size="small"
                    variant="outlined"
                    onClick={() => onDepChoice(d.id, "skip")}
                    sx={{ color: "text.primary", borderColor: "divider" }}
                  >
                    Leave out
                  </Button>
                </Stack>
              </PlanLine>
            ))}
          </PlanRow>
        )}

        <PlanRow label="Records" reading={pending("records")}>
          {records?.length ? (
            records.map((t) => (
              <PlanLine key={t.name} src={t.src}>
                <Box component="span" sx={{ fontWeight: 500 }}>
                  {t.name}
                </Box>
                <Quiet>
                  {[`${t.rows.toLocaleString()} rows`, t.note]
                    .filter(Boolean)
                    .join(" · ")}
                </Quiet>
              </PlanLine>
            ))
          ) : (
            <PlanLine empty>
              {draft.files.length
                ? "None proposed. Neither your files nor a description name any."
                : "No records. The description named none."}
            </PlanLine>
          )}
        </PlanRow>

        <PlanRow label="Tools" reading={pending("tools")}>
          {tools?.length ? (
            tools.map((t) => (
              <PlanLine key={t.name} src={t.src}>
                <Box component="span" sx={{ fontWeight: 500 }}>
                  {t.name}
                </Box>
                <Quiet>{t.desc}</Quiet>
              </PlanLine>
            ))
          ) : (
            <PlanLine empty>
              {forAgent
                ? "Read from the agent's code when it builds"
                : "None yet"}
            </PlanLine>
          )}
          {tools &&
            deps
              .filter((d) => d.choice === "skip")
              .map((d) => (
                <PlanLine key={d.id} empty>
                  {`${d.request}: can't be built here, left out`}
                </PlanLine>
              ))}
        </PlanRow>

        <PlanRow label="Rules" reading={pending("rules")}>
          {rules?.length ? (
            rules.map((r) => (
              <PlanLine key={`${r.src}-${r.text}`} src={r.src}>
                {r.text}
              </PlanLine>
            ))
          ) : (
            <PlanLine empty>None yet</PlanLine>
          )}
        </PlanRow>

        <PlanRow label="People" reading={pending("people")}>
          {(people || []).map((p) => (
            <PlanLine key={p.name} src={p.src}>
              {p.name}
              {p.note && <Quiet>{p.note}</Quiet>}
            </PlanLine>
          ))}
        </PlanRow>

        <PlanRow
          label="Scenarios"
          reading={pending("scenarios")}
          count={(scenarios?.length || 0) + extraScenarios}
        >
          {scenarios?.length ? (
            [
              ...scenarios.slice(0, allCases ? undefined : 6).map((s) => (
                <PlanLine key={s.text} src={s.src}>
                  {s.text}
                </PlanLine>
              )),
              scenarios.length > 6 && (
                <PlanLine key="all" empty>
                  <ButtonBase
                    onClick={() => setAllCases((v) => !v)}
                    sx={linkSx}
                  >
                    {allCases
                      ? "Show fewer"
                      : `Show all ${scenarios.length}, written and checked when it builds`}
                  </ButtonBase>
                </PlanLine>
              ),
              extraScenarios > 0 && (
                <PlanLine key="more" empty>
                  {`${extraScenarios} more written when it builds`}
                </PlanLine>
              ),
            ]
          ) : (
            <PlanLine empty>Written once it knows what the agent does</PlanLine>
          )}
        </PlanRow>
      </Stack>

      <FilePreview
        file={openFile}
        summary={summary}
        onClose={() => setOpenFile(null)}
      />
    </Box>
  );
}
DesignReview.propTypes = {
  draft: PropTypes.object.isRequired,
  // designSummary(draft, now)
  summary: PropTypes.object.isRequired,
  // Plain file rows ({ id, name, size, type }) to add to the design.
  onAddFiles: PropTypes.func.isRequired,
  // (depId, "simulate" | "skip") for a behaviour that can't be built here.
  onDepChoice: PropTypes.func.isRequired,
};
