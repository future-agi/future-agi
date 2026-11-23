import PropTypes from "prop-types";
import { useEffect, useRef, useState } from "react";
import {
  Box,
  Button,
  ButtonBase,
  Checkbox,
  Fade,
  IconButton,
  InputBase,
  Link,
  Stack,
  Tooltip,
  Typography,
  useMediaQuery,
} from "@mui/material";
import { ClosableDialog } from "src/components/custom-dialog";
import ContentSkeleton from "src/components/content-skeleton";
import ErrorState from "src/components/error-state";
import Iconify from "src/components/iconify";
import StatusBadge from "src/components/status-badge";
import { useDebounce } from "src/hooks/use-debounce";
import { paths } from "src/routes/paths";
import {
  isKbFileReady,
  isKbReady,
  useEnvironmentSources,
  useKnowledgeBaseFiles,
  useKnowledgeBases,
  useSourceCapabilities,
} from "src/api/simulate-environments/sources";
import { fDate } from "src/utils/format-time";
import ShimmerText from "../../components/ShimmerText";
import { plural } from "./sources.constants";

const MB = 1024 * 1024;
const extOf = (name) => name.split(".").pop().toUpperCase();
// One height for both steps, searching and loading: only the list scrolls.
const DIALOG_HEIGHT = 600;
// The next page loads once the list is scrolled this close to its end.
const NEAR_END = 120;

// The Knowledge base page's StatusBadge mapping, with PartialCompleted ready.
const BADGE = {
  Processing: { status: "running", label: "Processing" },
  Failed: { status: "failed", label: "Couldn't be processed" },
};
const badgeOf = (status) =>
  BADGE[status] || { status: "queued", label: status };

const rowSx = {
  py: 1,
  // Not `& + &`: rows in different states get different classes.
  "&:not(:first-of-type)": { borderTop: "1px solid", borderColor: "divider" },
};

function Note({ children }) {
  return (
    <Typography sx={{ typography: "s1", color: "text.tertiary", py: 1 }}>
      {children}
    </Typography>
  );
}

Note.propTypes = { children: PropTypes.node };

function CreateLink() {
  return (
    <Link
      href={paths.dashboard.knowledge_base}
      target="_blank"
      rel="noopener noreferrer"
      underline="hover"
      sx={{ typography: "s1", fontWeight: 500 }}
    >
      Create a Knowledge base
    </Link>
  );
}

// The loaded rows, then a shimmer while the next page loads.
function PagedList({ query, label, empty, children }) {
  if (query.isPending) return <ContentSkeleton rows={5} label={label} />;
  if (query.isError && !query.isFetchNextPageError)
    return (
      <ErrorState
        title={`Couldn't load the ${label}`}
        onRetry={() => query.refetch()}
      />
    );
  return (
    <>
      {query.data.length ? children : empty}
      {query.isFetchingNextPage && (
        <ShimmerText sx={{ py: 1 }}>{`Loading more ${label}…`}</ShimmerText>
      )}
      {query.isFetchNextPageError && (
        <Stack direction="row" alignItems="center" spacing={1} sx={{ py: 1 }}>
          <Typography sx={{ typography: "s1", color: "text.secondary" }}>
            {`Couldn't load more ${label}.`}
          </Typography>
          <Button size="small" onClick={() => query.fetchNextPage()}>
            Try again
          </Button>
        </Stack>
      )}
    </>
  );
}

PagedList.propTypes = {
  query: PropTypes.object.isRequired,
  // What's loading, as it reads mid-sentence: "Knowledge bases", "files".
  label: PropTypes.string.isRequired,
  empty: PropTypes.node,
  children: PropTypes.node,
};

