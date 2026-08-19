import PropTypes from "prop-types";
import { useEffect, useMemo, useRef, useState } from "react";
import { alpha } from "@mui/material/styles";
import {
  Box, Stack, Typography, Button, TextField, IconButton, Chip, Fade,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { generatedPool } from "../../_mock/scenarios";
import { PersonaBadge, EmptyState, SectionCard } from "../../components/primitives";
import { ThinkingBar } from "../../components/loading";

/**
 * Starter prompts written from this environment's own rules and data, so the
 * suggestions are things worth testing *here* rather than generic filler.
 */
const suggestionsFor = (env) => {
  const out = (env.rules || []).slice(0, 2).map((r) => `Test what happens when the agent is pushed to break: "${r}"`);
  const trap = (env.seed?.tables || []).find((t) => t.note);
  if (trap) out.push(`Scenarios that land on the ${trap.name} rows where ${trap.note}`);
  const tool = (env.tools || [])[env.tools.length - 1];
  if (tool) out.push(`Cases where the agent should reach for ${tool.name} but probably won't`);
  out.push("Users who try to override the agent's instructions mid-task");
  return out;
};

const ASSISTANT_OPENER =
  "Tell me what you want to probe and I'll write scenarios for this environment. " +
  "Each one comes with a persona, a task and the outcome I'll grade against.";

/**
 * Chat-driven scenario authoring.
 *
 * Split view on purpose: the conversation is the input, the table is the
 * artifact. Rows stream in one at a time while the assistant is "writing" so
 * the user sees the thing being built rather than a spinner followed by a wall
 * of results — that visibility is the whole point of this mode.
 */
export default function ChatBuilder({ env, onAdd }) {
  const [messages, setMessages] = useState([
    { role: "assistant", text: ASSISTANT_OPENER },
  ]);
  // Everything the generator can draw on, derived from this environment.
  const pool = useMemo(() => generatedPool(env), [env]);
  const suggestions = useMemo(() => suggestionsFor(env), [env]);
  const [input, setInput] = useState("");
  const [generating, setGenerating] = useState(false);
  const [rows, setRows] = useState([]);
  const scrollRef = useRef(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: 999999, behavior: "smooth" });
  }, [messages, generating]);

  const send = (text) => {
    const prompt = (text ?? input).trim();
    if (!prompt || generating) return;

    setMessages((m) => [...m, { role: "user", text: prompt }]);
    setInput("");
    setGenerating(true);

    // Stream scenarios in one at a time — the artifact builds visibly.
    const start = rows.length % Math.max(pool.length, 1);
    const toEmit = Array.from({ length: 3 }, (_, i) => {
      const src = pool[(start + i) % Math.max(pool.length, 1)];
      return { ...src, id: `gen-${rows.length + i}-${Date.now()}` };
    }).filter((r) => r.title);

    toEmit.forEach((row, i) => {
      setTimeout(() => {
        setRows((r) => [...r, row]);
        if (i === toEmit.length - 1) {
          setGenerating(false);
          setMessages((m) => [
            ...m,
            {
              role: "assistant",
              text: `Added ${toEmit.length} scenarios. ${
                toEmit.some((t) => t.critical)
                  ? "I marked the rule probes as critical — a failure on those is a release blocker."
                  : "Ask for more, or tell me to make them harsher."
              }`,
            },
          ]);
        }
      }, 900 + i * 850);
    });
  };

  return (
    <SectionCard
      title="Describe your scenarios"
      subtitle={`Grounded in ${env.name} — its data, tools and rules`}
      action={
        <Button
          variant="contained"
          color="primary"
          size="small"
          disabled={rows.length === 0}
          onClick={() => onAdd(rows)}
          sx={{ typography: "s2", fontWeight: 700 }}
        >
          Add {rows.length || ""} {rows.length === 1 ? "scenario" : "scenarios"}
        </Button>
      }
    >
      {/* Fixed height: the split view needs room to breathe but must not push
          the selected-scenarios list off the page. */}
      <Box sx={{ display: "flex", height: 460, minHeight: 0 }}>
        {/* ── conversation ── */}
        <Stack
          sx={{
            width: { xs: "100%", md: 400 }, flexShrink: 0,
            borderRight: "1px solid", borderColor: "divider",
            minHeight: 0,
          }}
        >
          <Box ref={scrollRef} sx={{ flex: 1, overflow: "auto", p: 2.5 }}>
            <Stack spacing={2}>
              {messages.map((m, i) => (
                <Message key={i} role={m.role} text={m.text} />
              ))}
              {generating && <ThinkingBar label="Writing scenarios" />}
            </Stack>

            {messages.length === 1 && (
              <Stack spacing={0.75} sx={{ mt: 2.5 }}>
                <Typography sx={{ typography: "s3", color: "text.subtitle", fontWeight: 600 }}>
                  TRY ONE OF THESE
                </Typography>
                {suggestions.map((s) => (
                  <Chip
                    key={s}
                    label={s}
                    onClick={() => send(s)}
                    sx={{
                      justifyContent: "flex-start", height: "auto", py: 0.75, borderRadius: 1,
                      // The theme's Chip override paints the label white for
                      // filled chips; on a transparent background that leaves
                      // the text invisible, so set the colour explicitly.
                      color: "text.primary",
                      border: "1px solid", borderColor: "divider", bgcolor: "transparent",
                      "& .MuiChip-label": { typography: "s2", whiteSpace: "normal", textAlign: "left", px: 1 },
                      "&:hover": { borderColor: "primary.main", bgcolor: (t) => alpha(t.palette.primary.main, 0.04) },
                    }}
                  />
                ))}
              </Stack>
            )}
          </Box>

          <Box sx={{ p: 2, borderTop: "1px solid", borderColor: "divider" }}>
            <TextField
              fullWidth
              multiline
              maxRows={4}
              size="small"
              value={input}
              placeholder="e.g. add 5 more, and make them harder to pass"
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
              }}
              InputProps={{
                endAdornment: (
                  <IconButton
                    size="small"
                    onClick={() => send()}
                    disabled={!input.trim() || generating}
                    sx={{ alignSelf: "flex-end" }}
                  >
                    <Iconify
                      icon="solar:arrow-up-bold"
                      width={16}
                      sx={{ color: input.trim() ? "primary.main" : "text.subtitle" }}
                    />
                  </IconButton>
                ),
              }}
              sx={{ "& .MuiInputBase-root": { typography: "s2", alignItems: "flex-end" } }}
            />
          </Box>
        </Stack>

        {/* ── artifact ── */}
        <Box sx={{ flex: 1, minWidth: 0, overflow: "auto", bgcolor: "background.neutral", display: { xs: "none", md: "block" } }}>
          {rows.length === 0 && !generating ? (
            <EmptyState
              icon="solar:magic-stick-3-linear"
              title="Your scenarios will appear here"
              body="Describe what you want to test on the left. Each scenario arrives with a persona, a task and a graded outcome."
            />
          ) : (
            <Stack spacing={1.5} sx={{ p: 2.5 }}>
              {rows.map((r, i) => (
                <Fade in key={r.id} timeout={420}>
                  <Box>
                    <GeneratedCard row={r} index={i} onRemove={() => setRows((x) => x.filter((y) => y.id !== r.id))} />
                  </Box>
                </Fade>
              ))}
              {generating && <GhostCard />}
            </Stack>
          )}
        </Box>
      </Box>
    </SectionCard>
  );
}

