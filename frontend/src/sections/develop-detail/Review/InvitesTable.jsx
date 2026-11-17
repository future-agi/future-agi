import React from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
} from "@mui/material";
import CopyButton from "src/sections/common/ux/CopyButton";
import RelativeTime from "src/sections/common/ux/RelativeTime";
import { INVITE_STATUS_LABELS, progressWords } from "./inviteModel";

const linkOf = (inv) =>
  typeof window !== "undefined"
    ? `${window.location.origin}${inv.url_path}`
    : inv.url_path;

const LIVE = new Set(["active", "pending_approval", "done"]);

/**
 * Review summary › Outside reviewers › the invites: email · status ·
 * `k of K` · last seen, with Approve (admins, waiting invites), Copy link
 * and Revoke on links that still open.
 */
export default function InvitesTable({
  invites,
  canApprove,
  busy,
  onApprove,
  onRevoke,
}) {
  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 0.5,
        overflow: "auto",
      }}
    >
      <Table
        size="small"
        data-testid="invites-table"
        // compact cells so the actions never scroll out of the drawer
        sx={{ "& .MuiTableCell-root": { px: 1 } }}
      >
        <TableHead>
          <TableRow>
            <TableCell>Email</TableCell>
            <TableCell>Status</TableCell>
            <TableCell>Reviewed</TableCell>
            <TableCell sx={{ whiteSpace: "nowrap" }}>Last seen</TableCell>
            <TableCell align="right" />
          </TableRow>
        </TableHead>
        <TableBody>
          {invites.map((inv) => {
            const live = LIVE.has(inv.status);
            return (
              <TableRow key={inv.id} data-testid="invite-row">
                <TableCell>{inv.email}</TableCell>
                <TableCell sx={{ whiteSpace: "nowrap" }}>
                  {INVITE_STATUS_LABELS[inv.status] ?? inv.status}
                </TableCell>
                <TableCell sx={{ fontVariantNumeric: "tabular-nums" }}>
                  {progressWords(inv.progress)}
                </TableCell>
                <TableCell>
                  {inv.last_seen_at ? (
                    <RelativeTime iso={inv.last_seen_at} />
                  ) : (
                    "Not opened yet"
                  )}
                </TableCell>
                <TableCell align="right" sx={{ whiteSpace: "nowrap" }}>
                  {inv.status === "pending_approval" && canApprove ? (
                    <Button
                      size="small"
                      disabled={busy}
                      onClick={() => onApprove(inv)}
                    >
                      Approve
                    </Button>
                  ) : null}
                  {live ? (
                    <CopyButton value={() => linkOf(inv)} tooltip="Copy link" />
                  ) : null}
                  {live ? (
                    <Button
                      size="small"
                      color="error"
                      disabled={busy}
                      onClick={() => onRevoke(inv)}
                    >
                      Revoke
                    </Button>
                  ) : null}
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </Box>
  );
}

InvitesTable.propTypes = {
  invites: PropTypes.array.isRequired,
  canApprove: PropTypes.bool,
  busy: PropTypes.bool,
  onApprove: PropTypes.func.isRequired,
  onRevoke: PropTypes.func.isRequired,
};
