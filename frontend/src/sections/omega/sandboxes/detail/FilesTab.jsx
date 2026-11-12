import React, { useEffect, useId, useRef, useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import Link from "@mui/material/Link";
import TableSortLabel from "@mui/material/TableSortLabel";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import LoadingButton from "@mui/lab/LoadingButton";

import { OMEGA_STATE } from "src/sections/omega/shared";

import { useFileList } from "../api/queries";
import { useSandboxesApi } from "../api/SandboxesApiContext";
import DeletedPanel from "./DeletedPanel";
import SandboxErrorState, { errorCopy } from "../components/SandboxErrorState";
import SandboxIcon from "../components/SandboxIcon";
import { LoadingRows } from "../components/SessionGate";
import StatePanel from "../components/StatePanel";
import {
  fileErrorMessage,
  joinPath as join,
  newFilePath,
  newFilePathProblem,
} from "../lib/files";
import { formatBytes } from "../lib/format";
import {
  FILE_SORTS,
  SORT_LABEL_SX,
  nextSort,
  sortFiles,
} from "../list/listView";
import FileRowMenu from "./FileRowMenu";
import FilesToolbar from "./FilesToolbar";
import FileViewer from "./FileViewer";
import UploadStatus from "./UploadStatus";
import useFileDownload from "./useFileDownload";
import useFileUploads from "./useFileUploads";

// A long folder name wraps like text, from the left, not centred in its button.
const SEGMENT_SX = { textAlign: "left", overflowWrap: "anywhere" };

function Breadcrumbs({ path, onOpen }) {
  const parts = path ? path.split("/") : [];
  return (
    <Box
      data-testid="files-breadcrumbs"
      sx={{
        display: "flex",
        flexWrap: "wrap",
        alignItems: "center",
        gap: 0.5,
        // Takes the header's free width; a path wider than that pushes the
        // toolbar onto its own line instead of being squeezed beside it.
        flex: "1 1 auto",
        minWidth: 0,
        typography: "s2",
        fontFamily: "monospace",
      }}
    >
      <Link
        component="button"
        type="button"
        onClick={() => onOpen("")}
        underline="hover"
        color={parts.length ? "text.secondary" : "text.primary"}
        sx={SEGMENT_SX}
      >
        /workspace
      </Link>
      {parts.map((part, index) => (
        <React.Fragment key={index}>
          <Box component="span" sx={{ color: "text.disabled" }}>
            /
          </Box>
          <Link
            component="button"
            type="button"
            underline="hover"
            color={
              index === parts.length - 1 ? "text.primary" : "text.secondary"
            }
            onClick={() => onOpen(parts.slice(0, index + 1).join("/"))}
            sx={SEGMENT_SX}
          >
            {part}
          </Link>
        </React.Fragment>
      ))}
    </Box>
  );
}

Breadcrumbs.propTypes = {
  path: PropTypes.string.isRequired,
  onOpen: PropTypes.func.isRequired,
};

const visuallyHidden = {
  border: 0,
  clip: "rect(0 0 0 0)",
  height: "1px",
  margin: "-1px",
  overflow: "hidden",
  padding: 0,
  position: "absolute",
  whiteSpace: "nowrap",
  width: "1px",
};

/**
 * The list's column headings, Name and Size, each a sort: a first click sorts
 * A to Z (largest first for Size), a second click reverses it. Folders stay
 * above files. Until one is chosen the list keeps the sandbox's own order.
 */
function FileColumns({ sort, onSort }) {
  const heading = (key, label) => {
    const active = sort?.key === key;
    return (
      <TableSortLabel
        active={active}
        direction={active ? sort.direction : FILE_SORTS[key].first}
        onClick={() => onSort(nextSort(sort, key, FILE_SORTS))}
        sx={SORT_LABEL_SX}
        data-testid={`files-sort-${key}`}
      >
        {label}
        {active && (
          <Box component="span" sx={visuallyHidden}>
            {sort.direction === "asc"
              ? ", sorted ascending"
              : ", sorted descending"}
          </Box>
        )}
      </TableSortLabel>
    );
  };
  return (
    <Box
      data-testid="files-columns"
      sx={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        gap: 1,
        // Lines up with the names and sizes in the rows below.
        pl: 4.5,
        pr: 4.75,
        py: 0.5,
        typography: "s2",
        fontWeight: 500,
        color: "text.secondary",
        borderBottom: "1px solid",
        borderColor: "divider",
      }}
    >
      {heading("name", "Name")}
      {heading("size", "Size")}
    </Box>
  );
}