function KnowledgeBaseList({ query, search, onOpen }) {
  return (
    <PagedList
      query={query}
      label="Knowledge bases"
      empty={
        search ? (
          <Note>{`No Knowledge bases match "${search}".`}</Note>
        ) : (
          <Note>
            No Knowledge bases yet. <CreateLink />, then add its files here.
          </Note>
        )
      }
    >
      {query.data?.map((kb) => {
        const ready = isKbReady(kb);
        return (
          <ButtonBase
            key={kb.id}
            disabled={!ready}
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
            <Box sx={{ flex: 1, minWidth: 0, opacity: ready ? 1 : 0.6 }}>
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
            {ready ? (
              <Iconify
                icon="solar:alt-arrow-right-linear"
                width={16}
                sx={{ color: "text.tertiary" }}
              />
            ) : (
              <StatusBadge size="sm" {...badgeOf(kb.status)} />
            )}
          </ButtonBase>
        );
      })}
    </PagedList>
  );
}

KnowledgeBaseList.propTypes = {
  query: PropTypes.object.isRequired,
  search: PropTypes.string,
  onOpen: PropTypes.func.isRequired,
};

function KnowledgeBaseFiles({ query, search, added, picked, onToggle }) {
  return (
    <PagedList
      query={query}
      label="files"
      empty={
        <Note>
          {search
            ? `No files match "${search}".`
            : "This Knowledge base has no files yet."}
        </Note>
      }
    >
      {query.data?.map((file) => {
        const isAdded = added.has(file.id);
        const ready = isKbFileReady(file);
        const locked = isAdded || !ready;
        return (
          <Stack
            key={file.id}
            component="label"
            direction="row"
            alignItems="center"
            spacing={1}
            sx={{ ...rowSx, cursor: locked ? "default" : "pointer" }}
          >
            <Checkbox
              size="small"
              checked={isAdded || picked.some((one) => one.id === file.id)}
              disabled={locked}
              onChange={() => onToggle(file)}
              sx={{ opacity: ready ? 1 : 0.4 }}
            />
            <Box sx={{ flex: 1, minWidth: 0, opacity: ready ? 1 : 0.6 }}>
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
            {!isAdded && !ready && (
              <StatusBadge size="sm" {...badgeOf(file.status)} />
            )}
          </Stack>
        );
      })}
    </PagedList>
  );
}

KnowledgeBaseFiles.propTypes = {
  query: PropTypes.object.isRequired,
  search: PropTypes.string,
  added: PropTypes.instanceOf(Set).isRequired,
  picked: PropTypes.array.isRequired,
  onToggle: PropTypes.func.isRequired,
};

/**
 * Add from the Knowledge base in two steps: pick a Knowledge base, then tick
 * its ready files and Add selected, or Add all. A file is copied as it is
 * now, not linked live. Both lists are real, searched and paged by the API;
 * `onPick` gets the prepare selection.
 */
