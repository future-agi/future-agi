import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import PropTypes from "prop-types";
import { Box, ButtonBase, Collapse, Stack, Typography } from "@mui/material";
import { alpha } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import ValueViewer from "src/sections/common/ValueViewer/ValueViewer";
import useValueViewMode from "src/sections/common/ValueViewer/useValueViewMode";
import {
  failingTurnNote,
  findConversationCell,
  parseConversation,
  parseJson,
} from "src/sections/develop-detail/TaskSuite/itemModel";
import countTurns from "src/sections/common/Values/countTurns";
import ConversationThread from "./ConversationThread";
import ItemTabStrip from "./ItemTabStrip";
import TurnTimeline from "./TurnTimeline";
import { useRowCells } from "./itemHooks";
import { expiredCopyText } from "./RowViewStates";
import {
  isExpiredSource,
  sourceKindOf,
} from "src/sections/develop-detail/TaskSuite/itemModel";
import SessionConversation from "./SessionConversation";

/** `Conversation: Fail · Resolution · "…" · First failing turn: 4`. */
const VerdictLine = ({ conv, onTurn }) => {
  if (!conv.resolution) return null;
  const fail = conv.resolution === "Fail";
  return (
    <Stack
      direction="row"
      alignItems="flex-start"
      gap={0.75}
      data-testid="conversation-verdict"
      sx={{
        px: 1.25,
        py: 0.875,
        borderRadius: 1,
        bgcolor: (t) =>
          alpha(fail ? t.palette.red[600] : t.palette.green[600], 0.08),
      }}
    >
      <Iconify
        icon={fail ? "mdi:close-circle" : "mdi:check-circle"}
        width={16}
        sx={{ color: fail ? "red.600" : "green.600", mt: "1px", flexShrink: 0 }}
      />
      <Typography typography="s2" sx={{ minWidth: 0 }}>
        <Box component="span" fontWeight={600}>
          Conversation: {conv.resolution}
        </Box>
        {" · Resolution"}
        {conv.resolutionReason ? ` · "${conv.resolutionReason}"` : ""}
        {conv.failingTurn ? (
          <>
            {" · "}
            <ButtonBase
              onClick={() => onTurn(conv.failingTurn)}
              data-testid="verdict-first-failing"
              sx={{
                typography: "s2",
                fontWeight: 600,
                color: "red.600",
                verticalAlign: "baseline",
                "&:hover": { textDecoration: "underline" },
              }}
            >
              First failing turn: {conv.failingTurn}
            </ButtonBase>
          </>
        ) : null}
      </Typography>
    </Stack>
  );
};
VerdictLine.propTypes = { conv: PropTypes.object, onTurn: PropTypes.func };

/** The conversation's system prompt, collapsed on top. */
const SystemPrompt = ({ text }) => {
  const [open, setOpen] = useState(false);
  if (!text) return null;
  return (
    <Box sx={{ border: "1px solid", borderColor: "divider", borderRadius: 1 }}>
      <ButtonBase
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        sx={{
          width: "100%",
          justifyContent: "flex-start",
          gap: 0.5,
          px: 1,
          py: 0.75,
        }}
      >
        <Iconify
          icon={open ? "mdi:chevron-down" : "mdi:chevron-right"}
          width={16}
        />
        <Typography typography="s2" fontWeight={600}>
          System prompt
        </Typography>
        {!open && (
          <Typography
            typography="s3"
            color="text.secondary"
            noWrap
            sx={{ ml: 1, minWidth: 0 }}
          >
            {text}
          </Typography>
        )}
      </ButtonBase>
      <Collapse in={open} unmountOnExit>
        <Typography
          typography="s2"
          sx={{ px: 1.5, pb: 1, whiteSpace: "pre-wrap" }}
        >
          {text}
        </Typography>
      </Collapse>
    </Box>
  );
};
SystemPrompt.propTypes = { text: PropTypes.string };

/**
 * Conversation tab of the Row view (R2-SPEC §7.3.6 + F9): the verdict line,
 * the turn strip, the system prompt, the conversation as bubbles with the
 * failing turn labelled, and the tool calls. Raw = the value as JSON.
 * v2.1 g8: no Replay section here (the chosen run did not decide the
 * replay); the timeline and tool calls stay.
 *
 * Props (frozen):
 * @param {string} props.datasetId
 * @param {string} props.rowId
 * @param {object} props.row  get-row-data row (`row_meta.source`, `kind`)
 */
