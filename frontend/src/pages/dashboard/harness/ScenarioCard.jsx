import { useState } from "react";
import PropTypes from "prop-types";
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Box,
  Chip,
  Collapse,
  Stack,
  Typography,
} from "@mui/material";

import Iconify from "src/components/iconify";
import StatusChip from "src/components/custom-status-chip/CustomStatusChip";
import { STATUS_TYPES } from "src/utils/statusUtils";

// A generated scenario carries only `name`, `instruction`, `use_case` and `scenario_key`
// today; the richer authoring detail (goal, sub-goals, persona, background noise, actors,
// variables) is emitted by ALK when the bundle provides it. Every block below renders only
// when its field is present, so the card is correct on a minimal scenario and rich on a full
// one — it never invents a section the data does not carry.
const chipStatus = (status) => {
  if (status === "passed") return STATUS_TYPES.PASS;
  if (status === "failed" || status === "errored") return STATUS_TYPES.ERROR;
  if (status === "skipped") return STATUS_TYPES.CANCELED;
  return STATUS_TYPES.RUNNING;
};

// A sub-goal's verdict is tri-state: held (true), did not hold (false), or never judged
// (null / absent — a generated scenario that has not run yet). Only a real `false` is a mark
// against the agent, so a generated objective shows a neutral ring rather than a red cross.
function SubGoalMark({ held }) {
  if (held === true)
    return (
      <Iconify
        icon="solar:check-circle-bold"
        color="accent.pass"
        width={16}
        sx={{ mt: "1px", flexShrink: 0 }}
      />
    );
  if (held === false)
    return (
      <Iconify
        icon="solar:close-circle-bold"
        color="accent.fail"
        width={16}
        sx={{ mt: "1px", flexShrink: 0 }}
      />
    );
  return (
    <Iconify
      icon="solar:record-circle-linear"
      color="text.disabled"
      width={16}
      sx={{ mt: "1px", flexShrink: 0 }}
    />
  );
}

SubGoalMark.propTypes = { held: PropTypes.bool };

function MetaGroup({ label, children }) {
  return (
    <Box
      sx={{
        "& + &": {
          mt: 1.75,
          pt: 1.75,
          borderTop: "1px solid",
          borderColor: "divider",
        },
      }}
    >
      <Typography
        variant="caption"
        sx={{
          display: "block",
          mb: 0.75,
          fontWeight: 700,
          letterSpacing: "0.06em",
          textTransform: "uppercase",
          color: "text.disabled",
          fontSize: 10.5,
        }}
      >
        {label}
      </Typography>
      {children}
    </Box>
  );
}

MetaGroup.propTypes = {
  label: PropTypes.string.isRequired,
  children: PropTypes.node,
};

const SectionLabel = ({ children }) => (
  <Typography
    variant="caption"
    sx={{
      display: "block",
      mb: 0.5,
      fontWeight: 700,
      letterSpacing: "0.06em",
      textTransform: "uppercase",
      color: "text.disabled",
      fontSize: 10.5,
    }}
  >
    {children}
  </Typography>
);
SectionLabel.propTypes = { children: PropTypes.node };