FileColumns.propTypes = {
  sort: PropTypes.shape({ key: PropTypes.string, direction: PropTypes.string }),
  onSort: PropTypes.func.isRequired,
};

/**
 * Why a folder's names can't be checked against its list, in the same words
 * for New file and for an upload: the list is still loading, or the read
 * failed (waiting will not help).
 */
const UNCHECKED = {
  loading: "hasn't loaded yet",
  failed: "couldn't be read",
};

/**
 * A failed read that reading again won't change: refused, or a request the
 * sandbox won't take (such as a path that leads outside the workspace). Its
 * words then say why, with no "Try again".
 */
function readAgainWontHelp(error) {
  if (error?.state === OMEGA_STATE.REFUSED) return true;
  const status = Number(error?.status);
  return status >= 400 && status < 500 && status !== 408 && status !== 429;
}

/**
 * What Create found in the folder a new file's path names, or null when the
 * file can be created (also when that folder isn't there yet). A file or
 * folder of that name already there refuses the path; a list that could not
 * be read creates nothing, and Create can be tried again.
 */
function pathRefusal(folder, leaf, answer) {
  const unread = {
    retry: true,
    reason: `The list of ${folder} couldn't be read, so it can't be checked whether ${leaf} is already there. Nothing was created. Try again.`,
  };
  if (answer.error) {
    if (answer.error?.state === OMEGA_STATE.NOT_FOUND) return null;
    if (!readAgainWontHelp(answer.error)) return unread;
    return {
      retry: true,
      reason: `The list of ${folder} can't be read, so nothing was created. ${fileErrorMessage(answer.error)}`,
    };
  }
  if (!Array.isArray(answer.body?.entries)) return unread;
  const entry = answer.body.entries.find((item) => item.name === leaf);
  if (!entry) return null;
  return {
    retry: false,
    reason: entry.directory
      ? `A folder named ${leaf} already exists in ${folder}. Choose another name.`
      : `A file named ${leaf} already exists in ${folder}. Open it from that folder to edit it, or choose another name.`,
  };
}

/**
 * A name for a new file in this folder, or a path such as `src/notes.txt` for
 * one in a folder below it. A name already in this folder's list is refused,
 * because saving a new file under it would replace that file without a word,
 * and nothing is created until the list has loaded and the name can be
 * checked. A list that could not be read is named as such, with the remedy
 * the list's own error panel offers (errorCopy): Try again where reading it
 * again may help, a pointer to Reload where only a reload can, else none,
 * because the panel says why the read was refused. A path's folder is not
 * listed here, so Create reads that folder's list first and refuses the same
 * way; a folder that isn't there yet is left to the save, as before.
 */