const ConversationTab = ({ datasetId, rowId, row }) => {
  const cells = useRowCells(datasetId, row);
  const [mode, setMode] = useValueViewMode();
  // IT-R3 G5 P2: the conversation wherever it lives (a `conversation`
  // column, or an imported `{turns}` value in a json / text column).
  const rawValue = findConversationCell(cells)?.value ?? null;
  const conv = useMemo(() => parseConversation(rawValue), [rawValue]);
  const production = row?.row_meta?.production ?? row?.production ?? null;
  const failNote = useMemo(
    () => failingTurnNote(conv, production),
    [conv, production],
  );
  const [current, setCurrent] = useState(null);
  const [flash, setFlash] = useState(null);
  const refs = useRef({});

  useEffect(() => {
    setCurrent(null);
  }, [rowId, conv?.failingTurn]);

  const goTo = useCallback((n) => {
    setCurrent(n);
    setFlash(n);
    refs.current[n]?.scrollIntoView({ block: "center", behavior: "smooth" });
    window.setTimeout(() => setFlash((f) => (f === n ? null : f)), 1200);
  }, []);

  const source = row?.row_meta?.source ?? null;
  if (!conv && sourceKindOf(source) === "session") {
    return (
      <SessionConversation datasetId={datasetId} row={row} source={source} />
    );
  }
  if (!conv) {
    return (
      <Box sx={{ p: 3 }}>
        <Typography typography="s2" color="text.secondary">
          This row has no conversation.
        </Typography>
      </Box>
    );
  }
  const channel =
    conv.channel === "voice"
      ? "Voice call"
      : conv.channel === "chat"
        ? "Chat"
        : null;
  const turnCount = countTurns(conv.turns);

  return (
    <Box sx={{ minWidth: 0 }}>
      <ItemTabStrip
        mode={mode}
        setMode={setMode}
        copyValue={parseJson(rawValue) ?? rawValue}
        copyLabel="Copy the conversation as JSON"
      >
        <Typography typography="s3" color="text.secondary" noWrap>
          {[channel, `${turnCount} ${turnCount === 1 ? "turn" : "turns"}`]
            .filter(Boolean)
            .join(" · ")}
        </Typography>
      </ItemTabStrip>
      {mode === "raw" ? (
        <Box sx={{ px: 2, py: 1.5 }}>
          <ValueViewer value={rawValue} dataType="json" mode="raw" />
        </Box>
      ) : (
        <Stack gap={1.5} sx={{ px: 2, pt: 1.5, pb: 3 }}>
          {/* integration (DM-07): an expired session shows its kept copy. */}
          {isExpiredSource(row?.row_meta?.source) ? (
            <Stack
              direction="row"
              alignItems="center"
              gap={0.75}
              data-testid="conversation-expired-note"
              sx={{
                px: 1.25,
                py: 0.75,
                borderRadius: 1,
                bgcolor: (t) => alpha(t.palette.amber[500], 0.12),
              }}
            >
              <Iconify
                icon="mdi:history"
                width={16}
                sx={{ color: "amber.700", flexShrink: 0 }}
              />
              <Typography typography="s2">
                {expiredCopyText(row.row_meta.source)}
              </Typography>
            </Stack>
          ) : null}
          <VerdictLine
            conv={
              conv.resolution || !production
                ? conv
                : {
                    ...conv,
                    resolution: "Fail",
                    resolutionReason: production.reason ?? "",
                    failingTurn: failNote?.turn ?? conv.failingTurn,
                  }
            }
            onTurn={goTo}
          />
          <TurnTimeline conv={conv} current={current} onPick={goTo} />
          <SystemPrompt text={conv.systemPrompt} />
          <ConversationThread
            conv={conv}
            toolSource={rawValue}
            failNote={failNote}
            replayTurn={failNote?.turn ?? conv.failingTurn}
            flashTurn={flash}
            turnRef={(n, el) => {
              refs.current[n] = el;
            }}
          />
        </Stack>
      )}
    </Box>
  );
};

ConversationTab.propTypes = {
  datasetId: PropTypes.string,
  rowId: PropTypes.string,
  row: PropTypes.object,
};

export default ConversationTab;