export default function KnowledgeBasePicker({ envId, open, onClose, onPick }) {
  const reduce = useMediaQuery("(prefers-reduced-motion: reduce)");
  const { data: summary } = useEnvironmentSources(envId);
  const { data: capabilities } = useSourceCapabilities(envId);
  const [kb, setKb] = useState(null);
  const [picked, setPicked] = useState([]);
  const [search, setSearch] = useState("");
  const debounced = useDebounce(search.trim(), 300);
  // Clearing the box shows the whole list at once, not after the debounce.
  const term = search.trim() ? debounced : "";
  const kbs = useKnowledgeBases({ enabled: open && !kb, search: term });
  const files = useKnowledgeBaseFiles(open ? kb?.id : null, { search: term });
  const query = kb ? files : kbs;
  const listRef = useRef(null);
  const limits = capabilities?.knowledge_base;
  // Removed ones too: adding one again would find it already here.
  const added = new Set(
    (summary?.sources || [])
      .filter((source) => source.origin?.kind === "knowledge_base")
      .map((source) => source.origin.file_id),
  );
  // Add all reads every page, so it is off only once the whole unsearched
  // list is loaded and nothing in it can be added.
  const nothingToAdd =
    !term &&
    files.isSuccess &&
    !files.hasNextPage &&
    !files.data.some((file) => isKbFileReady(file) && !added.has(file.id));

  // Also after each page: a page whose rows were all hidden leaves the list
  // short, so the next one loads until the list fills or the total is reached.
  const loadMore = () => {
    const el = listRef.current;
    if (!el || !query.hasNextPage || query.isFetching || query.isError) return;
    if (el.scrollHeight - el.scrollTop - el.clientHeight < NEAR_END)
      query.fetchNextPage();
  };
  useEffect(loadMore);

  const openKb = (next) => {
    setKb(next);
    setPicked([]);
    setSearch("");
  };
  const toggle = (file) =>
    setPicked((was) =>
      was.some((one) => one.id === file.id)
        ? was.filter((one) => one.id !== file.id)
        : [...was, file],
    );
  const pick = (selection) =>
    onPick({ kind: "knowledge_base", kb, ...selection });

  return (
    <ClosableDialog
      open={open}
      onClose={onClose}
      fullWidth
      maxWidth={false}
      PaperProps={{ sx: { maxWidth: 640, height: DIALOG_HEIGHT, p: 2.5 } }}
      // Back to the list once the close has played, not during it.
      TransitionProps={{ onExited: () => openKb(null) }}
    >
      <Fade key={kb?.id || "list"} in timeout={reduce ? 0 : undefined}>
        <Stack sx={{ height: 1, minHeight: 0 }}>
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
            <Typography
              noWrap
              sx={{ typography: "m2", fontWeight: 600, flex: 1, minWidth: 0 }}
            >
              {kb ? kb.name : "Add from the Knowledge base"}
            </Typography>
            <Tooltip title="Refresh">
              <IconButton
                size="small"
                aria-label={kb ? "Refresh files" : "Refresh Knowledge bases"}
                onClick={() => query.refetch()}
              >
                <Iconify icon="solar:refresh-linear" width={16} />
              </IconButton>
            </Tooltip>
          </Stack>
          <Typography
            noWrap
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

          <Stack
            direction="row"
            alignItems="center"
            spacing={1}
            sx={{
              mt: 1.5,
              px: 1.25,
              height: 32,
              border: "1px solid",
              borderColor: "divider",
              borderRadius: "6px",
            }}
          >
            <Iconify
              icon="solar:magnifer-linear"
              width={14}
              sx={{ color: "text.tertiary" }}
            />
            <InputBase
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder={kb ? "Search files" : "Search Knowledge bases"}
              inputProps={{
                "aria-label": kb ? "Search files" : "Search Knowledge bases",
              }}
              sx={{ typography: "s2", flex: 1 }}
            />
          </Stack>

          <Box
            ref={listRef}
            onScroll={loadMore}
            sx={{ flex: 1, minHeight: 0, overflowY: "auto", mt: 1 }}
          >
            {kb ? (
              <KnowledgeBaseFiles
                query={files}
                search={term}
                added={added}
                picked={picked}
                onToggle={toggle}
              />
            ) : (
              <KnowledgeBaseList query={kbs} search={term} onOpen={openKb} />
            )}
          </Box>

          <Stack
            direction="row"
            alignItems="center"
            spacing={1}
            sx={{
              pt: 1.5,
              minHeight: 44,
              borderTop: "1px solid",
              borderColor: "divider",
            }}
          >
            <Box sx={{ flex: 1 }}>
              <CreateLink />
            </Box>
            {kb && (
              <>
                <Button
                  size="small"
                  variant="outlined"
                  disabled={nothingToAdd}
                  onClick={() => pick({ whole_collection: true })}
                >
                  Add all
                </Button>
                <Button
                  size="small"
                  variant="contained"
                  color="primary"
                  disabled={!picked.length}
                  onClick={() => pick({ files: picked })}
                >
                  {`Add selected (${picked.length})`}
                </Button>
              </>
            )}
          </Stack>
        </Stack>
      </Fade>
    </ClosableDialog>
  );
}

KnowledgeBasePicker.propTypes = {
  envId: PropTypes.string,
  open: PropTypes.bool,
  onClose: PropTypes.func.isRequired,
  // ({ kind: "knowledge_base", kb, files | whole_collection }):
  // usePrepareKbSource's selection.
  onPick: PropTypes.func.isRequired,
};
