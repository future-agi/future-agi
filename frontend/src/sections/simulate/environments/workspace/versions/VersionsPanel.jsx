import PropTypes from "prop-types";
import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  Box,
  Button,
  CircularProgress,
  List,
  ListItem,
  ListItemButton,
  MenuItem,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  TextField,
  Typography,
} from "@mui/material";
import {
  VERSION_PIECES,
  canMakeDefault,
  isVersioningDisabled,
  useEnvironmentVersions,
  useMoveDefault,
  useSuiteMembers,
  useVersionDiff,
} from "src/api/simulate-environments/versions";
import { VERSIONS_COPY } from "../workspace.constants";
import { refusalText } from "../evals/refusalText";
import { PreflightReport } from "../agents/PreflightStatus";
import { named } from "../agents/agentVersionStatus";
import MakeDefaultConfirm from "./MakeDefaultConfirm";
import VersionDiff from "./VersionDiff";
import VersionHeader from "./VersionHeader";
import { isEmptyDiff } from "./isEmptyDiff";

const MONO = "ui-monospace, Menlo, monospace";

function VersionRow({ version, selected, onSelect }) {
  return (
    <ListItem disablePadding>
      <ListItemButton
        selected={selected}
        onClick={onSelect}
        aria-current={selected ? "true" : undefined}
        sx={{
          borderRadius: 1,
          alignItems: "flex-start",
          display: "block",
          py: 1,
        }}
      >
        <VersionHeader version={version} />
      </ListItemButton>
    </ListItem>
  );
}
VersionRow.propTypes = {
  version: PropTypes.object.isRequired,
  selected: PropTypes.bool,
  onSelect: PropTypes.func.isRequired,
};

function VersionColumn({ piece, versions, selectedId, onSelect }) {
  const headingId = `versions-${piece}-heading`;
  return (
    <Box
      data-testid={`versions-${piece}`}
      sx={{
        flex: 1,
        minWidth: 0,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1.5,
        p: 1,
      }}
    >
      <Stack
        direction="row"
        alignItems="baseline"
        spacing={1}
        sx={{ px: 1, pb: 0.5 }}
      >
        <Typography
          id={headingId}
          sx={{ typography: "s1", fontWeight: "fontWeightBold" }}
        >
          {VERSIONS_COPY.pieces[piece]}
        </Typography>
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
          {versions.length}
        </Typography>
      </Stack>
      {versions.length ? (
        <List dense disablePadding aria-labelledby={headingId}>
          {versions.map((version) => (
            <VersionRow
              key={version.id}
              version={version}
              selected={version.id === selectedId}
              onSelect={() => onSelect(piece, version.id)}
            />
          ))}
        </List>
      ) : (
        <Typography
          sx={{ typography: "s3", color: "text.subtitle", px: 1, py: 1 }}
        >
          {VERSIONS_COPY.noneInPiece}
        </Typography>
      )}
    </Box>
  );
}
VersionColumn.propTypes = {
  piece: PropTypes.oneOf(VERSION_PIECES).isRequired,
  versions: PropTypes.arrayOf(PropTypes.object).isRequired,
  selectedId: PropTypes.string,
  onSelect: PropTypes.func.isRequired,
};

