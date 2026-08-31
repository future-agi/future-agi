import { useState } from "react";
import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import { alpha } from "@mui/material/styles";
import { fetchScenarioFile } from "src/api/al-environment/alEnvironment";
import Pane from "../parts/Pane";
import Field from "../parts/Field";
import Tag from "../parts/Tag";
import XCard from "../parts/XCard";
import CodeBlock, { languageOf } from "../parts/CodeBlock";
import DataTable from "../parts/DataTable";
import { ALK_MONO } from "../alkTokens";

/**
 * Three states, not two. `null` means the gates could not be run at all because no world
 * exists yet — which is different from a scenario that was checked and found not ready.
 */
const verdictOf = (validated) => {
  if (validated === true) return { kind: "pass", label: "validated" };
  if (validated === null || validated === undefined)
    return { kind: "soft", label: "unchecked" };
  return { kind: "fail", label: "not ready" };
};

/**
 * The order is the order the harness proves them in: a world that is ready, then a solution
 * that passes against it, then checks that could actually have failed. Reading them in any
 * other order tells you nothing about which link broke first.
 */
const GATES = [
  ["ready", "world is ready"],
  ["solvable", "solution passes"],
  ["not_vacuous", "checks can fail"],
];

/** ✓ / ✗ / ? — a dashed, muted lamp for "never ran" so it never reads as a failure. */
const GateLamp = ({ held, label }) => {
  const on = held === true;
  const off = held === false;
  const tone = on ? "accent.pass" : "accent.fail";
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={0.5}
      title={`${label}: ${on ? "holds" : off ? "fails" : "unknown"}`}
      sx={{
        fontFamily: ALK_MONO,
        fontSize: 11.7,
        color: on || off ? tone : "text.secondary",
      }}
    >
      <Box
        component="span"
        aria-hidden
        sx={{
          display: "grid",
          placeItems: "center",
          width: 18,
          height: 18,
          borderRadius: "50%",
          fontSize: 10.5,
          border: "1px solid",
          borderStyle: on || off ? "solid" : "dashed",
          borderColor: on || off ? tone : "divider",
          bgcolor: (theme) =>
            on || off
              ? alpha(theme.palette[on ? "success" : "error"].main, 0.14)
              : "transparent",
        }}
      >
        {on ? "✓" : off ? "✗" : "?"}
      </Box>
      <Box component="span">{label}</Box>
    </Stack>
  );
};

GateLamp.propTypes = {
  held: PropTypes.bool,
  label: PropTypes.string.isRequired,
};

/** The chip shape the harness uses for any "open this" affordance. */
const Chip = ({ onClick, active, children }) => (
  <Box
    component="button"
    type="button"
    onClick={onClick}
    sx={{
      px: 1.25,
      py: 0.4,
      border: "1px solid",
      borderColor: active ? "text.secondary" : "divider",
      borderRadius: 20,
      background: "none",
      bgcolor: active ? "action.selected" : "transparent",
      color: active ? "text.primary" : "text.secondary",
      fontFamily: ALK_MONO,
      fontSize: 12,
      cursor: "pointer",
      "&:hover": { color: "text.primary", borderColor: "text.secondary" },
    }}
  >
    {children}
  </Box>
);

Chip.propTypes = {
  onClick: PropTypes.func.isRequired,
  active: PropTypes.bool,
  children: PropTypes.node,
};