function NewFile({
  dir,
  existing,
  listState,
  remedy,
  onRetry,
  listFolder,
  onCreate,
  onCancel,
}) {
  const [name, setName] = useState("");
  const [checking, setChecking] = useState(false);
  // Create's answer for a path, kept for the path it was given.
  const [found, setFound] = useState(null);
  // A check that answers after Cancel opens nothing.
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  const hintId = useId();
  const inputRef = useRef(null);
  const ready = listState === "ready";
  const trimmed = newFilePath(name);
  const nested = trimmed.includes("/");
  const problem = newFilePathProblem(trimmed);
  const clash = (!nested && ready && !problem && existing.get(trimmed)) || null;
  const answered = found?.path === trimmed ? found : null;
  const pathRefused = answered?.reason ?? null;
  const valid =
    !problem &&
    !checking &&
    !(answered && !answered.retry) &&
    (nested || (ready && !clash));
  let hint = "";
  if (trimmed && problem) hint = problem;
  else if (!nested && trimmed && !ready)
    hint = `This folder's list ${UNCHECKED[listState]}, so the name can't be checked.${listState === "failed" && remedy === "reload" ? " Reload the page and try again." : ""}`;
  else if (clash)
    hint = clash.directory
      ? `A folder named ${trimmed} already exists here. Choose another name.`
      : `A file named ${trimmed} already exists here. Open it from the list to edit it, or choose another name.`;
  else if (pathRefused) hint = pathRefused;
  const refused =
    Boolean(trimmed && problem) || Boolean(clash) || Boolean(pathRefused);
  const retryable =
    !nested &&
    Boolean(trimmed && !problem) &&
    listState === "failed" &&
    remedy === "retry";
  const create = async () => {
    if (!nested) {
      onCreate(join(dir, trimmed));
      return;
    }
    const cut = trimmed.lastIndexOf("/");
    const folder = trimmed.slice(0, cut);
    const leaf = trimmed.slice(cut + 1);
    setChecking(true);
    const answer = await listFolder(join(dir, folder)).then(
      (body) => ({ body }),
      (error) => ({ error }),
    );
    if (!mounted.current) return;
    setChecking(false);
    const refusal = pathRefusal(folder, leaf, answer);
    if (refusal) setFound({ path: trimmed, ...refusal });
    else onCreate(join(dir, trimmed));
  };
  return (
    <Box
      component="form"
      onSubmit={(e) => {
        e.preventDefault();
        if (valid) void create();
      }}
      sx={{ display: "flex", flexDirection: "column" }}
    >
      <Box sx={{ display: "flex", gap: 1, alignItems: "center" }}>
        <TextField
          autoFocus
          size="small"
          placeholder="notes.txt"
          value={name}
          onChange={(e) => {
            setName(e.target.value);
            // Any edit gets a fresh check on Create.
            setFound(null);
          }}
          error={refused}
          inputRef={inputRef}
          inputProps={{
            // The name can't change under a check that is still running.
            readOnly: checking,
            "aria-label": "New file name",
            "aria-describedby": hintId,
            "data-testid": "new-file-name",
          }}
          sx={{ flex: 1 }}
        />
        <Button size="small" color="inherit" onClick={onCancel}>
          Cancel
        </Button>
        <LoadingButton
          size="small"
          variant="contained"
          type="submit"
          disabled={!valid}
          loading={checking}
          data-testid="new-file-create"
        >
          Create
        </LoadingButton>
      </Box>
      {/* Always present, so a screen reader announces the words as they change. */}
      <Typography
        id={hintId}
        aria-live="polite"
        typography="s2"
        color={refused ? "error.main" : "text.secondary"}
        data-testid="new-file-hint"
        sx={{ mt: hint ? 0.75 : 0, overflowWrap: "anywhere" }}
      >
        {hint}
      </Typography>
      {/* Outside the live region, so it is not read out as part of the hint. */}
      {retryable && (
        <Button
          size="small"
          variant="outlined"
          color="inherit"
          onClick={() => {
            // The button goes away once the read starts; keep focus on the name.
            inputRef.current?.focus();
            onRetry();
          }}
          data-testid="new-file-retry"
          sx={{ alignSelf: "flex-start", mt: 0.75 }}
        >
          Try again
        </Button>
      )}
    </Box>
  );
}

NewFile.propTypes = {
  dir: PropTypes.string.isRequired,
  existing: PropTypes.instanceOf(Map).isRequired,
  // "ready" once the folder's list has loaded; until then names can't be checked.
  listState: PropTypes.oneOf(["ready", ...Object.keys(UNCHECKED)]).isRequired,
  // What the list's error panel offers for a failed read, if anything.
  remedy: PropTypes.oneOf(["retry", "reload"]),
  onRetry: PropTypes.func.isRequired,
  // Reads a folder's list (listFiles) for a path's own check.
  listFolder: PropTypes.func.isRequired,
  onCreate: PropTypes.func.isRequired,
  onCancel: PropTypes.func.isRequired,
};

/**
 * Asks before an upload replaces files already in the folder, as Delete asks
 * before removing one. The whole upload waits for the answer: Replace uploads
 * every file, Cancel uploads none. While the folder's list has not loaded, or
 * could not be read, nobody can tell whether a file would be replaced, so
 * every upload asks.
 */
