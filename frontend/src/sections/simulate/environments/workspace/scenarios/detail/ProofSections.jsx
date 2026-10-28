import PropTypes from "prop-types";
import { Fragment } from "react";
import { Box, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import {
  SAMPLE_CALLER_POLICY,
  SAMPLE_PROOF_GATES,
  SAMPLE_PROOF_NOTE,
  SAMPLE_PROOF_VERSION,
  SAMPLE_REFERENCE_SOLUTION,
  SAMPLE_SCENARIO_FILES,
  SAMPLE_SCENARIO_FOLDER,
} from "src/api/simulate-environments/_fixtures/detail/scenarioProof";
import SoonChip from "../../../components/SoonChip";
import UiOnlyFlag from "../../../components/UiOnlyFlag";
import useUiSample from "../../../hooks/useUiSample";
import { CollapsibleRow } from "./sheetParts";
import { MONO } from "./sheet.constants";

// Vel's Proof and Internals rows of the drawer's Status part. The API has no
// proofs, caller policy, reference solution or scenario folder, so each says
// so; `?uiSample=proof` fills them with Vel's sample, flagged UI only.

const GATE_ICON = {
  true: "solar:check-circle-linear",
  false: "solar:close-circle-linear",
  null: "solar:question-circle-linear",
};

const gateColor = (ok) => {
  if (ok === false) return "error.main";
  return ok ? "text.secondary" : "text.disabled";
};

const gateLine = (g) =>
  g.ok === null
    ? `${g.detail} Not yet proved on ${SAMPLE_PROOF_VERSION}.`
    : g.detail;

function NotInApi({ label, what }) {
  return (
    <Stack direction="row" alignItems="center" spacing={1}>
      <Typography
        sx={{ typography: "s2", color: "text.subtitle", lineHeight: 1.6 }}
      >
        Not in the API yet
      </Typography>
      <SoonChip label={label} what={what} plural size="tiny" />
    </Stack>
  );
}
NotInApi.propTypes = { label: PropTypes.string, what: PropTypes.string };

// One gate, as an icon and its name. Red only when it failed.
function GateMark({ gate }) {
  return (
    <CustomTooltip show arrow size="small" title={gateLine(gate)}>
      <Stack
        direction="row"
        alignItems="center"
        spacing={0.375}
        sx={{ cursor: "help" }}
      >
        <Iconify
          icon={GATE_ICON[String(gate.ok)]}
          width={14}
          sx={{ color: gateColor(gate.ok), flexShrink: 0 }}
        />
        <Typography
          sx={{
            typography: "s2",
            color: gate.ok === null ? "text.subtitle" : "text.secondary",
            lineHeight: 1.6,
            whiteSpace: "nowrap",
          }}
        >
          {gate.label}
        </Typography>
      </Stack>
    </CustomTooltip>
  );
}
GateMark.propTypes = { gate: PropTypes.object };

// Every gate, one line each: what it proves, and the file it leaves behind.
function GateList({ files = false }) {
  return (
    <Box
      sx={{
        display: "grid",
        gridTemplateColumns: { xs: "1fr", sm: "max-content minmax(0, 1fr)" },
        columnGap: 2,
        rowGap: 0.375,
        maxWidth: 760,
      }}
    >
      {SAMPLE_PROOF_GATES.map((g) => (
        <Fragment key={g.id}>
          <GateMark gate={g} />
          <Typography
            sx={{
              typography: "s2",
              lineHeight: 1.6,
              color: g.ok === null ? "text.subtitle" : "text.secondary",
              mb: { xs: 0.5, sm: 0 },
            }}
          >
            {g.ok === null
              ? `Not yet proved on ${SAMPLE_PROOF_VERSION}.`
              : g.detail}
            {files && g.file && (
              <Box
                component="span"
                sx={{
                  ml: 0.75,
                  color: "text.subtitle",
                  fontFamily: MONO,
                  fontSize: 12,
                }}
              >
                {g.file}
              </Box>
            )}
          </Typography>
        </Fragment>
      ))}
    </Box>
  );
}
GateList.propTypes = { files: PropTypes.bool };

const dot = (
  <Box component="span" sx={{ color: "text.disabled", typography: "s2" }}>
    ·
  </Box>
);

function ProofSummary() {
  const core = SAMPLE_PROOF_GATES.filter((g) => g.core);
  return (
    <>
      <UiOnlyFlag feature="scenarioStates" />
      {core.map((g, i) => (
        <Fragment key={g.id}>
          {i > 0 && dot}
          <GateMark gate={g} />
        </Fragment>
      ))}
      {dot}
      <Typography
        sx={{ typography: "s2", color: "text.subtitle", lineHeight: 1.6 }}
      >
        {`on ${SAMPLE_PROOF_VERSION}`}
      </Typography>
    </>
  );
}

export function ProofRow() {
  const sample = useUiSample("proof");
  if (!sample) {
    return (
      <CollapsibleRow label="Proof">
        <NotInApi
          label="Proof"
          what="Scenario proofs (staged, solvable, not vacuous and the admission gates)"
        />
      </CollapsibleRow>
    );
  }
  return (
    <CollapsibleRow label="Proof" summary={<ProofSummary />}>
      <Typography
        sx={{
          typography: "s3",
          color: "text.subtitle",
          mb: 0.75,
          maxWidth: 760,
        }}
      >
        {SAMPLE_PROOF_NOTE}
      </Typography>
      <GateList />
    </CollapsibleRow>
  );
}

function Section({ title, children }) {
  return (
    <Box>
      <Typography
        sx={{
          typography: "s3",
          fontWeight: 600,
          color: "text.subtitle",
          mb: 0.75,
        }}
      >
        {title}
      </Typography>
      {children}
    </Box>
  );
}
Section.propTypes = { title: PropTypes.string, children: PropTypes.node };

function PolicyRow({ label, children }) {
  return (
    <Stack
      direction={{ xs: "column", sm: "row" }}
      spacing={{ xs: 0.5, sm: 2 }}
      sx={{ py: 0.375 }}
    >
      <Typography
        sx={{
          typography: "s2",
          color: "text.subtitle",
          width: { sm: 104 },
          flexShrink: 0,
          lineHeight: 1.6,
        }}
      >
        {label}
      </Typography>
      <Box sx={{ flex: 1, minWidth: 0 }}>{children}</Box>
    </Stack>
  );
}
PolicyRow.propTypes = { label: PropTypes.string, children: PropTypes.node };

const BODY_SX = { typography: "s2", color: "text.secondary", lineHeight: 1.6 };

function Bullets({ items }) {
  return (
    <Stack component="ul" sx={{ m: 0, pl: 2 }}>
      {items.map((t) => (
        <Typography key={t} component="li" sx={BODY_SX}>
          {t}
        </Typography>
      ))}
    </Stack>
  );
}
Bullets.propTypes = { items: PropTypes.arrayOf(PropTypes.string) };

function CallerPolicy() {
  const p = SAMPLE_CALLER_POLICY;
  return (
    <Section title="The simulated caller: a policy, not a prompt">
      <Stack spacing={0.75}>
        <PolicyRow label="States plainly">
          <Bullets items={p.facts} />
        </PolicyRow>
        <PolicyRow label="Held back">
          <Stack spacing={0.375}>
            {p.heldBack.map((f) => (
              <Stack
                key={f.fact}
                direction="row"
                spacing={0.75}
                alignItems="flex-start"
              >
                <Iconify
                  icon="solar:lock-keyhole-minimalistic-linear"
                  width={14}
                  sx={{ color: "warning.main", flexShrink: 0, mt: "3px" }}
                />
                <Typography sx={BODY_SX}>
                  <Box
                    component="span"
                    sx={{ color: "text.primary", fontWeight: 600 }}
                  >
                    {f.fact}
                  </Box>
                  {`: ${f.trigger}`}
                </Typography>
              </Stack>
            ))}
          </Stack>
        </PolicyRow>
        <PolicyRow label="Manner">
          <Typography sx={BODY_SX}>{p.manner}</Typography>
        </PolicyRow>
        <PolicyRow label="Hangs up when">
          <Bullets items={p.termination} />
        </PolicyRow>
        <PolicyRow label="Never">
          <Bullets items={p.prohibited} />
        </PolicyRow>
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
          {p.note}
        </Typography>
      </Stack>
    </Section>
  );
}

function InternalsSample() {
  return (
    <Stack
      spacing={2}
      divider={
        <Box sx={{ borderBottom: "1px dashed", borderColor: "divider" }} />
      }
      sx={{ maxWidth: 780 }}
    >
      <CallerPolicy />
      <Section title="The reference solution: proves it can be passed, never run against the agent">
        <Stack spacing={0.375}>
          {SAMPLE_REFERENCE_SOLUTION.map((r, i) => (
            <Typography
              key={r.tool}
              sx={{
                typography: "s2",
                fontFamily: MONO,
                color: "text.secondary",
              }}
            >
              {`${i + 1}. ${r.tool}`}
              {r.args.length > 0 && (
                <Box component="span" sx={{ color: "text.subtitle" }}>
                  {` ${r.args.join(", ")}`}
                </Box>
              )}
            </Typography>
          ))}
        </Stack>
      </Section>
      <Section title="The quality gate: every scenario passes all of these before it joins the set. A failure quarantines it.">
        <GateList files />
      </Section>
      <Section title="Its folder">
        <Typography
          sx={{
            typography: "s3",
            color: "text.subtitle",
            fontFamily: MONO,
            mb: 0.75,
          }}
        >
          {SAMPLE_SCENARIO_FOLDER}
        </Typography>
        <Stack
          direction="row"
          spacing={0.75}
          flexWrap="wrap"
          rowGap={0.75}
          useFlexGap
        >
          {SAMPLE_SCENARIO_FILES.map((f) => (
            <Typography
              key={f}
              sx={{
                px: 0.875,
                py: 0.375,
                borderRadius: "4px",
                typography: "s3",
                fontFamily: MONO,
                color: "text.secondary",
                border: "1px solid",
                borderColor: "divider",
              }}
            >
              {f}
            </Typography>
          ))}
        </Stack>
      </Section>
    </Stack>
  );
}

export function InternalsRow() {
  const sample = useUiSample("proof");
  if (!sample) {
    return (
      <CollapsibleRow label="Internals">
        <NotInApi
          label="Internals"
          what="A scenario's caller policy, reference solution and files"
        />
      </CollapsibleRow>
    );
  }
  return (
    <CollapsibleRow
      label="Internals"
      summary={<UiOnlyFlag feature="scenarioStates" />}
    >
      <InternalsSample />
    </CollapsibleRow>
  );
}
