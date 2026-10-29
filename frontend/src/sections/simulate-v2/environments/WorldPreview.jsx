import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import { estimateRunPlan, rangeLabel } from "../_mock/pricing";
import { resolveEval } from "../_mock/evals";
import { sampleAgentOf, worldPacksOf } from "./worldSource";

/* A spec-sheet section: a title, then rows or text. */
function Section({ title, aside, children }) {
  return (
    <Box>
      <Stack
        direction="row"
        alignItems="baseline"
        spacing={1}
        sx={{ mb: 0.75 }}
      >
        <Typography sx={{ typography: "m3", fontWeight: 500, flex: 1 }}>
          {title}
        </Typography>
        {aside && (
          <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
            {aside}
          </Typography>
        )}
      </Stack>
      {children}
    </Box>
  );
}
Section.propTypes = {
  title: PropTypes.string,
  aside: PropTypes.string,
  children: PropTypes.node,
};

/* Rows in one bordered panel: a label column and a value. */
function Rows({ rows, mono = false, labelWidth = 150 }) {
  return (
    <Box sx={{ border: "1px solid", borderColor: "divider", borderRadius: 1 }}>
      {rows.map((r, i) => (
        <Stack
          key={`${r.k}-${i}`}
          direction="row"
          spacing={1.5}
          sx={{
            px: 1.5,
            py: 0.75,
            ...(i > 0 && { borderTop: "1px solid", borderColor: "divider" }),
          }}
        >
          <Typography
            sx={{
              typography: "s1",
              width: labelWidth,
              flexShrink: 0,
              minWidth: 0,
              wordBreak: "break-word",
              ...(mono && { fontFamily: "monospace" }),
            }}
          >
            {r.k}
          </Typography>
          <Typography
            sx={{
              typography: "s1",
              color: "text.secondary",
              flex: 1,
              minWidth: 0,
            }}
          >
            {r.v}
          </Typography>
          {r.n != null && (
            <Typography
              sx={{
                typography: "s1",
                color: "text.tertiary",
                fontVariantNumeric: "tabular-nums",
                flexShrink: 0,
              }}
            >
              {r.n}
            </Typography>
          )}
        </Stack>
      ))}
    </Box>
  );
}
Rows.propTypes = {
  rows: PropTypes.array.isRequired,
  mono: PropTypes.bool,
  labelWidth: PropTypes.number,
};

const ATTEMPTS = 3;
const costOf = (template, count) => {
  const e = estimateRunPlan({
    env: { surface: template.surface },
    scenarios: count,
    attempts: ATTEMPTS,
    evals: (template.evalPreset || []).map(resolveEval).filter(Boolean),
  });
  return rangeLabel(e.platform.low, e.platform.high, e.platform.unknown);
};

/**
 * The preview a world opens on (catalogue.md "Discover and preview a
 * world"): what the agent will do, representative requests, seeded records,
 * tools, policies, actors, checks, and each pack's size and cost — the whole
 * task package, not a card that only says its domain.
 */