function SuiteMembers({ envId, version }) {
  const { data, isLoading } = useSuiteMembers(envId, version.id);
  if (isLoading) return <CircularProgress size={18} />;
  // A failed refresh keeps the members it already has.
  if (!data) {
    return (
      <Typography sx={{ typography: "s2", color: "error.main" }}>
        {VERSIONS_COPY.detail.membersError}
      </Typography>
    );
  }
  const members = data;
  const { members: COLS } = VERSIONS_COPY;
  return (
    <Stack spacing={1}>
      <Typography sx={{ typography: "s2", fontWeight: "fontWeightBold" }}>
        {VERSIONS_COPY.detail.scenarios(named(version), members.length)}
      </Typography>
      <Box sx={{ overflowX: "auto" }}>
        <Table
          size="small"
          aria-label={VERSIONS_COPY.detail.scenarios(
            named(version),
            members.length,
          )}
        >
          <TableHead>
            <TableRow>
              <TableCell>{COLS.key}</TableCell>
              <TableCell>{COLS.name}</TableCell>
              <TableCell>{COLS.useCase}</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {members.map((member) => (
              <TableRow key={member.scenario_key}>
                <TableCell sx={{ fontFamily: MONO }}>
                  {member.scenario_key}
                </TableCell>
                <TableCell>{member.name || "—"}</TableCell>
                <TableCell>{member.use_case || "—"}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Box>
    </Stack>
  );
}
SuiteMembers.propTypes = {
  envId: PropTypes.string.isRequired,
  version: PropTypes.object.isRequired,
};

// Siblings come newest first, so the oldest version of a piece is the last one.
// A version with a parent is never first: its parent may simply be deleted, and
// its stored diff still says what changed. Agent versions from before
// versioning carry no parent, so for them position alone decides.
const isFirst = (version, siblings) =>
  !version.parentId &&
  siblings.length > 0 &&
  siblings[siblings.length - 1].id === version.id;

function MakeDefault({ move, piece, version, defaultId }) {
  const [confirming, setConfirming] = useState(false);
  const { makeDefault } = VERSIONS_COPY;
  const confirm = () =>
    move.mutate(
      { piece, versionId: version.id, expectedDefaultId: defaultId },
      { onSettled: () => setConfirming(false) },
    );
  return (
    <>
      <Button
        size="small"
        variant="outlined"
        color="inherit"
        disabled={move.isPending}
        onClick={() => setConfirming(true)}
        sx={{ textTransform: "none", flexShrink: 0 }}
      >
        {makeDefault.action}
      </Button>
      <MakeDefaultConfirm
        piece={piece}
        version={confirming ? version : null}
        pending={move.isPending}
        onConfirm={confirm}
        onClose={() => setConfirming(false)}
      />
    </>
  );
}
MakeDefault.propTypes = {
  move: PropTypes.object.isRequired,
  piece: PropTypes.oneOf(VERSION_PIECES).isRequired,
  version: PropTypes.object.isRequired,
  defaultId: PropTypes.string,
};

function VersionDetail({ envId, move, piece, version, siblings, defaultId }) {
  const [picked, setAgainst] = useState("");
  const { detail } = VERSIONS_COPY;
  const others = siblings.filter((one) => one.id !== version.id);
  // A compared version that a refresh removed falls back to the previous one.
  const against = others.some((one) => one.id === picked) ? picked : "";
  const first = !against && isFirst(version, siblings);
  const {
    data,
    isLoading,
    isError: refreshFailed,
  } = useVersionDiff(envId, piece, version.id, against || undefined, {
    enabled: !first,
  });
  // A failed refresh keeps the diff it already has.
  const isError = refreshFailed && !data;
  const noRecord = !first && !isLoading && !isError && data?.diff == null;
  const empty =
    !first &&
    !noRecord &&
    !isLoading &&
    !isError &&
    isEmptyDiff(piece, data?.diff);

  const note = (text) => (
    <Typography sx={{ typography: "s2", color: "text.secondary" }}>
      {text}
    </Typography>
  );

  const body = () => {
    if (first) return note(detail.first);
    if (noRecord && against) return note(detail.notComparable);
    if (isLoading) return <CircularProgress size={18} />;
    if (isError) {
      return (
        <Typography sx={{ typography: "s2", color: "error.main" }}>
          {detail.loadError}
        </Typography>
      );
    }
    if (noRecord) return note(detail.noRecord);
    if (empty) return note(detail.noChange);
    return <VersionDiff piece={piece} diff={data.diff} />;
  };

  return (
    <Stack
      spacing={2}
      data-testid="version-detail"
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1.5,
        p: 2,
      }}
    >
      <Stack
        direction={{ xs: "column", sm: "row" }}
        spacing={2}
        alignItems={{ sm: "center" }}
        flexWrap="wrap"
        useFlexGap
      >
        <Typography
          sx={{ typography: "s1", fontWeight: "fontWeightBold", flex: 1 }}
        >
          {detail.changes(`${VERSIONS_COPY.pieces[piece]} ${named(version)}`)}
        </Typography>
        {others.length > 0 && (
          <TextField
            select
            size="small"
            label={detail.compareWith}
            value={against}
            onChange={(e) => setAgainst(e.target.value)}
            sx={{ minWidth: 200 }}
            // The empty value is "its previous version"; show it, not a blank box.
            SelectProps={{ displayEmpty: true }}
            InputLabelProps={{ shrink: true }}
            inputProps={{ "data-testid": "compare-with" }}
          >
            <MenuItem value="">{detail.previous}</MenuItem>
            {others.map((one) => (
              <MenuItem key={one.id} value={one.id}>
                {one.label || VERSIONS_COPY.unlabelled}
              </MenuItem>
            ))}
          </TextField>
        )}
        {canMakeDefault(piece, version) && (
          <MakeDefault
            move={move}
            piece={piece}
            version={version}
            defaultId={defaultId}
          />
        )}
      </Stack>
      {move.isError && (
        <Typography role="alert" sx={{ typography: "s2", color: "error.main" }}>
          {refusalText(move.error, VERSIONS_COPY.makeDefault.error)}
        </Typography>
      )}
      {data?.summary && !first && !empty && (
        <Typography sx={{ typography: "s2", color: "text.secondary" }}>
          {data.summary}
        </Typography>
      )}
      {first && version.summary && (
        <Typography sx={{ typography: "s2", color: "text.secondary" }}>
          {version.summary}
        </Typography>
      )}
      {piece === "agent" && (
        <PreflightReport report={version.preflightReport} />
      )}
      {body()}
      {piece === "suite" && <SuiteMembers envId={envId} version={version} />}
    </Stack>
  );
}
VersionDetail.propTypes = {
  envId: PropTypes.string.isRequired,
  move: PropTypes.object.isRequired,
  piece: PropTypes.oneOf(VERSION_PIECES).isRequired,
  version: PropTypes.object.isRequired,
  siblings: PropTypes.arrayOf(PropTypes.object).isRequired,
  defaultId: PropTypes.string,
};

// The Versions tab: the agent, environment and scenario versions side by side,
// and, for the version picked, what changed in it and a way to make it the
// default.
export default function VersionsPanel({ env }) {
  const enabled = Boolean(env?.versioning?.enabled);
  const { data, isLoading, error } = useEnvironmentVersions(env?.id, {
    enabled,
  });
  // A link (the unsaved-changes bar) may name the version to show first.
  const [params, setParams] = useSearchParams();
  const [linkedPiece, linkedId] = (params.get("version") || "").split(":");
  const [picked, setPicked] = useState(null);
  useEffect(() => {
    if (linkedId) setPicked({ piece: linkedPiece, id: linkedId });
  }, [linkedPiece, linkedId]);
  // Held here so a refusal outlives the refresh that re-renders the detail.
  const move = useMoveDefault(env?.id);

  // Until the reader picks one, show the default environment version.
  const selection = useMemo(() => {
    if (!data) return null;
    if (picked && data[picked.piece]?.some((v) => v.id === picked.id))
      return picked;
    const piece = data.defaults.environment
      ? "environment"
      : VERSION_PIECES.find((p) => data[p].length);
    if (!piece) return null;
    // The default first; otherwise the newest version that is not a draft.
    const version =
      data.defaults[piece] ||
      data[piece].find((v) => v.status !== "draft") ||
      data[piece][0];
    return { piece, id: version.id };
  }, [data, picked]);

  if (!enabled) return null;

  const frame = (children) => (
    <Stack spacing={2.5} sx={{ p: 3 }}>
      <Box>
        <Typography sx={{ typography: "m3", fontWeight: "fontWeightBold" }}>
          {VERSIONS_COPY.title}
        </Typography>
        <Typography sx={{ typography: "s2", color: "text.subtitle", mt: 0.5 }}>
          {VERSIONS_COPY.subtitle}
        </Typography>
      </Box>
      {children}
    </Stack>
  );

  if (isLoading) return frame(<CircularProgress size={22} />);
  if (isVersioningDisabled(error)) {
    return frame(
      <Typography sx={{ typography: "s2", color: "text.secondary" }}>
        {VERSIONS_COPY.disabled}
      </Typography>,
    );
  }
  // A failed refresh keeps the versions it already has.
  if (!data) {
    return frame(
      <Typography sx={{ typography: "s2", color: "error.main" }}>
        {VERSIONS_COPY.loadError}
      </Typography>,
    );
  }
  if (data.empty) {
    return frame(
      <Box
        sx={{
          border: "1px dashed",
          borderColor: "divider",
          borderRadius: 1.5,
          p: 3,
        }}
      >
        <Typography sx={{ typography: "s1", fontWeight: "fontWeightBold" }}>
          {VERSIONS_COPY.empty.title}
        </Typography>
        <Typography sx={{ typography: "s2", color: "text.secondary", mt: 0.5 }}>
          {VERSIONS_COPY.empty.body}
        </Typography>
      </Box>,
    );
  }

  const selected =
    selection && data[selection.piece].find((v) => v.id === selection.id);

  return frame(
    <>
      <Stack direction={{ xs: "column", md: "row" }} spacing={2}>
        {VERSION_PIECES.map((piece) => (
          <VersionColumn
            key={piece}
            piece={piece}
            versions={data[piece]}
            selectedId={selection?.piece === piece ? selection.id : null}
            onSelect={(p, id) => {
              move.reset();
              setPicked({ piece: p, id });
              // So the next link to a version counts as a change.
              if (params.has("version")) {
                const q = new URLSearchParams(params);
                q.delete("version");
                setParams(q, { replace: true });
              }
            }}
          />
        ))}
      </Stack>
      {selected ? (
        <VersionDetail
          key={`${selection.piece}-${selected.id}`}
          envId={env.id}
          move={move}
          piece={selection.piece}
          version={selected}
          siblings={data[selection.piece]}
          defaultId={data.defaults[selection.piece]?.id ?? null}
        />
      ) : (
        <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
          {VERSIONS_COPY.detail.pick}
        </Typography>
      )}
    </>,
  );
}

VersionsPanel.propTypes = {
  env: PropTypes.shape({
    id: PropTypes.string,
    versioning: PropTypes.shape({ enabled: PropTypes.bool }),
  }).isRequired,
};
