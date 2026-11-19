import PropTypes from "prop-types";
import { useState } from "react";
import {
  Box,
  Button,
  ButtonBase,
  Checkbox,
  Fade,
  IconButton,
  Stack,
  Typography,
  useMediaQuery,
} from "@mui/material";
import { ClosableDialog } from "src/components/custom-dialog";
import ContentSkeleton from "src/components/content-skeleton";
import ErrorState from "src/components/error-state";
import Iconify from "src/components/iconify";
import {
  useEnvironmentSources,
  useKnowledgeBaseFiles,
  useKnowledgeBases,
  useSourceCapabilities,
} from "src/api/simulate-environments/sources";
import { fDate } from "src/utils/format-time";
import { plural } from "./sources.constants";

const MB = 1024 * 1024;
const extOf = (name) => name.split(".").pop().toUpperCase();
// Both steps keep one height, so the dialog doesn't jump between them.
const BODY_HEIGHT = 380;

const rowSx = {
  py: 1,
  "& + &": { borderTop: "1px solid", borderColor: "divider" },
};

function Loading({ query, label, children }) {
  if (query.isPending) return <ContentSkeleton rows={5} label={label} />;
  if (query.isError)
    return (
      <ErrorState
        title={`Couldn't load the ${label}`}
        onRetry={() => query.refetch()}
      />
    );
  return children;
}

Loading.propTypes = {
  query: PropTypes.object.isRequired,
  // What's loading, as it reads mid-sentence: "Knowledge bases", "files".
  label: PropTypes.string.isRequired,
  children: PropTypes.node,
};

function KnowledgeBaseList({ query, onOpen }) {
  return (
    <Loading query={query} label="Knowledge bases">
      {query.data?.length ? (
        query.data.map((kb) => (
          <ButtonBase
            key={kb.kb_id}
            onClick={() => onOpen(kb)}
            sx={{
              ...rowSx,
              width: 1,
              px: 1,
              gap: 1.5,
              justifyContent: "flex-start",
              textAlign: "left",
              borderRadius: "4px",
              "&:hover": { bgcolor: "action.hover" },
            }}
          >
            <Box sx={{ flex: 1, minWidth: 0 }}>
              <Typography noWrap sx={{ typography: "s2", fontWeight: 600 }}>
                {kb.name}
              </Typography>
              <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                {[
                  plural(kb.file_count, "file"),
                  kb.updated_at && `Updated ${fDate(kb.updated_at)}`,
                ]
                  .filter(Boolean)
                  .join(" · ")}
              </Typography>
            </Box>
            <Iconify
              icon="solar:alt-arrow-right-linear"
              width={16}
              sx={{ color: "text.tertiary" }}
            />
          </ButtonBase>
        ))
      ) : (
        <Typography sx={{ typography: "s1", color: "text.tertiary", py: 1 }}>
          No Knowledge bases yet. Create one in Knowledge base, then add its
          files here.
        </Typography>
      )}
    </Loading>
  );
}

KnowledgeBaseList.propTypes = {
  query: PropTypes.object.isRequired,
  onOpen: PropTypes.func.isRequired,
};

function KnowledgeBaseFiles({ query, added, picked, onToggle }) {
  return (
    <Loading query={query} label="files">
      {query.data?.length ? (
        query.data.map((file) => {
          const isAdded = added.has(file.file_id);
          return (
            <Stack
              key={file.file_id}
              component="label"
              direction="row"
              alignItems="center"
              spacing={1}
              sx={{ ...rowSx, cursor: isAdded ? "default" : "pointer" }}
            >
              <Checkbox
                size="small"
                checked={isAdded || picked.includes(file.file_id)}
                disabled={isAdded}
                onChange={() => onToggle(file.file_id)}
              />
              <Box sx={{ flex: 1, minWidth: 0 }}>
                <Typography noWrap sx={{ typography: "s2", fontWeight: 600 }}>
                  {file.name}
                </Typography>
                <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                  {isAdded
                    ? "Added. If it changes in the Knowledge base, you review the change."
                    : extOf(file.name)}
                </Typography>
              </Box>
              {isAdded && (
                <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                  Added
                </Typography>
              )}
            </Stack>
          );
        })
      ) : (
        <Typography sx={{ typography: "s1", color: "text.tertiary", py: 1 }}>
          This Knowledge base has no files yet.
        </Typography>
      )}
    </Loading>
  );
}

KnowledgeBaseFiles.propTypes = {
  query: PropTypes.object.isRequired,
  added: PropTypes.instanceOf(Set).isRequired,
  picked: PropTypes.array.isRequired,
  onToggle: PropTypes.func.isRequired,
};

/**
 * Add from the Knowledge base in two steps: pick a Knowledge base, then tick
 * its files and Add selected, or Add all. A file is copied as it is now, not
 * linked live. The list and files are real; `onPick` gets the prepare
 * selection.
 */
