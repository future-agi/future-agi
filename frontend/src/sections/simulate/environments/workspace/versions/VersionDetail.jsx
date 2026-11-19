import PropTypes from "prop-types";
import { useState } from "react";
import {
  Box,
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
  useSuiteMembers,
  useVersionDiff,
} from "src/api/simulate-environments/versions";
import { VERSIONS_COPY } from "../workspace.constants";
import { refusalText } from "../evals/refusalText";
import { ROW_HEIGHT } from "src/theme/sizes";
import ContentSkeleton from "src/components/content-skeleton";
import { PreflightReport } from "../agents/PreflightStatus";
import { named } from "../agents/agentVersionStatus";
import VersionDiff from "./VersionDiff";
import { isEmptyDiff } from "./isEmptyDiff";
import { ChangesSkeleton } from "./HistorySkeleton";

const MONO = "ui-monospace, Menlo, monospace";

function SuiteMembers({ envId, version }) {
  const { data, isLoading } = useSuiteMembers(envId, version.id);
  if (isLoading) {
    return (
      <ContentSkeleton
        rows={3}
        role="status"
        label={VERSIONS_COPY.detail.membersLoading}
      />
    );
  }
  // A failed refresh keeps the members it already has.
  if (!data) {
    return (
      <Typography sx={{ typography: "s2", color: "status.danger.fg" }}>
        {VERSIONS_COPY.detail.membersError}
      </Typography>
    );
  }
  const members = data;
  const { members: COLS } = VERSIONS_COPY;
  return (
    <Stack spacing={1}>
      <Typography sx={{ typography: "s2", fontWeight: 500 }}>
        {VERSIONS_COPY.detail.scenarios(named(version), members.length)}
      </Typography>
      <Box sx={{ overflowX: "auto" }}>
        <Table
          size="small"
          sx={{ "& td": { height: ROW_HEIGHT.default } }}
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

// What changed in one version, against its previous one or another the reader
// picks, plus a suite version's scenarios and an agent version's check.
export default function VersionDetail({
  envId,
  move,
  piece,
  version,
  siblings,
}) {
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
    if (isLoading) return <ChangesSkeleton label={detail.loading} />;
    if (isError) {
      return (
        <Typography sx={{ typography: "s2", color: "status.danger.fg" }}>
          {detail.loadError}
        </Typography>
      );
    }
    if (noRecord) return note(detail.noRecord);
    if (empty) return note(detail.noChange);
    return <VersionDiff piece={piece} diff={data.diff} />;
  };

  return (
    <Stack spacing={2} data-testid="version-detail">
      <Stack
        direction={{ xs: "column", sm: "row" }}
        spacing={2}
        alignItems={{ sm: "center" }}
        flexWrap="wrap"
        useFlexGap
      >
        <Typography sx={{ typography: "s1", fontWeight: 500, flex: 1 }}>
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
      </Stack>
      {move.isError && (
        <Typography
          role="alert"
          sx={{ typography: "s2", color: "status.danger.fg" }}
        >
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
};