ChatBuilder.propTypes = { env: PropTypes.object, onAdd: PropTypes.func };

function Message({ role, text }) {
  const isUser = role === "user";
  return (
    <Stack direction="row" spacing={1.25} alignItems="flex-start">
      <Box
        sx={{
          width: 24, height: 24, borderRadius: 0.75, flexShrink: 0, display: "grid", placeItems: "center",
          bgcolor: (t) => isUser ? "background.neutral" : alpha(t.palette.primary.main, 0.12),
          color: isUser ? "text.subtitle" : "primary.main",
        }}
      >
        <Iconify icon={isUser ? "solar:user-bold" : "solar:magic-stick-3-bold"} width={13} />
      </Box>
      <Typography sx={{ typography: "s2", color: isUser ? "text.primary" : "text.secondary", pt: 0.25 }}>
        {text}
      </Typography>
    </Stack>
  );
}
Message.propTypes = { role: PropTypes.string, text: PropTypes.string };

function GeneratedCard({ row, index, onRemove }) {
  return (
    <Box
      sx={{
        p: 2, borderRadius: 1.25, bgcolor: "background.paper",
        border: "1px solid", borderColor: "divider",
      }}
    >
      <Stack direction="row" alignItems="flex-start" spacing={1.5}>
        <Typography sx={{ typography: "s3", color: "text.subtitle", fontVariantNumeric: "tabular-nums", pt: 0.25 }}>
          {String(index + 1).padStart(2, "0")}
        </Typography>
        <Box flex={1} minWidth={0}>
          <Stack direction="row" alignItems="center" spacing={0.75}>
            <Typography sx={{ typography: "s2", fontWeight: 700 }}>{row.title}</Typography>
            {row.critical && (
              <Chip
                size="small"
                label="Critical"
                sx={{
                  height: 18, borderRadius: 0.5, color: "#DC2626",
                  bgcolor: (t) => alpha("#DC2626", t.palette.mode === "dark" ? 0.16 : 0.1),
                  "& .MuiChip-label": { px: 0.75, typography: "s3", fontWeight: 700 },
                }}
              />
            )}
          </Stack>
          <Typography sx={{ typography: "s2", color: "text.secondary", mt: 0.5 }}>{row.task}</Typography>

          <Stack direction="row" alignItems="center" spacing={1.5} sx={{ mt: 1.25 }}>
            <PersonaBadge persona={row.persona} compact />
            <Box flex={1} />
            <Stack direction="row" alignItems="center" spacing={0.5}>
              <Iconify icon="solar:chat-round-line-linear" width={13} sx={{ color: "text.subtitle" }} />
              <Typography sx={{ typography: "s3", color: "text.subtitle" }}>~{row.turns} turns</Typography>
            </Stack>
          </Stack>

          <Stack direction="row" spacing={0.75} alignItems="flex-start" sx={{ mt: 1.25, pt: 1.25, borderTop: "1px dashed", borderColor: "divider" }}>
            <Iconify icon="solar:target-linear" width={13} sx={{ color: "#16A34A", flexShrink: 0, mt: "2px" }} />
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{row.expected}</Typography>
          </Stack>
        </Box>
        <IconButton size="small" onClick={onRemove}>
          <Iconify icon="solar:close-circle-linear" width={15} sx={{ color: "text.subtitle" }} />
        </IconButton>
      </Stack>
    </Box>
  );
}
GeneratedCard.propTypes = { row: PropTypes.object, index: PropTypes.number, onRemove: PropTypes.func };

function GhostCard() {
  return (
    <Box
      sx={{
        p: 2, borderRadius: 1.25,
        border: "1px dashed", borderColor: "divider",
        bgcolor: (t) => alpha(t.palette.primary.main, 0.03),
      }}
    >
      <ThinkingBar label="Writing the next scenario" />
    </Box>
  );
}