const ScenarioCard = ({ scenario, ran, onSeeRun }) => {
  // Only one file is held open at a time, the way the reference replaces its file-view node —
  // a scenario folder is small, and a stack of open files buries the card's own story.
  const [opened, setOpened] = useState(null);

  const verdict = verdictOf(scenario.validated);
  const solution = scenario.solution || [];
  const checks = scenario.checks || [];
  const variables = scenario.variables || {};
  const files = scenario.files || [];

  const openFile = async (path) => {
    if (opened?.path === path) {
      setOpened(null);
      return;
    }
    const got = await fetchScenarioFile(scenario.name, path);
    setOpened({ path, text: got?.source || got?.error || "(empty)" });
  };

  return (
    <XCard
      // A scenario that failed its gates is the one you opened this tab to read.
      open={!scenario.validated}
      // The verdict rides in the title slot purely so the summary's own gap spaces it — the
      // reference leads the row with it, ahead of the scenario's name.
      title={<Tag kind={verdict.kind}>{verdict.label}</Tag>}
      tags={
        <>
          <Typography
            component="span"
            sx={{
              fontFamily: ALK_MONO,
              fontSize: 13,
              fontWeight: 600,
              color: "text.primary",
            }}
          >
            {scenario.name}
          </Typography>
          {scenario.use_case && <Tag kind="soft">{scenario.use_case}</Tag>}
          {ran && (
            <Tag kind={ran.passed ? "pass" : "fail"}>
              {ran.passed ? "ran: pass" : "ran: fail"}
            </Tag>
          )}
        </>
      }
    >
      <Typography
        component="div"
        sx={{ fontFamily: ALK_MONO, fontSize: 12, color: "text.secondary" }}
      >
        {`${solution.length}-step solution · ${checks.length} checks`}
      </Typography>

      <Field label="validation">
        <Stack direction="row" flexWrap="wrap" useFlexGap spacing={1.5}>
          {GATES.map(([key, label]) => (
            <GateLamp
              key={key}
              held={(scenario.gates || {})[key]}
              label={label}
            />
          ))}
        </Stack>
      </Field>

      {scenario.why && (
        <Box
          sx={{
            px: 0.75,
            py: 0.5,
            borderRadius: "3px",
            fontFamily: ALK_MONO,
            fontSize: 11.7,
            color: "accent.fail",
            bgcolor: (theme) => alpha(theme.palette.error.main, 0.1),
            // A gate's explanation arrives with its own line breaks and can quote a
            // long unbroken token; both have to wrap rather than push the card wide.
            whiteSpace: "pre-wrap",
            overflowWrap: "anywhere",
          }}
        >
          {scenario.why}
        </Box>
      )}

      <Field label="the person is told">
        <Box
          component="blockquote"
          sx={{
            m: 0,
            pl: 1,
            py: 0.2,
            borderLeft: "3px solid",
            borderColor: "divider",
            fontSize: 13.5,
            color: "text.primary",
          }}
        >
          {scenario.instruction}
        </Box>
      </Field>

      {scenario.tests && (
        <Field label="what this tests">
          <Typography
            sx={{ fontStyle: "italic", fontSize: 13, color: "text.secondary" }}
          >
            {scenario.tests}
          </Typography>
        </Field>
      )}

      {Object.keys(variables).length > 0 && (
        <Field label="fills the prompt's slots">
          <DataTable
            columns={["Slot", "Value"]}
            rows={Object.entries(variables).map(([slot, value]) => ({
              Slot: slot,
              Value: String(value),
            }))}
          />
        </Field>
      )}

      <Field label="the reference solution — proves it can be passed, never run against the agent">
        <Box
          component="ol"
          sx={{
            m: 0,
            pl: 2.5,
            fontFamily: ALK_MONO,
            fontSize: 12,
            "& li": {
              py: 0.3,
              borderBottom: "1px solid",
              borderColor: "divider",
              "&:last-of-type": { borderBottom: 0 },
            },
            "& li::marker": { color: "text.secondary", fontSize: 10.5 },
          }}
        >
          {solution.map((step, index) => {
            const args = Object.keys(step.arguments || {});
            return (
              // Steps are ordered and unnamed, so their position is the only stable key.
              // eslint-disable-next-line react/no-array-index-key
              <Box component="li" key={index}>
                {args.length === 0 ? (
                  <Box component="span" sx={{ color: "text.primary" }}>
                    {step.tool}
                  </Box>
                ) : (
                  <Box
                    component="details"
                    sx={{ "&[open] .alk-step-mark": { transform: "rotate(90deg)" } }}
                  >
                    <Box
                      component="summary"
                      sx={{
                        listStyle: "none",
                        "&::-webkit-details-marker": { display: "none" },
                        cursor: "pointer",
                        display: "flex",
                        alignItems: "baseline",
                        gap: 0.75,
                        "&:hover": { bgcolor: "action.hover" },
                      }}
                    >
                      <Box
                        className="alk-step-mark"
                        component="span"
                        aria-hidden
                        sx={{
                          color: "text.secondary",
                          fontSize: 10,
                          transition: "transform 120ms",
                        }}
                      >
                        ▸
                      </Box>
                      <Box component="span" sx={{ color: "text.primary" }}>
                        {step.tool}
                      </Box>
                      <Box component="span" sx={{ color: "text.disabled", fontSize: 10.5 }}>
                        {args.length === 1 ? args[0] : `${args.length} arguments`}
                      </Box>
                    </Box>
                    <Box sx={{ py: 0.5 }}>
                      <CodeBlock language="json" wrap>
                        {JSON.stringify(step.arguments, null, 2)}
                      </CodeBlock>
                    </Box>
                  </Box>
                )}
              </Box>
            );
          })}
        </Box>
      </Field>

      <Field label="graded against">
        <Stack direction="row" flexWrap="wrap" useFlexGap spacing={0.5}>
          {checks.map((check) => (
            <Tag
              key={check.name}
              kind={check.settled_by === "code" ? "code" : "evalHarness"}
              title={check.what || ""}
              keepCase
            >
              {check.name}
            </Tag>
          ))}
        </Stack>
      </Field>

      {files.length > 0 && (
        <Field label="its files">
          <Stack direction="row" flexWrap="wrap" useFlexGap spacing={0.5}>
            {files.map((path) => (
              <Chip key={path} active={opened?.path === path} onClick={() => openFile(path)}>
                {path}
              </Chip>
            ))}
          </Stack>
        </Field>
      )}

      {opened && (
        <Field label={opened.path}>
          <CodeBlock language={languageOf(opened.path)}>{opened.text}</CodeBlock>
        </Field>
      )}

      {/* Only offered once there is a run to jump to, and only when the parent can switch tabs. */}
      {ran && onSeeRun && (
        <Box sx={{ alignSelf: "flex-start" }}>
          <Chip onClick={onSeeRun}>see its run →</Chip>
        </Box>
      )}
    </XCard>
  );
};