// Persona, background noise, actors and variables are configuration about the world the
// scenario runs in, not the objective itself — so they sit in their own panel to the side of
// the goal and sub-goals, which are the substance. On a narrow column the panel drops below.
function MetaPanel({ persona, backgroundNoise, actors, variables }) {
  return (
    <Box
      sx={{
        // A tonal well rather than a fourth outlined card, so the metadata reads as a nested
        // aside of the scenario, not another peer container stacked inside it.
        bgcolor: "background.neutral",
        borderRadius: 1,
        p: 1.75,
      }}
    >
      {persona && (
        <MetaGroup label="Persona">
          <Stack
            direction="row"
            spacing={1}
            alignItems="center"
            sx={{ mb: 0.25 }}
          >
            <Iconify
              icon="solar:user-rounded-linear"
              width={14}
              color="text.secondary"
            />
            <Typography variant="body2" fontWeight={600}>
              {persona.name}
            </Typography>
          </Stack>
          {persona.role && (
            <Typography variant="caption" color="text.disabled" display="block">
              {persona.role}
            </Typography>
          )}
          {persona.situation && (
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ display: "block", mt: 0.75, mb: 1 }}
            >
              {persona.situation}
            </Typography>
          )}
          {persona.traits && (
            <Stack direction="row" spacing={0.75} flexWrap="wrap" useFlexGap>
              {Object.entries(persona.traits).map(([key, value]) => (
                <Chip
                  key={key}
                  size="small"
                  variant="outlined"
                  label={
                    <>
                      <Box component="span" sx={{ color: "text.disabled" }}>
                        {key.replaceAll("_", " ")}
                      </Box>{" "}
                      {String(value)}
                    </>
                  }
                />
              ))}
            </Stack>
          )}
        </MetaGroup>
      )}

      {backgroundNoise?.length > 0 && (
        <MetaGroup label="Background noise">
          {backgroundNoise.map((note) => (
            <Typography
              key={String(note)}
              variant="caption"
              color="text.secondary"
              sx={{ display: "block", py: 0.25 }}
            >
              {String(note)}
            </Typography>
          ))}
        </MetaGroup>
      )}

      {actors?.length > 0 && (
        <MetaGroup label="Actors">
          {actors.map((actor) => (
            <Box key={actor.name} sx={{ py: 0.5 }}>
              <Typography variant="body2" component="span" fontWeight={600}>
                {actor.name}
              </Typography>{" "}
              {actor.role && (
                <Typography
                  variant="caption"
                  component="span"
                  color="text.disabled"
                >
                  {actor.role}
                </Typography>
              )}
              {(actor.sub_actors || []).map((sub) => (
                <Box
                  key={sub.name}
                  sx={{
                    ml: 1.75,
                    pl: 1.25,
                    mt: 0.5,
                    borderLeft: "1px solid",
                    borderColor: "divider",
                  }}
                >
                  <Typography
                    variant="caption"
                    component="span"
                    fontWeight={600}
                  >
                    {sub.name}
                  </Typography>{" "}
                  {sub.role && (
                    <Typography
                      variant="caption"
                      component="span"
                      color="text.secondary"
                    >
                      {sub.role}
                    </Typography>
                  )}
                </Box>
              ))}
            </Box>
          ))}
        </MetaGroup>
      )}

      {variables && Object.keys(variables).length > 0 && (
        <MetaGroup label="Variables">
          <Box
            component="dl"
            sx={{
              m: 0,
              display: "grid",
              gridTemplateColumns: "auto 1fr",
              columnGap: 1.5,
              rowGap: 0.75,
              fontFamily: "monospace",
              fontSize: 12,
            }}
          >
            {Object.entries(variables).map(([key, value]) => (
              <Box key={key} sx={{ display: "contents" }}>
                <Box component="dt" sx={{ color: "text.disabled" }}>
                  {key}
                </Box>
                <Box component="dd" sx={{ m: 0 }}>
                  {String(value)}
                </Box>
              </Box>
            ))}
          </Box>
        </MetaGroup>
      )}
    </Box>
  );
}

MetaPanel.propTypes = {
  persona: PropTypes.object,
  backgroundNoise: PropTypes.array,
  actors: PropTypes.array,
  variables: PropTypes.object,
};

