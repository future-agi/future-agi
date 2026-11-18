import PropTypes from "prop-types";
import { Box, Button, Stack, Typography } from "@mui/material";
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
  onAddMany = null,
  added = [],
}) {
  return (
    <ClosableDialog
      open={open}
      onClose={onClose}
      fullWidth
      maxWidth={false}
      PaperProps={{ sx: { maxWidth: 640, p: 2.5 } }}
    >
      <Typography sx={{ typography: "m2", fontWeight: 600 }}>
        Add from the Knowledge base
      </Typography>
      <Typography
        title={`${KB_SNAPSHOT_LINE} ${KB_STATE_LINE} ${KB_LIMIT_LINE}`}
        sx={{ typography: "s3", color: "text.tertiary", mt: 0.5 }}
      >
        {/* The Knowledge base has files, not versions or access requests: a
            file is copied as it is now, and a later change there arrives as a
            refresh to review. */}
        Copy files into this environment · PDF, DOCX, RTF, TXT up to 5 MB
      </Typography>

      <Box sx={{ mt: 1.5, maxHeight: 420, overflowY: "auto" }}>
        {kbCollections().map((c) => {
          /* Only files you can open are listed — the Knowledge base's own access. */
          const files = c.files.filter((o) => o.access);
          if (!files.length) return null;
          const toAdd = files.filter((o) => !added.includes(o.name));
          return (
            <Box key={c.name}>
              <Stack
                direction="row"
                alignItems="center"
                sx={{
                  py: 0.5,
                  borderBottom: "1px solid",
                  borderColor: "divider",
                }}
              >
                <Typography
                  sx={{
                    typography: "s3",
                    fontWeight: 600,
                    color: "text.tertiary",
                    flex: 1,
                  }}
                >{`${c.name} · ${files.length} ${files.length === 1 ? "file" : "files"}`}</Typography>
                {toAdd.length > 1 && (
                  <Button
                    size="small"
                    color="inherit"
                    onClick={() =>
                      onAddMany
                        ? onAddMany(toAdd)
                        : toAdd.forEach((o) => onAddOrg({ ...o }))
                    }
                    sx={{ minWidth: 0, color: "text.secondary" }}
                  >
                    {`Add all ${toAdd.length}`}
                  </Button>
                )}
              </Stack>
              {files.map((o) => {
                const isAdded = added.includes(o.name);
                return (
                  <Stack
                    key={o.id}
                    direction="row"
                    alignItems="center"
                    spacing={1.5}
                    sx={{
                      py: 1,
                      "& + &": {
                        borderTop: "1px solid",
                        borderColor: "divider",
                      },
                    }}
                  >
                    <Box sx={{ flex: 1, minWidth: 0 }}>
                      <Typography
                        noWrap
                        sx={{ typography: "s2", fontWeight: 600 }}
                      >
                        {o.name}
                      </Typography>
                      <Typography
                        sx={{ typography: "s3", color: "text.tertiary" }}
                      >
                        {isAdded
                          ? "Added — if it changes in the Knowledge base, you review the change"
                          : typeLabelOf(o)}
                      </Typography>
                    </Box>
                    <Button
                      size="small"
                      variant="outlined"
                      disabled={isAdded}
                      onClick={() => onAddOrg({ ...o })}
                    >
                      {isAdded ? "Added" : "Add"}
                    </Button>
                  </Stack>
                );
              })}
            </Box>
          );
        })}
      </Box>
    </ClosableDialog>
  );
}

KnowledgeBasePicker.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func.isRequired,
  onAddOrg: PropTypes.func.isRequired,
  onAddMany: PropTypes.func,
  added: PropTypes.arrayOf(PropTypes.string),
};