export default function KnowledgeBasePicker({ envId, open, onClose, onPick }) {
  const reduce = useMediaQuery("(prefers-reduced-motion: reduce)");
  const { data: summary } = useEnvironmentSources(envId);
  const { data: capabilities } = useSourceCapabilities(envId);
  const [kb, setKb] = useState(null);
  const [picked, setPicked] = useState([]);
  const kbs = useKnowledgeBases({ enabled: open });
  const files = useKnowledgeBaseFiles(open ? kb?.kb_id : null);
  const limits = capabilities?.knowledge_base;
  // Removed ones too: adding one again would find it already here.
  const added = new Set(
    (summary?.sources || [])
      .filter((source) => source.origin?.kind === "knowledge_base")
      .map((source) => source.origin.file_id),
  );
  const addable = (files.data || []).filter((file) => !added.has(file.file_id));
  const allPicked = addable.length > 0 && picked.length === addable.length;

  const openKb = (next) => {
    setKb(next);
    setPicked([]);
  };
  const toggle = (fileId) =>
    setPicked((was) =>
      was.includes(fileId)
        ? was.filter((one) => one !== fileId)
        : [...was, fileId],
    );
  const pick = (selection) =>
    onPick({ kind: "knowledge_base", kb_id: kb.kb_id, ...selection });

  return (
    <ClosableDialog
      open={open}
      onClose={onClose}
      fullWidth
      maxWidth={false}
      PaperProps={{ sx: { maxWidth: 640, p: 2.5 } }}
      // Back to the list once the close has played, not during it.
      TransitionProps={{ onExited: () => openKb(null) }}
    >
      <Fade key={kb?.kb_id || "list"} in timeout={reduce ? 0 : undefined}>
        <Box>
          <Stack direction="row" alignItems="center" spacing={1} sx={{ pr: 4 }}>
            {kb && (
              <IconButton
                size="small"
                aria-label="Back to Knowledge bases"
                onClick={() => openKb(null)}
                sx={{ ml: -0.75 }}
              >
                <Iconify icon="solar:alt-arrow-left-linear" width={18} />
              </IconButton>
            )}
            <Typography noWrap sx={{ typography: "m2", fontWeight: 600 }}>
              {kb ? kb.name : "Add from the Knowledge base"}
            </Typography>
          </Stack>
          <Typography
            sx={{ typography: "s3", color: "text.tertiary", mt: 0.5 }}
          >
            {[
              kb
                ? plural(kb.file_count, "file")
                : "Copy files into this environment",
              limits &&
                `${limits.formats.map((ext) => ext.toUpperCase()).join(", ")} up to ${Math.round(limits.max_file_bytes / MB)} MB`,
            ]
              .filter(Boolean)
              .join(" · ")}
          </Typography>

          <Stack sx={{ mt: 1.5, height: BODY_HEIGHT }}>
            {kb && addable.length > 0 && (
              <Stack
                component="label"
                direction="row"
                alignItems="center"
                spacing={1}
                sx={{
                  py: 0.5,
                  borderBottom: "1px solid",
                  borderColor: "divider",
                  cursor: "pointer",
                }}
              >
                <Checkbox
                  size="small"
                  checked={allPicked}
                  indeterminate={picked.length > 0 && !allPicked}
                  onChange={() =>
                    setPicked(
                      allPicked ? [] : addable.map((file) => file.file_id),
                    )
                  }
                />
                <Typography sx={{ typography: "s1", fontWeight: 500 }}>
                  Select all
                </Typography>
              </Stack>
            )}
            <Box sx={{ flex: 1, minHeight: 0, overflowY: "auto" }}>
              {kb ? (
                <KnowledgeBaseFiles
                  query={files}
                  added={added}
                  picked={picked}
                  onToggle={toggle}
                />
              ) : (
                <KnowledgeBaseList query={kbs} onOpen={openKb} />
              )}
            </Box>
            {kb && (
              <Stack
                direction="row"
                justifyContent="flex-end"
                spacing={1}
                sx={{ pt: 1.5, borderTop: "1px solid", borderColor: "divider" }}
              >
                <Button
                  size="small"
                  variant="outlined"
                  disabled={!addable.length}
                  onClick={() => pick({ whole_collection: true })}
                >
                  Add all
                </Button>
                <Button
                  size="small"
                  variant="contained"
                  color="primary"
                  disabled={!picked.length}
                  onClick={() => pick({ file_ids: picked })}
                >
                  {`Add selected (${picked.length})`}
                </Button>
              </Stack>
            )}
          </Stack>
        </Box>
      </Fade>
    </ClosableDialog>
  );
}

KnowledgeBasePicker.propTypes = {
  envId: PropTypes.string,
  open: PropTypes.bool,
  onClose: PropTypes.func.isRequired,
  // ({ kind: "knowledge_base", kb_id, file_ids | whole_collection }):
  // usePrepareKbSource's selection.
  onPick: PropTypes.func.isRequired,
};
