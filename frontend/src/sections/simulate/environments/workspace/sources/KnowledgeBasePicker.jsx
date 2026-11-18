import PropTypes from "prop-types";
import { Box, Button, Stack, Typography } from "@mui/material";
import { ClosableDialog } from "src/components/custom-dialog";
import { KNOWLEDGE_BASES } from "src/api/simulate-environments/_fixtures/sources";
import {
  useEnvironmentSources,
  useSourceCapabilities,
} from "src/api/simulate-environments/sources";

const MB = 1024 * 1024;

const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
const extOf = (name) => name.split(".").pop().toUpperCase();

/**
 * Add from the Knowledge base: its files by Knowledge base, Add per file and
 * Add all. A file is copied as it is now, not linked live. Files the person
 * can't open aren't listed. `onPick` gets the prepare selection.
 */
export default function KnowledgeBasePicker({ envId, open, onClose, onPick }) {
  const { data: summary } = useEnvironmentSources(envId);
  const { data: capabilities } = useSourceCapabilities(envId);
  const limits = capabilities?.knowledge_base;
  // Removed ones too: adding one again would find it already here.
  const added = new Set(
    (summary?.sources || [])
      .filter((source) => source.origin?.kind === "knowledge_base")
      .map((source) => source.origin.file_id),
  );
  const pick = (selection) => onPick({ kind: "knowledge_base", ...selection });

  return (
    <ClosableDialog
      open={open}
      onClose={onClose}
      fullWidth
      maxWidth={false}
      PaperProps={{ sx: { maxWidth: 640, p: 2.5 } }}
    >
      <Typography sx={{ typography: "m2", fontWeight: 600, pr: 4 }}>
        Add from the Knowledge base
      </Typography>
      <Typography sx={{ typography: "s3", color: "text.tertiary", mt: 0.5 }}>
        {[
          "Copy files into this environment",
          limits &&
            `${limits.formats.map((ext) => ext.toUpperCase()).join(", ")} up to ${Math.round(limits.max_file_bytes / MB)} MB`,
        ]
          .filter(Boolean)
          .join(" · ")}
      </Typography>

      <Box sx={{ mt: 1.5, maxHeight: 420, overflowY: "auto" }}>
        {KNOWLEDGE_BASES.map((kb) => {
          const files = kb.files.filter((file) => file.access);
          if (!files.length) return null;
          const toAdd = files.filter((file) => !added.has(file.file_id));
          return (
            <Box key={kb.kb_id}>
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
                >
                  {`${kb.name} · ${plural(files.length, "file")}`}
                </Typography>
                {toAdd.length > 1 && (
                  <Button
                    size="small"
                    color="inherit"
                    onClick={() =>
                      pick({
                        kb_id: kb.kb_id,
                        file_ids: toAdd.map((file) => file.file_id),
                      })
                    }
                    sx={{ minWidth: 0, color: "text.secondary" }}
                  >
                    {`Add all ${toAdd.length}`}
                  </Button>
                )}
              </Stack>
              {files.map((file) => {
                const isAdded = added.has(file.file_id);
                return (
                  <Stack
                    key={file.file_id}
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
                        {file.name}
                      </Typography>
                      <Typography
                        sx={{ typography: "s3", color: "text.tertiary" }}
                      >
                        {isAdded
                          ? "Added. If it changes in the Knowledge base, you review the change."
                          : extOf(file.name)}
                      </Typography>
                    </Box>
                    <Button
                      size="small"
                      variant="outlined"
                      disabled={isAdded}
                      onClick={() =>
                        pick({ kb_id: kb.kb_id, file_ids: [file.file_id] })
                      }
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
  envId: PropTypes.string,
  open: PropTypes.bool,
  onClose: PropTypes.func.isRequired,
  // ({ kind: "knowledge_base", kb_id, file_ids }): usePrepareKbSource's selection.
  onPick: PropTypes.func.isRequired,
};
