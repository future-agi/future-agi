import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";
import { alpha } from "@mui/material/styles";
import JsonView from "../JsonView";
import Pane from "../parts/Pane";
import Field from "../parts/Field";
import XCard from "../parts/XCard";
import DataTable from "../parts/DataTable";
import { ALK_MONO } from "../alkTokens";

const ARG_COLUMNS = ["Argument", "Type", "Permitted values"];
const DEP_COLUMNS = ["Name", "Kind", "Provides", "Used by"];

/** The contract's plain-prose lists — rules, use cases, banned names, open questions. */
const RuleList = ({ items }) => (
  <Box component="ul" sx={{ m: 0, pl: 2.2 }}>
    {items.map((item) => (
      <Typography key={item} component="li" variant="body2" sx={{ mb: 0.4 }}>
        {item}
      </Typography>
    ))}
  </Box>
);

RuleList.propTypes = { items: PropTypes.array.isRequired };

/** The reference's `.subline`: an italic muted sentence under a heading or a field label. */
const Subline = ({ children }) => (
  <Typography variant="body2" sx={{ fontStyle: "italic", color: "text.secondary" }}>
    {children}
  </Typography>
);

Subline.propTypes = { children: PropTypes.node };

const argumentRows = (tool) =>
  (tool.args || []).map((arg) => {
    const permitted = (tool.arg_values || {})[arg];
    return {
      Argument: arg,
      Type: (tool.arg_types || {})[arg] || "",
      "Permitted values": Array.isArray(permitted) ? permitted.join(", ") : "",
    };
  });

/**
 * The contract is the first thing the harness writes and the thing every later stage is
 * graded against, so it is shown as prose the reader can check — the raw JSON is kept at the
 * bottom rather than being the whole tab.
 */
const ContractTab = ({ contract }) => {
  const data = contract || {};

  // The endpoint answers `{}` until the read stage writes one. Anything with keys in it is
  // shown, even half-written: the reference gates on `agent` alone, which would hide a
  // partial contract behind "nothing yet" when the honest answer is to show what there is.
  if (Object.keys(data).length === 0) {
    return (
      <Box>
        <Pane title="Contract">
          <Typography variant="body2" color="text.secondary">
            Nothing yet. Point the harness at an agent and it will read the source and write
            down what is verifiably true.
          </Typography>
        </Pane>
      </Box>
    );
  }

  const tools = data.tools || [];
  const dependencies = data.dependencies || [];
  const hardConstraints = data.hard_constraints || [];
  const useCases = data.real_use_cases || [];
  const antiHallucination = data.anti_hallucination || [];
  const openQuestions = data.open_questions || [];
  const amendments = data.amendments || [];

  return (
    <Box>
      {data.agent && (
        <Pane title={data.agent} meta="what this agent is">
          {data.one_liner && <Subline>{data.one_liner}</Subline>}
        </Pane>
      )}

      <Pane
        title="Tools"
        meta={`${tools.length} the agent really has — click one for its arguments`}
      >
        {tools.map((tool) => {
          const rows = argumentRows(tool);
          return (
            <XCard
              key={tool.name}
              title={tool.name}
              meta={(tool.args || []).join(", ") || "no arguments"}
            >
              {tool.description && (
                <Field label="what it does">
                  <Subline>{tool.description}</Subline>
                </Field>
              )}
              {rows.length > 0 && (
                <Field label="arguments">
                  <DataTable columns={ARG_COLUMNS} rows={rows} count={rows.length} />
                </Field>
              )}
            </XCard>
          );
        })}
      </Pane>

      {dependencies.length > 0 && (
        <Pane title="What it depends on" meta="the environment stage builds each of these">
          <DataTable
            columns={DEP_COLUMNS}
            rows={dependencies.map((one) => ({
              Name: one.name,
              Kind: one.kind || "",
              Provides: one.what || "",
              "Used by": (one.used_by || []).join(", "),
            }))}
            count={dependencies.length}
          />
        </Pane>
      )}

      {hardConstraints.length > 0 && (
        <Pane title="Hard rules" meta="told to the agent, graded afterwards">
          <RuleList items={hardConstraints} />
        </Pane>
      )}

      {useCases.length > 0 && (
        <Pane title="Use cases" meta="what it is actually for">
          <RuleList items={useCases} />
        </Pane>
      )}

      {antiHallucination.length > 0 && (
        <Pane title="Does not exist" meta="plausible names later stages must never use">
          <RuleList items={antiHallucination} />
        </Pane>
      )}

      {openQuestions.length > 0 && (
        <Pane title="Open questions" meta="what the source did not settle">
          <RuleList items={openQuestions} />
        </Pane>
      )}

      {amendments.length > 0 && (
        <Pane title="Amendments" meta="changed after reading, each with its reason">
          {amendments.map((line) => (
            // Amber, not red: an amendment is the contract correcting itself, not a failure.
            <Box
              key={line}
              sx={{
                mt: 0.75,
                px: 0.7,
                py: 0.45,
                borderRadius: "3px",
                fontFamily: ALK_MONO,
                fontSize: 11.8,
                color: "accent.tool",
                bgcolor: (theme) => alpha(theme.palette.warning.main, 0.12),
              }}
            >
              {line}
            </Box>
          ))}
        </Pane>
      )}

      <Pane title="The whole contract" meta="contract.json, as written">
        <JsonView value={data} />
      </Pane>
    </Box>
  );
};

ContractTab.propTypes = { contract: PropTypes.object };

export default ContractTab;