export default function WorldPreview({ world }) {
  const t = world.template;
  const entry = world.entry || null;
  const packs = worldPacksOf(t);
  const checks = (t.evalPreset || []).map(resolveEval).filter(Boolean);
  return (
    <Stack spacing={2.5}>
      <Section title="What the agent does">
        <Typography sx={{ typography: "s1", color: "text.secondary" }}>
          {t.description}
        </Typography>
      </Section>
      {entry?.requests?.length > 0 && (
        <Section title="Example requests">
          <Stack spacing={0.75}>
            {entry.requests.map((q) => (
              <Typography
                key={q}
                sx={{
                  typography: "s1",
                  color: "text.primary",
                  pl: 1.25,
                  borderLeft: "2px solid",
                  borderColor: "divider",
                }}
              >
                {`“${q}”`}
              </Typography>
            ))}
          </Stack>
        </Section>
      )}
      <Section
        title="Seeded records"
        aside="Synthetic — the world never touches production"
      >
        <Rows
          rows={(t.seed?.tables || []).map((x) => ({
            k: x.name,
            v: x.note || "",
            n: x.rows.toLocaleString(),
          }))}
          mono
        />
      </Section>
      <Section
        title="Tools"
        aside={`${(t.tools || []).length} the world answers`}
      >
        <Rows
          rows={(t.tools || []).map((x) => ({ k: x.name, v: x.desc }))}
          mono
          labelWidth={190}
        />
      </Section>
      <Section title="Rules" aside="Each is a critical check">
        <Rows
          rows={(t.rules || []).map((r, i) => ({ k: `${i + 1}`, v: r }))}
          labelWidth={16}
        />
      </Section>
      {entry?.actors?.length > 0 && (
        <Section title="Other parties">
          {/* Name — role when the catalogue gives one; otherwise the whole line. */}
          <Rows
            rows={entry.actors.map((a) => {
              const [k, ...v] = a.split(" — ");
              return v.length ? { k, v: v.join(" — ") } : { k: a, v: "" };
            })}
            labelWidth={entry.actors.every((a) => a.includes(" — ")) ? 150 : 520}
          />
        </Section>
      )}
      {checks.length > 0 && (
        <Section title="Checks" aside="Run on every scenario">
          <Rows
            rows={checks.map((c) => ({
              k: c.name,
              v: c.description || c.type || "",
            }))}
          />
        </Section>
      )}
      <Section
        title="Scenario packs"
        aside={`× ${ATTEMPTS} attempts · cost per run`}
      >
        <Rows
          rows={packs.map((p) => ({
            k: p.name,
            v: `${p.count} scenarios · ${p.blurb}`,
            n: costOf(t, p.count),
          }))}
        />
      </Section>
    </Stack>
  );
}
WorldPreview.propTypes = { world: PropTypes.object.isRequired };

/**
 * The sample agent's recipe, shown apart from the world (catalogue.md): its
 * model, prompt and tools, and what it's known to get wrong. A different
 * agent never changes the world or what counts as success.
 */
export function SampleRecipe({ world }) {
  const t = world.template;
  const recipe = world.entry?.recipe || {
    model: "gpt-4.1-mini",
    limitations: [],
  };
  const sample = sampleAgentOf(t);
  const prompt = [
    `You are the ${t.name} agent. ${t.description}`,
    "",
    "Follow these rules exactly:",
    ...(t.rules || []).map((r) => `- ${r}`),
    "",
    "Use the tools for every fact you state; never guess a record.",
  ].join("\n");
  return (
    <Stack spacing={2.5}>
      <Typography sx={{ typography: "s1", color: "text.secondary" }}>
        The agent this world was designed and proved with. It&apos;s shown apart
        from the world: using your own agent never changes the world&apos;s
        cases or what counts as success.
      </Typography>
      <Rows
        rows={[
          { k: "Agent", v: sample.name },
          { k: "Model", v: recipe.model },
          { k: "Tools", v: (t.tools || []).map((x) => x.name).join(", ") },
        ]}
        labelWidth={90}
      />
      <Section title="Prompt">
        <Box
          component="pre"
          sx={{
            m: 0,
            p: 1.5,
            border: "1px solid",
            borderColor: "divider",
            borderRadius: 1,
            typography: "s2",
            fontFamily: "monospace",
            whiteSpace: "pre-wrap",
            color: "text.secondary",
          }}
        >
          {prompt}
        </Box>
      </Section>
      {recipe.limitations.length > 0 && (
        <Section
          title="Known limitations"
          aside="Its failures are shown, not hidden"
        >
          <Rows
            rows={recipe.limitations.map((l, i) => ({ k: `${i + 1}`, v: l }))}
            labelWidth={16}
          />
        </Section>
      )}
    </Stack>
  );
}
SampleRecipe.propTypes = { world: PropTypes.object.isRequired };
