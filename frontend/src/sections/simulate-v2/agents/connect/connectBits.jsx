import PropTypes from "prop-types";
import { useState } from "react";
import {
  Box,
  Button,
  IconButton,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { HintLine } from "./replicationBits";

const MONO = "ui-monospace, Menlo, monospace";

/* The quiet "do it another way" link the connect panels use. */
export function TextLink({ onClick, children, sx }) {
  return (
    <Typography
      component="span"
      role="button"
      tabIndex={0}
      onClick={onClick}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onClick?.();
        }
      }}
      sx={{
        typography: "s3",
        color: "text.subtitle",
        cursor: "pointer",
        display: "inline-block",
        width: "fit-content",
        "&:hover": { color: "text.primary", textDecoration: "underline" },
        ...sx,
      }}
    >
      {children}
    </Typography>
  );
}
TextLink.propTypes = {
  onClick: PropTypes.func,
  children: PropTypes.node,
  sx: PropTypes.object,
};

/* A few lines of code to copy — the command or the wrapper the person pastes. */
export function CodeSnippet({ code }) {
  const [copied, setCopied] = useState(false);
  const copy = () => {
    navigator.clipboard?.writeText(code);
    setCopied(true);
    setTimeout(() => setCopied(false), 1600);
  };
  return (
    <Box
      sx={{
        position: "relative",
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        bgcolor: "background.neutral",
      }}
    >
      <Box
        component="pre"
        sx={{
          m: 0,
          px: 1.5,
          py: 1.125,
          pr: 5,
          overflowX: "auto",
          typography: "s2",
          fontFamily: MONO,
          color: "text.primary",
          whiteSpace: "pre",
        }}
      >
        {code}
      </Box>
      <Tooltip arrow title={copied ? "Copied" : "Copy"}>
        <IconButton
          size="small"
          onClick={copy}
          sx={{ position: "absolute", top: 6, right: 6, p: 0.5 }}
        >
          <Iconify
            icon={copied ? "solar:check-read-linear" : "solar:copy-linear"}
            width={16}
            sx={{ color: copied ? "text.primary" : "text.subtitle" }}
          />
        </IconButton>
      </Tooltip>
    </Box>
  );
}
CodeSnippet.propTypes = { code: PropTypes.string.isRequired };

