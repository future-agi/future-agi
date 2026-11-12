import React, { useState } from "react";
import PropTypes from "prop-types";
import { Box, Link, Menu, MenuItem, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { notify } from "src/sections/common/ux/toast";
import { useRegisterCommands } from "src/sections/common/CommandPalette/commandRegistry";
import {
  collabError,
  fetchAssignments,
  fetchDatasetComments,
  useAssignments,
  useDatasetComments,
} from "src/api/develop/row-collab";
import { isPendingRowId } from "src/sections/develop-detail/DataTab/Versions/pendingRows";
import { resolveSelectedRows } from "src/sections/develop-detail/DataTab/DataGrid/selectionRows";
import AssignDialog from "./AssignDialog";
import { footerAssignedText } from "./assignModel";
import {
  MENTIONS_VIEW,
  OPEN_COMMENTS_VIEW,
  mentionsText,
  openCommentsText,
} from "./commentModel";

const linkSx = {
  verticalAlign: "baseline",
  textDecorationStyle: "dotted",
  whiteSpace: "nowrap",
};

// The views the Assigned links open (they count rows).
const ASSIGNED_VIEW = {
  me: "Rows assigned to you",
  anyone: "Rows assigned to anyone",
  nobody: "Unassigned rows",
};

const Dot = () => (
  <Box component="span" sx={{ color: "text.disabled", px: 0.75 }}>
    ·
  </Box>
);

/**
 * W4-1 / W4-2 in the Data grid footer: "3 open comments on 2 rows", "1
 * mention of you", "5 assigned to you · 1 overdue" and "Assigned ▾". Each
 * opens the grid's `system:row_ids` view with ids read from the server at
 * click time; the view counts rows (`setRowIds(ids, label)` names the pill
 * "Rows with open comments (2)"), so a link names its rows whenever they
 * differ from what it counts. Links show only when their count is above 0.
 * Also the ⌘K commands of both items.
 * `selection` = `{selectAll, toggledNodes, count, api}` from the grid.
 */
const CollabFooterLinks = ({ datasetId, gridView, selection }) => {
  const [menu, setMenu] = useState(null);
  const [assignRows, setAssignRows] = useState(null);
  const comments = useDatasetComments(datasetId, { status: "open" });
  const mine = useAssignments(datasetId, { assignee: "me", status: "open" });
  const anyone = useAssignments(datasetId, {
    assignee: "anyone",
    status: "open",
  });
  const open = comments.data?.total_open ?? 0;
  const mentions = comments.data?.mentions_of_me ?? 0;
  const assigned = footerAssignedText(mine.data?.counts, mine.data?.open_rows);
  const people = anyone.data?.people ?? [];
  const meId = mine.data?.me?.id;
  const canAssign = Boolean(anyone.data?.me?.perms?.edit);

  // W4 §9 (M6): a grid that takes live views gets the live key, which the
  // server answers at query time (it follows new comments and
  // assignments); otherwise the ids read now (`system:row_ids`).
  const show = async (read, label, live) => {
    if (live && gridView?.setCollabView) {
      gridView.setCollabView({ ...live, label });
      return;
    }
    try {
      const ids = await read();
      gridView.setRowIds(ids ?? [], label);
    } catch (e) {
      notify(collabError(e, "Couldn't read those rows."), { variant: "error" });
    }
  };
  const showOpen = () =>
    show(
      () =>
        fetchDatasetComments(datasetId, { status: "open" }).then(
          (r) => r?.row_ids,
        ),
      OPEN_COMMENTS_VIEW,
      { key: "system:open_comments", value: "open" },
    );
  const showMentions = () =>
    show(
      () =>
        fetchDatasetComments(datasetId, { status: "all", mentions: "me" }).then(
          (r) => r?.mention_row_ids ?? r?.row_ids,
        ),
      MENTIONS_VIEW,
      { key: "system:open_comments", value: "mentions_me" },
    );
  const showAssigned = (assignee, label) =>
    show(
      () =>
        fetchAssignments(datasetId, { assignee, status: "open" }).then(
          (r) => r?.row_ids,
        ),
      label,
      { key: "system:assigned_to", value: assignee },
    );
  const assignSelected = async () => {
    try {
      const rows = await resolveSelectedRows({
        datasetId,
        api: selection?.api,
        selectAll: selection?.selectAll,
        toggledNodes: selection?.toggledNodes,
      });
      const ids = rows
        .filter((r) => !r.pending && !isPendingRowId(r.row_id))
        .map((r) => r.row_id);
      if (!ids.length) {
        notify("Save the new rows first; only saved rows can be assigned.", {
          key: "assign-none",
        });
        return;
      }
      setAssignRows(ids);
    } catch (e) {
      notify(collabError(e, "Couldn't read the selected rows."), {
        variant: "error",
      });
    }
  };

  const selected = selection?.count ?? 0;
  useRegisterCommands(
    `w4-collab-${datasetId}`,
    datasetId
      ? [
          {
            id: "rows.showOpenComments",
            group: "Rows",
            label: "Show rows with open comments",
            icon: "mdi:comment-outline",
            keywords: ["comments", "threads", "discussion"],
            run: showOpen,
            when: () => open > 0,
          },
          {
            id: "rows.showMentions",
            group: "Rows",
            label: "Show rows that mention you",
            icon: "mdi:at",
            keywords: ["mentions", "@"],
            run: showMentions,
            when: () => mentions > 0,
          },
          {
            id: "rows.showAssignedToMe",
            group: "Rows",
            label: "Show rows assigned to me",
            icon: "mdi:account-arrow-right-outline",
            keywords: ["assigned", "todo", "mine"],
            run: () => showAssigned("me", ASSIGNED_VIEW.me),
            when: () => (mine.data?.counts?.open ?? 0) > 0,
          },
          {
            id: "rows.assignSelected",
            group: "Rows",
            label: `Assign ${selected.toLocaleString("en-US")} selected row${selected === 1 ? "" : "s"}…`,
            icon: "mdi:account-plus-outline",
            keywords: ["assign", "teammate", "due"],
            run: assignSelected,
            when: () => canAssign && selected > 0,
          },
        ]
      : [],
    [datasetId, open, mentions, selected, canAssign, mine.data?.counts?.open],
  );

  const parts = [];
  if (open > 0)
    parts.push(
      <Link
        key="open"
        component="button"
        type="button"
        typography="s2"
        color="inherit"
        underline="always"
        onClick={showOpen}
        data-testid="footer-open-comments"
        sx={linkSx}
      >
        {openCommentsText(open, comments.data?.open_rows)}
      </Link>,
    );
  if (mentions > 0)
    parts.push(
      <Link
        key="mentions"
        component="button"
        type="button"
        typography="s2"
        color="inherit"
        underline="always"
        onClick={showMentions}
        data-testid="footer-mentions"
        sx={linkSx}
      >
        {mentionsText(mentions, comments.data?.mention_row_ids?.length)}
      </Link>,
    );
  if (assigned)
    parts.push(
      <Link
        key="mine"
        component="button"
        type="button"
        typography="s2"
        color="inherit"
        underline="always"
        onClick={() => showAssigned("me", ASSIGNED_VIEW.me)}
        data-testid="footer-assigned-to-me"
        sx={linkSx}
      >
        {assigned}
      </Link>,
    );
  if (people.length || (anyone.data?.counts?.open ?? 0) > 0)
    parts.push(
      <Link
        key="menu"
        component="button"
        type="button"
        typography="s2"
        color="inherit"
        underline="none"
        onClick={(e) => setMenu(e.currentTarget)}
        data-testid="footer-assigned-menu"
        sx={{ ...linkSx, display: "inline-flex", alignItems: "center" }}
      >
        Assigned
        <Iconify icon="eva:chevron-down-fill" width={14} />
      </Link>,
    );

  return (
    <>
      {parts.length > 0 && (
        <Typography
          component="div"
          typography="s2"
          color="text.secondary"
          noWrap
          data-testid="footer-collab"
          sx={{
            display: "flex",
            alignItems: "center",
            flexShrink: 0,
          }}
        >
          {parts.map((p) => (
            <React.Fragment key={p.key}>
              <Dot />
              {p}
            </React.Fragment>
          ))}
        </Typography>
      )}
      <Menu anchorEl={menu} open={Boolean(menu)} onClose={() => setMenu(null)}>
        {[
          // Each number is the rows its view shows.
          {
            key: "me",
            label: "To me",
            view: ASSIGNED_VIEW.me,
            n: mine.data?.open_rows,
          },
          {
            key: "anyone",
            label: "To anyone",
            view: ASSIGNED_VIEW.anyone,
            n: anyone.data?.open_rows,
          },
          {
            key: "nobody",
            label: "To nobody",
            view: ASSIGNED_VIEW.nobody,
            n: anyone.data?.counts?.unassigned,
          },
          ...people
            .filter((p) => p.id !== meId)
            .map((p) => ({
              key: p.id,
              label: `To ${p.name}`,
              view: `Rows assigned to ${p.name}`,
              n: p.rows,
            })),
        ].map((o) => (
          <MenuItem
            key={o.key}
            data-testid={`footer-assigned-${o.key}`}
            sx={{ typography: "s1" }}
            onClick={() => {
              setMenu(null);
              showAssigned(o.key, o.view);
            }}
          >
            {o.label}
            {Number.isFinite(o.n) && (
              <Box component="span" sx={{ ml: 1, color: "text.disabled" }}>
                {o.n.toLocaleString("en-US")}
              </Box>
            )}
          </MenuItem>
        ))}
      </Menu>
      {assignRows && (
        <AssignDialog
          open
          onClose={() => setAssignRows(null)}
          datasetId={datasetId}
          rowIds={assignRows}
        />
      )}
    </>
  );
};

CollabFooterLinks.propTypes = {
  datasetId: PropTypes.string.isRequired,
  gridView: PropTypes.shape({
    setRowIds: PropTypes.func,
    setCollabView: PropTypes.func,
  }).isRequired,
  selection: PropTypes.object,
};

export default CollabFooterLinks;