export default function ScenarioCard({ scenario, defaultExpanded = false }) {
  const [showRaw, setShowRaw] = useState(false);

  const subGoals = Array.isArray(scenario.sub_goals) ? scenario.sub_goals : [];
  const failedSubGoals = subGoals.filter((goal) => goal.held === false).length;
  const persona = scenario.persona || null;
  const backgroundNoise = Array.isArray(scenario.background_noise)
    ? scenario.background_noise
    : null;
  const actors = Array.isArray(scenario.actors) ? scenario.actors : null;
  const variables =
    scenario.variables && typeof scenario.variables === "object"
      ? scenario.variables
      : null;
  const goal = scenario.goal;
  // The success criterion (goal) is a distinct statement from the prompt (instruction);
  // labelling them keeps a full scenario from reading as the same sentence twice.
  const prompt = scenario.instruction;
  const hasMeta =
    persona || backgroundNoise?.length || actors?.length || variables;

  return (
    <Accordion
      variant="outlined"
      defaultExpanded={defaultExpanded}
      disableGutters
      sx={{
        bgcolor: "background.paper",
        borderColor: scenario.status === "failed" ? "accent.fail" : "divider",
        "&.Mui-expanded": { bgcolor: "background.paper" },
        "&:before": { display: "none" },
      }}
    >
      <AccordionSummary
        expandIcon={<Iconify icon="eva:arrow-ios-forward-fill" width={18} />}
        sx={{
          // Chevron points down when open by default; rotate from the forward glyph so a
          // collapsed row reads "expand me" rather than the ambiguous downward caret.
          "& .MuiAccordionSummary-expandIconWrapper.Mui-expanded": {
            transform: "rotate(90deg)",
          },
          "& .MuiAccordionSummary-content": {
            minWidth: 0,
            alignItems: "center",
          },
        }}
      >
        <Box sx={{ minWidth: 0, flex: 1 }}>
          <Stack
            direction="row"
            spacing={1}
            alignItems="center"
            flexWrap="wrap"
            useFlexGap
          >
            <Typography
              variant="body2"
              fontWeight={600}
              sx={{ fontFamily: "monospace", wordBreak: "break-all" }}
            >
              {scenario.name || scenario.scenario_key}
            </Typography>
            {persona?.name && (
              <Chip
                size="small"
                variant="outlined"
                icon={<Iconify icon="solar:user-rounded-linear" width={12} />}
                label={persona.name}
              />
            )}
            {subGoals.length > 0 && (
              <Chip
                size="small"
                variant="outlined"
                label={
                  <>
                    {subGoals.length} sub-goals
                    {failedSubGoals > 0 && (
                      <Box component="span" sx={{ color: "accent.fail" }}>
                        {" "}
                        · {failedSubGoals} failed
                      </Box>
                    )}
                  </>
                }
              />
            )}
          </Stack>
          {scenario.use_case && (
            <Typography
              variant="caption"
              color="text.disabled"
              noWrap
              display="block"
            >
              {scenario.use_case}
            </Typography>
          )}
        </Box>
        {scenario.status && (
          <StatusChip
            label={scenario.status}
            status={chipStatus(scenario.status)}
            showIcon={false}
            sx={{ mr: 1, flexShrink: 0 }}
          />
        )}
      </AccordionSummary>

      <AccordionDetails sx={{ pt: 0.5 }}>
        <Box
          sx={{
            display: "grid",
            gap: 2.25,
            alignItems: "start",
            gridTemplateColumns: hasMeta
              ? { xs: "1fr", md: "minmax(0, 1fr) 288px" }
              : "1fr",
          }}
        >
          <Box>
            {prompt && (
              <Box>
                <SectionLabel>Prompt</SectionLabel>
                <Typography variant="body2">{prompt}</Typography>
              </Box>
            )}
            {goal && (
              <Box sx={{ mt: 1.75 }}>
                <SectionLabel>Success criterion</SectionLabel>
                <Typography variant="body2">{goal}</Typography>
              </Box>
            )}
            {subGoals.length > 0 && (
              <Box sx={{ mt: 2 }}>
                <SectionLabel>Sub-goals</SectionLabel>
                <Box>
                  {subGoals.map((sub) => (
                    <Stack
                      key={sub.name}
                      direction="row"
                      spacing={1.125}
                      alignItems="flex-start"
                      sx={{
                        py: 1,
                        "& + &": {
                          borderTop: sub.held === false ? 0 : "1px solid",
                          borderColor: "divider",
                        },
                        ...(sub.held === false && {
                          // The fail token is a solid hue; lean on opacity for the wash so it
                          // reads as a highlight, not a filled block.
                          bgcolor: (theme) =>
                            theme.palette.mode === "dark"
                              ? "rgba(248,113,113,0.13)"
                              : "rgba(220,38,38,0.08)",
                          mx: -1,
                          px: 1,
                          borderRadius: 1,
                        }),
                      }}
                    >
                      <SubGoalMark held={sub.held} />
                      <Box>
                        <Typography
                          variant="body2"
                          fontWeight={600}
                          sx={{ fontFamily: "monospace" }}
                        >
                          {sub.name}
                        </Typography>
                        {sub.description && (
                          <Typography
                            variant="caption"
                            color="text.disabled"
                            display="block"
                          >
                            {sub.description}
                          </Typography>
                        )}
                        {/* A failed sub-goal carries the grader's reason — the point of
                            opening the scenario at all — so it reads in the fail colour
                            rather than being folded away with the neutral description. */}
                        {sub.held === false && sub.reason && (
                          <Typography
                            variant="caption"
                            color="accent.fail"
                            sx={{ display: "block", mt: 0.5 }}
                          >
                            {sub.reason}
                          </Typography>
                        )}
                      </Box>
                    </Stack>
                  ))}
                </Box>
              </Box>
            )}
          </Box>

          {hasMeta && (
            <MetaPanel
              persona={persona}
              backgroundNoise={backgroundNoise}
              actors={actors}
              variables={variables}
            />
          )}
        </Box>

        <Box sx={{ mt: 1.75 }}>
          <Typography
            component="button"
            type="button"
            variant="caption"
            onClick={() => setShowRaw((open) => !open)}
            sx={{
              display: "inline-flex",
              alignItems: "center",
              gap: 0.5,
              border: 0,
              bgcolor: "transparent",
              cursor: "pointer",
              color: "text.disabled",
              p: 0,
            }}
          >
            <Iconify
              icon={
                showRaw ? "eva:chevron-down-fill" : "eva:chevron-right-fill"
              }
              width={14}
            />
            View raw scenario
          </Typography>
          <Collapse in={showRaw} unmountOnExit>
            <Box
              component="pre"
              sx={{
                mt: 1,
                p: 1.25,
                fontSize: 11.5,
                color: "text.secondary",
                bgcolor: "background.default",
                border: "1px solid",
                borderColor: "divider",
                borderRadius: 1,
                maxHeight: 260,
                overflow: "auto",
                whiteSpace: "pre-wrap",
              }}
            >
              {JSON.stringify(scenario, null, 2)}
            </Box>
          </Collapse>
        </Box>
      </AccordionDetails>
    </Accordion>
  );
}

ScenarioCard.propTypes = {
  scenario: PropTypes.shape({
    name: PropTypes.string,
    scenario_key: PropTypes.string,
    instruction: PropTypes.string,
    use_case: PropTypes.string,
    status: PropTypes.string,
    goal: PropTypes.string,
    sub_goals: PropTypes.array,
    persona: PropTypes.object,
    background_noise: PropTypes.array,
    actors: PropTypes.array,
    variables: PropTypes.object,
  }).isRequired,
  defaultExpanded: PropTypes.bool,
};