/*
  One fact we read: confirm it as is, or correct it in place. A `fixed`
  fact was read from compiled code — a graph, a crew — so it can only be
  confirmed; correcting it means changing the code.
*/
export function FactRow({
  fact,
  value,
  from,
  status,
  first,
  onConfirm,
  onCorrect,
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const shown = fact.list ? value || [] : value;
  const open = () => {
    setDraft(fact.list ? (value || []).join("\n") : value || "");
    setEditing(true);
  };
  const save = () => {
    onCorrect(
      fact.list
        ? draft
            .split("\n")
            .map((x) => x.trim())
            .filter(Boolean)
        : draft.trim(),
    );
    setEditing(false);
  };
  return (
    <Stack
      direction={{ xs: "column", sm: "row" }}
      spacing={{ xs: 0.5, sm: 2 }}
      alignItems={{ sm: "flex-start" }}
      sx={{
        px: 1.75,
        py: 1.25,
        borderTop: first ? "none" : "1px solid",
        borderColor: "divider",
      }}
    >
      <Typography
        sx={{
          typography: "s2",
          color: "text.subtitle",
          width: { sm: 120 },
          flexShrink: 0,
          pt: "1px",
        }}
      >
        {fact.label}
      </Typography>
      <Box flex={1} minWidth={0}>
        {editing ? (
          <Stack spacing={0.75}>
            <TextField
              autoFocus
              fullWidth
              size="small"
              multiline={fact.list}
              minRows={fact.list ? 3 : undefined}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              helperText={fact.list ? "One per line" : null}
              sx={{
                "& .MuiInputBase-input": {
                  typography: "s2",
                  ...(fact.mono && { fontFamily: MONO }),
                },
              }}
            />
            <Stack direction="row" spacing={0.5}>
              <Button
                size="small"
                variant="contained"
                color="primary"
                onClick={save}
              >
                Save
              </Button>
              <Button
                size="small"
                onClick={() => setEditing(false)}
                sx={{ color: "text.secondary" }}
              >
                Cancel
              </Button>
            </Stack>
          </Stack>
        ) : (
          <>
            {fact.chips ? (
              <Stack direction="row" spacing={0.5} useFlexGap flexWrap="wrap">
                {shown.map((t) => (
                  <Box
                    key={t}
                    sx={{
                      px: 0.75,
                      py: 0.25,
                      borderRadius: "4px",
                      border: "1px solid",
                      borderColor: "divider",
                      typography: "s3",
                      fontFamily: MONO,
                      color: "text.secondary",
                    }}
                  >
                    {t}
                  </Box>
                ))}
                {!shown.length && (
                  <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
                    None
                  </Typography>
                )}
              </Stack>
            ) : (
              <Typography
                sx={{
                  typography: "s2",
                  fontWeight: 600,
                  wordBreak: fact.mono ? "break-all" : "normal",
                  whiteSpace: "pre-line",
                  ...(fact.mono && { fontFamily: MONO }),
                }}
              >
                {fact.list ? shown.join("\n") || "None" : shown || "—"}
              </Typography>
            )}
            {from && (
              <Typography
                sx={{ typography: "s3", color: "text.subtitle", mt: 0.25 }}
              >
                {from}
              </Typography>
            )}
          </>
        )}
      </Box>
      {!editing && (
        <Stack
          direction="row"
          alignItems="center"
          spacing={0.25}
          sx={{ flexShrink: 0, minWidth: 120, justifyContent: "flex-end" }}
        >
          {status ? (
            <Stack
              direction="row"
              alignItems="center"
              spacing={0.5}
              sx={{ color: "text.secondary", pr: 0.5 }}
            >
              <Iconify
                icon={
                  status === "corrected"
                    ? "solar:pen-2-linear"
                    : "solar:check-read-linear"
                }
                width={14}
              />
              <Typography sx={{ typography: "s3", fontWeight: 600 }}>
                {status === "corrected" ? "Corrected" : "Confirmed"}
              </Typography>
            </Stack>
          ) : (
            <Button
              size="small"
              onClick={onConfirm}
              sx={{ color: "text.primary", minWidth: 0 }}
            >
              Confirm
            </Button>
          )}
          {fact.fixed ? (
            /* Same width as the pencil, so every row's Confirm lines up. */
            <Tooltip
              arrow
              title="Read from the compiled code — change it there"
            >
              <Box
                sx={{
                  width: 30,
                  height: 30,
                  display: "grid",
                  placeItems: "center",
                }}
              >
                <Iconify
                  icon="solar:lock-keyhole-minimalistic-linear"
                  width={14}
                  sx={{ color: "text.disabled" }}
                />
              </Box>
            </Tooltip>
          ) : (
            <Tooltip arrow title="Correct it">
              <IconButton size="small" onClick={open}>
                <Iconify
                  icon="solar:pen-2-linear"
                  width={14}
                  sx={{ color: "text.subtitle" }}
                />
              </IconButton>
            </Tooltip>
          )}
        </Stack>
      )}
    </Stack>
  );
}
FactRow.propTypes = {
  fact: PropTypes.object,
  value: PropTypes.oneOfType([PropTypes.string, PropTypes.array]),
  from: PropTypes.node,
  status: PropTypes.string,
  first: PropTypes.bool,
  onConfirm: PropTypes.func,
  onCorrect: PropTypes.func,
};

/*
  A "What we found" row that isn't a single value — a pick, a flag, a list
  of references: same label column and divider as FactRow, any controls in
  the middle, an optional status or action on the right.
*/
export function DetailRow({ label, from, right, first, children }) {
  return (
    <Stack
      direction={{ xs: "column", sm: "row" }}
      spacing={{ xs: 0.5, sm: 2 }}
      alignItems={{ sm: "flex-start" }}
      sx={{
        px: 1.75,
        py: 1.25,
        borderTop: first ? "none" : "1px solid",
        borderColor: "divider",
      }}
    >
      <Typography
        sx={{
          typography: "s2",
          color: "text.subtitle",
          width: { sm: 120 },
          flexShrink: 0,
          pt: "1px",
        }}
      >
        {label}
      </Typography>
      <Box flex={1} minWidth={0}>
        {children}
        {from && (
          <Typography
            sx={{ typography: "s3", color: "text.subtitle", mt: 0.5 }}
          >
            {from}
          </Typography>
        )}
      </Box>
      {right && (
        <Stack
          direction="row"
          alignItems="center"
          spacing={0.25}
          sx={{ flexShrink: 0, minWidth: 120, justifyContent: "flex-end" }}
        >
          {right}
        </Stack>
      )}
    </Stack>
  );
}
DetailRow.propTypes = {
  label: PropTypes.node,
  from: PropTypes.node,
  right: PropTypes.node,
  first: PropTypes.bool,
  children: PropTypes.node,
};

/*
  The members an import found — a Vapi squad, a Retell conversation flow, an
  ElevenLabs workflow or a code agent's handoffs: one agent made of several,
  each with its role, model and the prompt it runs. Read-only; they're saved
  as the agent's members.
*/
export function MembersPreview({ members, title = "Members", note }) {
  if (!members?.length) return null;
  const cols = "minmax(0, 1.1fr) minmax(0, 1.4fr) 130px minmax(0, 1.2fr)";
  const head = {
    typography: "s3",
    fontWeight: 600,
    letterSpacing: 0.4,
    textTransform: "none",
    color: "text.subtitle",
  };
  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        overflow: "hidden",
      }}
    >
      <Stack
        direction="row"
        alignItems="baseline"
        spacing={1}
        sx={{
          px: 1.5,
          py: 0.875,
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        <Typography sx={{ typography: "s2", fontWeight: 600 }}>
          {title}
        </Typography>
        <Typography
          sx={{ typography: "s3", color: "text.subtitle" }}
        >{`${members.length} · saved as this agent's members`}</Typography>
      </Stack>
      <Box
        sx={{
          display: { xs: "none", sm: "grid" },
          gridTemplateColumns: cols,
          columnGap: 2,
          px: 1.5,
          py: 0.75,
          bgcolor: "background.neutral",
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        <Typography sx={head}>Name</Typography>
        <Typography sx={head}>Role</Typography>
        <Typography sx={head}>Model</Typography>
        <Typography sx={head}>Prompt</Typography>
      </Box>
      {members.map((m, i) => (
        <Box
          key={m.id || m.name}
          sx={{
            display: "grid",
            gridTemplateColumns: { xs: "1fr", sm: cols },
            columnGap: 2,
            rowGap: 0.25,
            alignItems: "center",
            px: 1.5,
            py: 0.875,
            borderTop: i ? "1px solid" : "none",
            borderColor: "divider",
          }}
        >
          <Typography noWrap sx={{ typography: "s2", fontWeight: 600 }}>
            {m.name}
          </Typography>
          <Typography noWrap sx={{ typography: "s3", color: "text.secondary" }}>
            {m.role}
          </Typography>
          <Typography
            noWrap
            sx={{ typography: "s3", fontFamily: MONO, color: "text.secondary" }}
          >
            {m.model}
          </Typography>
          <Typography
            noWrap
            sx={{ typography: "s3", fontFamily: MONO, color: "text.subtitle" }}
          >
            {m.promptRef}
          </Typography>
        </Box>
      ))}
      {note && (
        <Typography
          sx={{
            px: 1.5,
            py: 0.75,
            typography: "s3",
            color: "text.subtitle",
            borderTop: "1px solid",
            borderColor: "divider",
            bgcolor: "background.neutral",
          }}
        >
          {note}
        </Typography>
      )}
    </Box>
  );
}
MembersPreview.propTypes = {
  members: PropTypes.array,
  title: PropTypes.string,
  note: PropTypes.node,
};

/*
  What improvements can change for this framework, as a two-row spec sheet:
  what the improver edits (the tuning table) and whether its production
  traces reach the Error Feed through traceAI. `tuning` is
  frameworkTuningOf(…); nothing renders for a framework we don't know.
*/
export function FrameworkTuning({ tuning, structure }) {
  if (!tuning) return null;
  const rows = [
    ["Improvements edit", tuning.edits],
    ...(structure
      ? [
          [
            "Structure",
            `${structure} — read by the adapter, changed only with a written rationale`,
          ],
        ]
      : []),
    [
      "Production traces",
      tuning.traced
        ? "Through traceAI, into the Error Feed"
        : "traceAI doesn't instrument it — failures come from simulations",
    ],
  ];
  return (
    <Box sx={{ border: "1px solid", borderColor: "divider", borderRadius: 1 }}>
      {rows.map(([label, value], i) => (
        <Stack
          key={label}
          direction="row"
          spacing={1.5}
          sx={{
            px: 1.5,
            py: 0.875,
            borderTop: i ? "1px solid" : "none",
            borderColor: "divider",
          }}
        >
          <Typography
            sx={{
              typography: "s3",
              color: "text.subtitle",
              width: 128,
              flexShrink: 0,
            }}
          >
            {label}
          </Typography>
          <Typography
            sx={{
              typography: "s3",
              color: "text.primary",
              flex: 1,
              minWidth: 0,
            }}
          >
            {value}
          </Typography>
        </Stack>
      ))}
    </Box>
  );
}
FrameworkTuning.propTypes = {
  tuning: PropTypes.object,
  structure: PropTypes.string,
};

/** One of two or three ways to do something, as a selectable card with a one-line summary and a hint. */
export function WhereOption({ icon, label, short, hint, on, onClick }) {
  return (
    <Stack
      direction="row"
      spacing={1.25}
      alignItems="flex-start"
      role="radio"
      aria-checked={on}
      tabIndex={0}
      onClick={onClick}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onClick();
        }
      }}
      sx={{
        px: 1.5,
        py: 1.25,
        borderRadius: 1,
        border: "1px solid",
        cursor: "pointer",
        borderColor: on ? "text.primary" : "divider",
        bgcolor: on ? "action.selected" : "transparent",
      }}
    >
      <Iconify
        icon={icon}
        width={16}
        sx={{ color: "text.secondary", mt: 0.25, flexShrink: 0 }}
      />
      <Box flex={1} minWidth={0}>
        <Typography sx={{ typography: "s2", fontWeight: 600 }}>
          {label}
        </Typography>
        <Typography
          component="div"
          sx={{ typography: "s3", color: "text.subtitle", mt: 0.25 }}
        >
          <HintLine hint={hint}>{short}</HintLine>
        </Typography>
      </Box>
      <Iconify
        icon={on ? "solar:check-circle-bold" : "solar:circle-linear"}
        width={16}
        sx={{ color: on ? "text.primary" : "text.disabled", flexShrink: 0 }}
      />
    </Stack>
  );
}
WhereOption.propTypes = {
  icon: PropTypes.string,
  label: PropTypes.string,
  short: PropTypes.string,
  hint: PropTypes.string,
  on: PropTypes.bool,
  onClick: PropTypes.func,
};