function ReplaceDialog({ open, pending, onCancel, onReplace }) {
  const unchecked = Boolean(pending?.unchecked);
  const total = pending?.files.length ?? 0;
  const clashes = pending?.clashes ?? [];
  const count = unchecked ? total : clashes.length;
  const others = total - count;
  const where = pending?.dir ? `/workspace/${pending.dir}` : "/workspace";
  const files = `file${count === 1 ? "" : "s"}`;
  const which =
    count === 1 ? pending?.files[0]?.name : `these ${count} ${files}`;
  return (
    <Dialog
      open={open}
      onClose={onCancel}
      maxWidth="xs"
      data-testid="upload-replace-dialog"
    >
      <DialogTitle sx={{ typography: "m3" }}>
        {unchecked ? `Upload ${count} ${files}?` : `Replace ${count} ${files}?`}
      </DialogTitle>
      <DialogContent>
        <Typography
          typography="s1"
          color="text.secondary"
          sx={{ overflowWrap: "anywhere" }}
        >
          {unchecked
            ? `The list of ${where} ${UNCHECKED[pending.unchecked]}, so it can't be checked whether ${which} ${count === 1 ? "is" : "are"} already there. Uploading replaces any file with the same name. Snapshots already taken are not changed.`
            : `${clashes.join(", ")} already exist${count === 1 ? "s" : ""} in ${where}. Uploading replaces ${count === 1 ? "it" : "them"}. Snapshots already taken are not changed.`}
          {others > 0 ? " Nothing is uploaded if you cancel." : ""}
        </Typography>
      </DialogContent>
      <DialogActions>
        <Button
          color="inherit"
          onClick={onCancel}
          data-testid="upload-replace-cancel"
        >
          Cancel
        </Button>
        <Button
          variant="contained"
          onClick={onReplace}
          data-testid="upload-replace-confirm"
        >
          {unchecked ? "Upload" : "Replace"}
        </Button>
      </DialogActions>
    </Dialog>
  );
}

ReplaceDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  // Kept after the answer, so the closing dialog keeps its words.
  pending: PropTypes.shape({
    dir: PropTypes.string.isRequired,
    files: PropTypes.array.isRequired,
    clashes: PropTypes.arrayOf(PropTypes.string).isRequired,
    // Set when the folder's list could not check the names: why (UNCHECKED).
    unchecked: PropTypes.oneOf(Object.keys(UNCHECKED)),
  }),
  onCancel: PropTypes.func.isRequired,
  onReplace: PropTypes.func.isRequired,
};

