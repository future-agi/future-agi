import PropTypes from "prop-types";
import { useMemo, useRef, useState } from "react";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography, ButtonBase, IconButton } from "@mui/material";
import Iconify from "src/components/iconify";
import WidgetRenderer from "src/components/imagine/WidgetRenderer";
import { resolvePrompt, IMAGINE_SUGGESTIONS } from "../../_mock/imagineResolver";

/**
 * Imagine — the exploratory analytics tab of the Debug-failures drawer.
 *
 * Diagnosis is a fixed six-analyzer view; Imagine is user-driven follow-ups
 * on the same run. Left = a canvas of widgets that answer the question the
 * user just asked. Right = the chat that produced them.
 *
 * Grounded in the run's actual tasks[] — every number is computed by
 * `resolvePrompt` from the passed-in data, so a chart reader can trace a
 * bar back to specific scenarios. No Falcon, no trace_id, no save-view;
 * the simulation prototype is fully local, so Imagine here has to be too.
 *
 * Talks to the shared widget primitives via `WidgetRenderer`, so a
 * donut / bar / table here looks identical to the same widget on the
 * Observe trace drawer.
 */
export default function ImaginePane({ tasks, env }) {
  const [messages, setMessages] = useState([]);
  const [widgets, setWidgets] = useState([]);
  const [input, setInput] = useState("");
  const scrollRef = useRef(null);

  const ctx = useMemo(() => ({ tasks, env }), [tasks, env]);

  const send = (text) => {
    const prompt = (text || "").trim();
    if (!prompt) return;
    const userMsg = { id: `u-${Date.now()}`, role: "user", text: prompt };
    setMessages((prev) => [...prev, userMsg]);
    setInput("");

    /* Small delay so the message list has a beat between the user's
       message landing and the widgets appearing — otherwise widget +
       reply flash in the same frame and the interaction reads as
       instant/mechanical rather than "the assistant thought about it". */
    setTimeout(() => {
      const result = resolvePrompt(prompt, ctx);
      const reply = result?.reply || "I couldn't build a view for that. Try one of the suggested prompts.";
      const newWidgets = result?.widgets || [];
      setMessages((prev) => [...prev, { id: `a-${Date.now()}`, role: "assistant", text: reply, widgetIds: newWidgets.map((w) => w.id) }]);
      if (newWidgets.length) setWidgets((prev) => [...newWidgets, ...prev]);
      /* Scroll the message list to the bottom on next tick. */
      setTimeout(() => {
        scrollRef.current?.scrollTo?.({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
      }, 30);
    }, 260);
  };

  const removeWidget = (id) => setWidgets((prev) => prev.filter((w) => w.id !== id));
  const clearAll = () => { setWidgets([]); setMessages([]); };

  return (
    <Box sx={{ display: "grid", gridTemplateColumns: "1fr 320px", flex: 1, minHeight: 0, height: "100%" }}>
      {/* ── canvas (left) ── */}
      <Box sx={{ minWidth: 0, minHeight: 0, overflow: "auto", borderRight: "1px solid", borderColor: "divider" }}>
        {widgets.length === 0 ? (
          <EmptyCanvas onPick={send} />
        ) : (
          <Stack spacing={1.5} sx={{ p: 2 }}>
            {widgets.map((w) => (
              <Box
                key={w.id}
                sx={{
                  border: "1px solid", borderColor: "divider", borderRadius: 1.25,
                  bgcolor: "background.paper", overflow: "hidden", position: "relative",
                }}
              >
                {w.subtitle && (
                  <Typography sx={{ typography: "s3", color: "text.subtitle", px: 2, pt: 1.5 }}>
                    {w.subtitle}
                  </Typography>
                )}
                <Box sx={{ minHeight: 240, height: w.type === "data_table" ? "auto" : 300 }}>
                  <WidgetRenderer widget={w} />
                </Box>
                <IconButton
                  size="small"
                  onClick={() => removeWidget(w.id)}
                  sx={{ position: "absolute", top: 6, right: 6, color: "text.disabled", "&:hover": { color: "text.primary" } }}
                >
                  <Iconify icon="eva:close-fill" width={14} />
                </IconButton>
              </Box>
            ))}
          </Stack>
        )}
      </Box>

      {/* ── chat (right) ── */}
      <Stack sx={{ minHeight: 0, height: "100%" }}>
        <Stack
          direction="row" alignItems="center" spacing={1}
          sx={{ px: 1.75, py: 1.25, borderBottom: "1px solid", borderColor: "divider", flexShrink: 0 }}
        >
          <Box
            sx={{
              width: 22, height: 22, borderRadius: 0.75, display: "grid", placeItems: "center",
              bgcolor: (t) => alpha("#7857FC", t.palette.mode === "dark" ? 0.16 : 0.1), color: "#7857FC",
            }}
          >
            <Iconify icon="solar:magic-stick-3-bold" width={13} />
          </Box>
          <Typography sx={{ typography: "s2", fontWeight: 700, flex: 1 }}>Ask about this run</Typography>
          {(messages.length > 0 || widgets.length > 0) && (
            <ButtonBase onClick={clearAll} sx={{ typography: "s3", color: "text.secondary", "&:hover": { color: "text.primary" } }}>
              Clear
            </ButtonBase>
          )}
        </Stack>

        <Box ref={scrollRef} sx={{ flex: 1, minHeight: 0, overflow: "auto", px: 1.75, py: 1.5 }}>
          {messages.length === 0 ? (
            <Typography sx={{ typography: "s3", color: "text.disabled" }}>
              Ask a question about the run — persona, rule, latency, where things break. Answers appear as charts on the left.
            </Typography>
          ) : (
            <Stack spacing={1.25}>
              {messages.map((m) => (
                <ChatBubble key={m.id} message={m} />
              ))}
            </Stack>
          )}
        </Box>

        <Box sx={{ p: 1.25, borderTop: "1px solid", borderColor: "divider", flexShrink: 0 }}>
          <Box
            component="form"
            onSubmit={(e) => { e.preventDefault(); send(input); }}
            sx={{
              display: "flex", alignItems: "center", gap: 0.75,
              border: "1px solid", borderColor: "divider", borderRadius: 1,
              px: 1, py: 0.5, bgcolor: "background.paper",
              "&:focus-within": { borderColor: "text.primary" },
            }}
          >
            <Iconify icon="solar:magnifer-linear" width={13} sx={{ color: "text.disabled" }} />
            <Box
              component="input"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="Ask about this run…"
              sx={{
                flex: 1, border: "none", outline: "none", bgcolor: "transparent",
                typography: "s2", color: "text.primary", fontFamily: "inherit",
                "&::placeholder": { color: "text.disabled" },
              }}
            />
            <IconButton
              type="submit" size="small" disabled={!input.trim()}
              sx={{ color: input.trim() ? "primary.main" : "text.disabled" }}
            >
              <Iconify icon="solar:arrow-right-linear" width={15} />
            </IconButton>
          </Box>
        </Box>
      </Stack>
    </Box>
  );
}
ImaginePane.propTypes = {
  tasks: PropTypes.array.isRequired,
  env: PropTypes.object,
};

/* ── empty state — suggested prompts ────────────────────────────────────── */

function EmptyCanvas({ onPick }) {
  return (
    <Stack alignItems="center" justifyContent="center" spacing={2} sx={{ minHeight: "100%", p: 4, textAlign: "center" }}>
      <Box
        sx={{
          width: 48, height: 48, borderRadius: 1.5, display: "grid", placeItems: "center",
          bgcolor: (t) => alpha("#7857FC", t.palette.mode === "dark" ? 0.16 : 0.08), color: "#7857FC",
        }}
      >
        <Iconify icon="solar:magic-stick-3-linear" width={24} />
      </Box>
      <Box>
        <Typography sx={{ typography: "s1", fontWeight: 700 }}>Imagine a view</Typography>
        <Typography sx={{ typography: "s2", color: "text.subtitle", mt: 0.5, maxWidth: 420 }}>
          Ask a question about this run — the answer renders as a chart or table right here. Grounded in the actual scenarios that ran.
        </Typography>
      </Box>
      <Stack direction="row" spacing={0.75} sx={{ flexWrap: "wrap", justifyContent: "center", maxWidth: 560, rowGap: 0.75 }}>
        {IMAGINE_SUGGESTIONS.map((s) => (
          <ButtonBase
            key={s.label}
            onClick={() => onPick(s.label)}
            sx={{
              display: "inline-flex", alignItems: "center", gap: 0.5,
              px: 1.25, py: 0.625, borderRadius: 16,
              border: "1px solid", borderColor: "divider",
              bgcolor: "background.paper",
              typography: "s3", color: "text.secondary",
              whiteSpace: "nowrap",
              "&:hover": { borderColor: "text.primary", color: "text.primary" },
            }}
          >
            <Iconify icon={s.icon} width={13} />
            {s.label}
          </ButtonBase>
        ))}
      </Stack>
    </Stack>
  );
}
EmptyCanvas.propTypes = { onPick: PropTypes.func.isRequired };

/* ── chat bubble ────────────────────────────────────────────────────────── */

function ChatBubble({ message }) {
  const isUser = message.role === "user";
  return (
    <Stack
      direction="row"
      spacing={0.75}
      sx={{ justifyContent: isUser ? "flex-end" : "flex-start" }}
    >
      {!isUser && (
        <Box
          sx={{
            width: 20, height: 20, borderRadius: 0.75, display: "grid", placeItems: "center", flexShrink: 0, mt: 0.25,
            bgcolor: (t) => alpha("#7857FC", t.palette.mode === "dark" ? 0.16 : 0.1), color: "#7857FC",
          }}
        >
          <Iconify icon="solar:magic-stick-3-bold" width={12} />
        </Box>
      )}
      <Box
        sx={{
          px: 1.25, py: 0.875, borderRadius: 1, maxWidth: "88%",
          bgcolor: isUser ? "action.selected" : "background.paper",
          border: isUser ? "none" : "1px solid",
          borderColor: "divider",
        }}
      >
        <Typography sx={{ typography: "s3", color: "text.primary", whiteSpace: "pre-wrap" }}>
          {message.text}
        </Typography>
      </Box>
    </Stack>
  );
}
ChatBubble.propTypes = { message: PropTypes.object.isRequired };
