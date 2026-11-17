import React from "react";
import PropTypes from "prop-types";
import { Stack, Typography } from "@mui/material";
import Term from "src/sections/common/ux/Term";
import ConversationThread from "src/sections/develop-detail/DataTab/DatapointDrawerV2/ConversationThread";
import {
  failingTurnNote,
  parseConversation,
} from "src/sections/develop-detail/TaskSuite/itemModel";
import { DraftChip, KindBadge } from "./ImportItemPreview";
import { conversationOfCells } from "./mappingModel";

/** `name · one-line rule` of a drafted check. */
const ruleOf = (check) =>
  check?.rule ?? check?.description ?? check?.summary ?? null;

/**
 * IT-R4 G5 P3: the import sheet's middle column for the selected source
 * card — the whole conversation (failing turn marked with its reason) and
 * the drafted checks the row will carry. Null when the item has no
 * conversation.
 */
const SelectedItemPreview = ({ item }) => {
  const raw = item ? conversationOfCells(item.cells) : null;
  const conv = raw ? parseConversation(raw) : null;
  if (!item || !conv) return null;
  const failNote = failingTurnNote(conv, item.production);
  const checks = Array.isArray(item.cells?.checks) ? item.cells.checks : [];
  return (
    <Stack
      spacing={1}
      data-testid="add-data-selected-preview"
      sx={{ mt: 1.5, pt: 1.5, borderTop: "1px solid", borderColor: "divider" }}
    >
      <Stack direction="row" alignItems="center" gap={0.75}>
        <Typography typography="s2" fontWeight={600} sx={{ flex: 1 }} noWrap>
          {item.label ?? item.source?.session_name ?? "Selected session"}
        </Typography>
        <KindBadge kind={item.kind} />
      </Stack>
      <ConversationThread
        conv={conv}
        toolSource={raw}
        failNote={failNote}
        compact
        replayTurn={failNote?.turn ?? conv.failingTurn ?? undefined}
      />
      {checks.length > 0 && (
        <Stack spacing={0.5} data-testid="add-data-selected-checks">
          <Typography typography="s3" color="text.secondary">
            <Term k="rowChecks">Checks</Term>
            {` · ${checks.length}`}
          </Typography>
          {checks.map((check, index) => (
            <Stack
              key={check.key ?? check.name ?? index}
              direction="row"
              gap={0.75}
              alignItems="flex-start"
            >
              <Typography typography="s3" sx={{ flex: 1, minWidth: 0 }}>
                <strong>{check.name ?? `Check ${index + 1}`}</strong>
                {ruleOf(check) ? ` · ${ruleOf(check)}` : ""}
              </Typography>
              {check.draft !== false && <DraftChip />}
            </Stack>
          ))}
        </Stack>
      )}
    </Stack>
  );
};

SelectedItemPreview.propTypes = { item: PropTypes.object };

export default SelectedItemPreview;