export default function FilesTab({ sandbox, onResume, resuming }) {
  const live = sandbox.state === "active";
  const [dir, setDir] = useState("");
  const [open, setOpen] = useState(null);
  const [adding, setAdding] = useState(false);
  const [dragging, setDragging] = useState(false);
  // An upload that would replace files in the list, waiting for Replace.
  const [replacing, setReplacing] = useState(null);
  const [asking, setAsking] = useState(false);
  const [sort, setSort] = useState(null);
  const listing = useFileList(sandbox.id, dir, live);
  const api = useSandboxesApi();
  const canEdit = !sandbox.readOnly;
  // An agent run's desk is read through the v1 read route only; it has no download.
  const canDownload = !sandbox.readOnly;
  const uploads = useFileUploads(sandbox.id);
  const download = useFileDownload(sandbox.id);

  if (!live) {
    if (sandbox.state === "destroyed")
      return <DeletedPanel sandbox={sandbox} what="files" />;
    const hibernated = sandbox.state === "hibernated";
    return (
      <StatePanel
        compact
        icon="folder"
        title={
          hibernated
            ? "Files are kept while the sandbox is hibernated"
            : "This sandbox isn't running"
        }
        description={
          hibernated
            ? "Resume it to browse and edit them."
            : "Its files can be browsed while it runs."
        }
        action={
          sandbox.state === "hibernated" && onResume && !sandbox.readOnly ? (
            <LoadingButton
              size="small"
              variant="contained"
              loading={resuming}
              onClick={onResume}
            >
              Resume
            </LoadingButton>
          ) : null
        }
      />
    );
  }

  const entries = listing.data?.entries ?? [];
  // No list yet: names can't be checked while its first read is in flight
  // ("loading") or after that read failed ("failed"). A failed re-read keeps
  // the last list, which still can be.
  const listState =
    listing.data != null ? "ready" : listing.error ? "failed" : "loading";
  const ready = listState === "ready";
  // The remedy the list's error panel offers for its failure (Reload wins, as
  // there), so New file never offers a retry that panel calls useless.
  const listCopy = listing.error ? errorCopy(listing.error) : null;
  const remedy = listCopy?.reload ? "reload" : listCopy?.retry ? "retry" : null;
  const byName = new Map(entries.map((entry) => [entry.name, entry]));
  // Each upload replaces a same-named file, so one that would asks first. A
  // folder of that name is not replaced (the sandbox refuses that upload).
  const requestUpload = (fileList) => {
    const files = Array.from(fileList || []);
    if (files.length === 0) return;
    if (!ready) {
      setReplacing({ dir, files, clashes: [], unchecked: listState });
      setAsking(true);
      return;
    }
    const clashes = files
      .filter((file) => {
        const entry = byName.get(file.name);
        return Boolean(entry) && !entry.directory;
      })
      .map((file) => file.name);
    if (clashes.length > 0) {
      setReplacing({ dir, files, clashes });
      setAsking(true);
    } else void uploads.upload(dir, files);
  };
  const openDir = (path) => {
    setDir(path);
    setOpen(null);
    setAdding(false);
  };
  // Keep the open file in step with a rename or delete made from the list.
  const onChanged = (change) => {
    if (!open) return;
    if (change.kind === "moved" && open.path === change.from)
      setOpen({ ...open, path: change.to });
    if (
      change.kind === "deleted" &&
      (open.path === change.path || open.path.startsWith(`${change.path}/`))
    )
      setOpen(null);
  };
  const dropProps = canEdit
    ? {
        onDragOver: (event) => {
          if (!event.dataTransfer?.types?.includes("Files")) return;
          event.preventDefault();
          setDragging(true);
        },
        onDragLeave: () => setDragging(false),
        onDrop: (event) => {
          if (!event.dataTransfer?.files?.length) return;
          event.preventDefault();
          setDragging(false);
          requestUpload(event.dataTransfer.files);
        },
      }
    : {};

  return (
    <Box
      sx={{
        display: "grid",
        gridTemplateColumns: { xs: "1fr", md: "minmax(240px, 2fr) 3fr" },
        gap: 2,
        alignItems: "start",
      }}
    >
      <Box
        sx={{
          border: "1px solid",
          borderColor: dragging ? "primary.main" : "divider",
          outline: dragging ? "1px dashed" : "none",
          outlineColor: "primary.main",
          borderRadius: 1,
          minWidth: 0,
        }}
        data-testid="files-list"
        {...dropProps}
      >
        <Box
          sx={{
            px: 1.5,
            py: 1,
            display: "flex",
            flexWrap: "wrap",
            justifyContent: "space-between",
            alignItems: "center",
            gap: 1,
            borderBottom: "1px solid",
            borderColor: "divider",
          }}
        >
          <Breadcrumbs path={dir} onOpen={openDir} />
          {canEdit && !adding && (
            <FilesToolbar
              sandboxId={sandbox.id}
              dir={dir}
              uploads={uploads}
              onUpload={requestUpload}
              onNewFile={() => setAdding(true)}
            />
          )}
        </Box>
        <UploadStatus uploads={uploads} />
        {adding && (
          <Box
            sx={{ p: 1.5, borderBottom: "1px solid", borderColor: "divider" }}
          >
            <NewFile
              dir={dir}
              existing={byName}
              listState={listState}
              remedy={remedy}
              onRetry={() => listing.refetch()}
              listFolder={(path) => api.listFiles(sandbox.id, path)}
              onCancel={() => setAdding(false)}
              onCreate={(path) => {
                setAdding(false);
                setOpen({ path, isNew: true });
              }}
            />
          </Box>
        )}
        {listing.isLoading && (
          <Box sx={{ p: 1.5 }}>
            <LoadingRows rows={3} />
          </Box>
        )}
        {listing.error && (
          <SandboxErrorState
            compact
            error={listing.error}
            onRetry={() => listing.refetch()}
          />
        )}
        {!listing.isLoading && !listing.error && entries.length === 0 && (
          <Typography typography="s1" color="text.secondary" sx={{ p: 2 }}>
            {dir
              ? "This folder is empty."
              : canEdit
                ? "No files yet. Upload or create one here, or make one from the Run tab."
                : "No files yet."}
          </Typography>
        )}
        {entries.length > 0 && <FileColumns sort={sort} onSort={setSort} />}
        <Box component="ul" sx={{ listStyle: "none", m: 0, p: 0.5 }}>
          {sortFiles(entries, sort).map((entry) => {
            const path = join(dir, entry.name);
            const selected = open?.path === path;
            return (
              <Box
                component="li"
                key={entry.name}
                sx={{ display: "flex", alignItems: "center", gap: 0.25 }}
              >
                <Box
                  component="button"
                  type="button"
                  data-testid="file-entry"
                  data-name={entry.name}
                  onClick={() =>
                    entry.directory
                      ? openDir(path)
                      : setOpen({ path, isNew: false, size: entry.size })
                  }
                  sx={{
                    all: "unset",
                    boxSizing: "border-box",
                    flex: 1,
                    minWidth: 0,
                    cursor: "pointer",
                    display: "flex",
                    alignItems: "center",
                    gap: 1,
                    px: 1,
                    py: 0.75,
                    borderRadius: 0.75,
                    bgcolor: selected ? "action.selected" : "transparent",
                    "&:hover, &:focus-visible": { bgcolor: "action.hover" },
                    "&:focus-visible": {
                      outline: (theme) =>
                        `2px solid ${theme.palette.border.focus}`,
                      outlineOffset: -2,
                    },
                  }}
                >
                  <SandboxIcon
                    name={entry.directory ? "folder" : "file"}
                    size={16}
                    sx={{ color: "text.secondary" }}
                  />
                  <Typography
                    typography="s1"
                    sx={{
                      flex: 1,
                      minWidth: 0,
                      fontFamily: "monospace",
                      fontSize: 13,
                    }}
                    noWrap
                  >
                    {entry.name}
                  </Typography>
                  <Typography
                    typography="s2"
                    color="text.secondary"
                    sx={{ flexShrink: 0 }}
                  >
                    {entry.directory ? "" : formatBytes(entry.size)}
                  </Typography>
                </Box>
                {(canEdit || (canDownload && !entry.directory)) && (
                  <FileRowMenu
                    sandboxId={sandbox.id}
                    entry={entry}
                    path={path}
                    canEdit={canEdit}
                    onDownload={download.download}
                    onChanged={onChanged}
                  />
                )}
              </Box>
            );
          })}
        </Box>
      </Box>
      <Box
        sx={{
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 1,
          p: 2,
          minWidth: 0,
        }}
      >
        {open ? (
          <FileViewer
            sandboxId={sandbox.id}
            path={open.path}
            isNew={open.isNew}
            canEdit={canEdit}
            canDownload={canDownload}
            size={open.size}
            download={download}
            onSaved={() => setOpen({ path: open.path, isNew: false })}
          />
        ) : (
          <Typography typography="s1" color="text.secondary">
            Choose a file to read
            {canEdit
              ? ", edit or download it. Upload files with Upload or by dropping them on the list. Changes made here are recorded in Activity."
              : canDownload
                ? " or download it."
                : " it."}
          </Typography>
        )}
      </Box>
      {/* Outside the list, so a drop on the open dialog is not a second upload. */}
      <ReplaceDialog
        open={asking}
        pending={replacing}
        onCancel={() => setAsking(false)}
        onReplace={() => {
          setAsking(false);
          void uploads.upload(replacing.dir, replacing.files);
        }}
      />
    </Box>
  );
}

FilesTab.propTypes = {
  sandbox: PropTypes.object.isRequired,
  onResume: PropTypes.func,
  resuming: PropTypes.bool,
};