ScenarioCard.propTypes = {
  scenario: PropTypes.object.isRequired,
  ran: PropTypes.object,
  onSeeRun: PropTypes.func,
};

const ScenariosTab = ({ scenarios, runs, onSay, hasWorld, onSeeRun }) => {
  if (scenarios.length === 0) {
    return (
      <Box>
        <Pane title="Scenarios">
          <Typography sx={{ fontSize: 13.5, color: "text.secondary" }}>
            None written yet. Each one owns a folder: what it changes, whether
            the world is ready for it, and one runnable file per check.
          </Typography>
          {/* Asking for scenarios before a world exists only produces scenarios nothing can check. */}
          {hasWorld && onSay && (
            <Box sx={{ mt: 1 }}>
              <Chip onClick={() => onSay("write 5 scenarios for this agent")}>
                write 5 scenarios
              </Chip>
            </Box>
          )}
        </Pane>
      </Box>
    );
  }

  const ranByName = {};
  (runs || []).forEach((run) => {
    ranByName[run.scenario] = run;
  });
  const validated = scenarios.filter((one) => one.validated).length;

  return (
    <Box>
      <Pane
        title="Scenarios"
        meta={`${validated} of ${scenarios.length} validated — only these are ever run`}
      />
      {scenarios.map((scenario) => (
        <ScenarioCard
          key={scenario.name}
          scenario={scenario}
          ran={ranByName[scenario.name]}
          onSeeRun={onSeeRun}
        />
      ))}
    </Box>
  );
};

ScenariosTab.propTypes = {
  scenarios: PropTypes.array.isRequired,
  runs: PropTypes.array,
  onSay: PropTypes.func,
  hasWorld: PropTypes.bool,
  onSeeRun: PropTypes.func,
};

export default ScenariosTab;
