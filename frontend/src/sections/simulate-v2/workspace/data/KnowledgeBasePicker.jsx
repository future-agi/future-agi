import PropTypes from "prop-types";
import { useState } from "react";
import {
  Box,
  Button,
  MenuItem,
  Select,
  Stack,
  Typography,
} from "@mui/material";
import ClosableDialog from "../../components/ClosableDialog";
import {
  KB_LIMIT_LINE,
  KB_SNAPSHOT_LINE,
  KB_STATE_LINE,
  typeLabelOf,
} from "../../_mock/orgData";
import { kbCollections } from "../../_mock/kbCatalog";

/**
 * Choose a file from the Knowledge base: collections, a file's revision to
 * copy, and a plain statement of what choosing does. It copies a snapshot of
 * the revision into the environment's draft — it isn't linked live, and
 * nothing reaches the world until the builder's proposal is accepted.
 */
export default function KnowledgeBasePicker({
  open,
  onClose,
  onAddOrg,
  onRequestAccess,
  added = [],
}) {
  const [picked, setPicked] = useState({});
  return (
    <ClosableDialog
      open={open}
      onClose={onClose}
      fullWidth
      maxWidth={false}
      PaperProps={{ sx: { maxWidth: 640, p: 2.5 } }}
    >
      <Typography sx={{ typography: "m2", fontWeight: 600 }}>
        Choose from Knowledge base
      </Typography>
      <Typography sx={{ typography: "s2", mt: 1 }}>
        {KB_SNAPSHOT_LINE}
      </Typography>
      <Typography sx={{ typography: "s3", color: "text.tertiary", mt: 0.5 }}>
        {KB_STATE_LINE}
      </Typography>
      <Typography sx={{ typography: "s3", color: "text.tertiary", mt: 0.5 }}>
        {`${KB_LIMIT_LINE} Updates are never pulled in on their own — a new revision waits for you to review it.`}
      </Typography>

      <Box sx={{ mt: 1.5, maxHeight: 420, overflowY: "auto" }}>
        {kbCollections().map((c) => (
          <Box key={c.name}>
            <Typography
              sx={{
                typography: "s3",
                fontWeight: 600,
                color: "text.tertiary",
                py: 0.75,
                borderBottom: "1px solid",
                borderColor: "divider",
              }}
            >{`${c.name} · ${c.owner} · ${c.files.length} ${c.files.length === 1 ? "file" : "files"}`}</Typography>
            {c.files.map((o) => {
              const isAdded = o.access && added.includes(o.name);
              const rev = picked[o.id] ?? o.revision;
              return (
                <Stack
                  key={o.id}
                  direction="row"
                  alignItems="center"
                  spacing={1.5}
                  sx={{
                    py: 1,
                    "& + &": { borderTop: "1px solid", borderColor: "divider" },
                  }}
                >
                  <Box sx={{ flex: 1, minWidth: 0 }}>
                    <Typography
                      noWrap
                      sx={{ typography: "s2", fontWeight: 600 }}
                    >
                      {o.access ? o.name : `${o.owner} file`}
                    </Typography>
                    <Typography
                      sx={{ typography: "s3", color: "text.tertiary" }}
                    >
                      {isAdded
                        ? "Already a source — a new revision arrives as a refresh to review"
                        : o.access
                          ? `${typeLabelOf(o)} · latest rev ${o.revision}`
                          : "No access"}
                    </Typography>
                  </Box>
                  {o.access && !isAdded && (
                    <Select
                      size="small"
                      value={rev}
                      onChange={(e) =>
                        setPicked((cur) => ({ ...cur, [o.id]: e.target.value }))
                      }
                      inputProps={{ "aria-label": `Revision of ${o.name}` }}
                      sx={{ typography: "s3", minWidth: 220 }}
                    >
                      {[...o.revisions].reverse().map((r) => (
                        <MenuItem
                          key={r.rev}
                          value={r.rev}
                          sx={{ typography: "s3" }}
                        >
                          {`rev ${r.rev}${r.rev === o.revision ? " (latest)" : ""} · ${r.note}`}
                        </MenuItem>
                      ))}
                    </Select>
                  )}
                  {o.access ? (
                    <Button
                      size="small"
                      variant="outlined"
                      disabled={isAdded}
                      onClick={() => onAddOrg({ ...o, revision: rev })}
                    >
                      Copy rev {rev}
                    </Button>
                  ) : (
                    <Button
                      size="small"
                      variant="outlined"
                      onClick={() => onRequestAccess?.(o)}
                    >
                      Request access
                    </Button>
                  )}
                </Stack>
              );
            })}
          </Box>
        ))}
      </Box>
    </ClosableDialog>
  );
}

KnowledgeBasePicker.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func.isRequired,
  onAddOrg: PropTypes.func.isRequired,
  onRequestAccess: PropTypes.func,
  added: PropTypes.arrayOf(PropTypes.string),
};
