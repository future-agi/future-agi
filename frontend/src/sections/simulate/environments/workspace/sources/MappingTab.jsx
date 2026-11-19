import PropTypes from "prop-types";
import { useState } from "react";
import { closeSnackbar, enqueueSnackbar } from "notistack";
import {
  Box,
  Button,
  Checkbox,
  Collapse,
  MenuItem,
  Select,
  Stack,
  Typography,
} from "@mui/material";
import { useEnvironmentSources } from "src/api/simulate-environments/sources";
import ShimmerText from "../../components/ShimmerText";
import usePrefersReducedMotion from "../../components/usePrefersReducedMotion";
import { MONO_FONT } from "../contract/contract.constants";
import { SOURCES_COPY, isSaved } from "./sources.constants";
import {
  COLUMN_TYPES,
  WORLD_COLUMNS,
  approveIdMapping,
  editColumn,
  idCheckOf,
  joinsOf,
  mappingVersionOf,
  relationshipTargets,
  setJoin,
  toReviewOf,
} from "./mapping";
import { standInId } from "./personalData";

const LINE = { border: "1px solid", borderColor: "divider", borderRadius: 1 };
const ROW_SX = {
  px: 1.25,
  py: 0.75,
  "& + &": { borderTop: "1px solid", borderColor: "divider" },
};
const mono = (text) => (
  <Box component="span" sx={{ fontFamily: MONO_FONT }}>
    {text}
  </Box>
);

function Row({ label, children }) {
  return (
    <Stack
      direction="row"
      spacing={2}
      sx={{
        py: 0.5,
        "& + &": { borderTop: "1px solid", borderColor: "divider" },
      }}
    >
      <Typography
        sx={{
          typography: "s3",
          color: "text.tertiary",
          width: 128,
          flexShrink: 0,
        }}
      >
        {label}
      </Typography>
      <Box sx={{ flex: 1, minWidth: 0, typography: "s1" }}>{children}</Box>
    </Stack>
  );
}

Row.propTypes = {
  label: PropTypes.string.isRequired,
  children: PropTypes.node,
};

function Heading({ children, note }) {
  return (
    <Box>
      <Typography sx={{ typography: "s1", fontWeight: 600 }}>
        {children}
      </Typography>
      {note && (
        <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
          {note}
        </Typography>
      )}
    </Box>
  );
}

Heading.propTypes = { children: PropTypes.node, note: PropTypes.string };

// A column that holds the same ID another file calls something else.
function IdProposal({ source, column, sources, readOnly, onEdit, onSupply }) {
  // Replacing IDs is suggested; the person unticks it to keep them.
  const [replace, setReplace] = useState(true);
  const reduce = usePrefersReducedMotion();
  const { table, rejected } = source.ui.parsed;
  const check = idCheckOf(column, sources);
  const done = column.proposal.approved;
  const id = column.proposal.id;

  let checkLine = (
    <Box component="span" sx={{ color: "status.warning.fg" }}>
      {`${check.table} isn't attached, so ${id} can't be checked.`}
    </Box>
  );
  if (check.found) checkLine = `${id} exists in ${check.target.name}`;
  else if (check.target) checkLine = `${id} isn't in ${check.target.name}`;

  return (
    <Box sx={{ ...LINE, p: 1.25 }}>
      <Heading
        note={
          done
            ? `Approved · mapping v${mappingVersionOf(source)}`
            : "Suggested by the builder. Not applied until you approve it."
        }
      >
        {done
          ? `${table}.${column.name} is ${column.ref}`
          : `Match ${table}.${column.name} to ${column.proposal.to}`}
      </Heading>
      <Box sx={{ mt: 0.75 }}>
        <Row label="Check">{checkLine}</Row>
        <Row label="Same person">
          {`${table}.${column.name} = ${id} · ${check.table}.${check.field} = ${id}`}
        </Row>
        <Row label="IDs replaced">
          {mono(`${id} → ${standInId(id)}`)}
          {` in ${table}.${column.name} and in ${check.table}.${check.field}. The same stand-in in both files, so the relationship holds.`}
        </Row>
        {done && (
          <Row label="Mismatches">
            {rejected.length
              ? `${rejected.length} rows of ${table} name a ${check.field} that isn't in ${check.table}. They're listed under Relationships.`
              : "None"}
          </Row>
        )}
      </Box>
      {/* Approving folds the actions away. */}
      <Collapse
        in={!done && !readOnly}
        timeout={reduce ? 0 : "auto"}
        unmountOnExit
      >
        <Stack spacing={1} sx={{ mt: 1 }}>
          <Stack
            component="label"
            direction="row"
            alignItems="center"
            spacing={0.5}
            sx={{ cursor: "pointer" }}
          >
            <Checkbox
              size="small"
              checked={replace}
              onChange={(event) => setReplace(event.target.checked)}
              sx={{ p: 0.25 }}
            />
            <Typography sx={{ typography: "s1" }}>
              Replace the IDs with synthetic ones in both files
            </Typography>
            <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
              Suggested
            </Typography>
          </Stack>
          <Stack direction="row" spacing={1}>
            <Button
              size="small"
              variant="contained"
              color="primary"
              onClick={() =>
                onEdit((one) => approveIdMapping(one, column.name, { replace }))
              }
            >
              Approve mapping
            </Button>
            <Button
              size="small"
              variant="outlined"
              onClick={() =>
                onEdit((one) =>
                  editColumn(one, column.name, { proposal: null }),
                )
              }
            >
              Not the same ID
            </Button>
            {!check.target && (
              <Button size="small" variant="outlined" onClick={onSupply}>
                Supply the records
              </Button>
            )}
          </Stack>
        </Stack>
      </Collapse>
    </Box>
  );
}

IdProposal.propTypes = {
  source: PropTypes.object.isRequired,
  column: PropTypes.object.isRequired,
  sources: PropTypes.array.isRequired,
  readOnly: PropTypes.bool,
  onEdit: PropTypes.func.isRequired,
  onSupply: PropTypes.func.isRequired,
};

function Unmatched({ source, readOnly, onEdit }) {
  const { table, columns, sample } = source.ui.parsed;
  const unmatched = columns.filter((column) => column.unmatched);
  const open = unmatched.filter(
    (column) => !column.mappedTo && !column.leftOut,
  );
  const edit = (name, fields) => onEdit((one) => editColumn(one, name, fields));
  if (!unmatched.length) return null;

  return (
    <Box>
      <Heading
        note={open.length ? "No matching column in the world" : "All resolved."}
      >
        {`Unmatched fields · ${open.length}`}
      </Heading>
      <Box sx={{ ...LINE, mt: 0.75 }}>
        {unmatched.map((column) => (
          <Stack
            key={column.name}
            direction="row"
            alignItems="center"
            spacing={1}
            sx={ROW_SX}
          >
            <Box sx={{ width: 150, flexShrink: 0, minWidth: 0 }}>
              <Typography
                noWrap
                sx={{ typography: "s1", fontFamily: MONO_FONT }}
              >
                {column.name}
              </Typography>
              <Typography
                noWrap
                sx={{
                  typography: "s3",
                  color: "text.tertiary",
                  fontFamily: MONO_FONT,
                }}
              >
                {String(sample?.[column.name] ?? "")}
              </Typography>
            </Box>
            {column.mappedTo || column.leftOut ? (
              <>
                <Typography sx={{ typography: "s1", flex: 1 }}>
                  {column.leftOut ? "Left out" : `Mapped to ${column.mappedTo}`}
                </Typography>
                {!readOnly && (
                  <Button
                    size="small"
                    variant="outlined"
                    onClick={() =>
                      edit(column.name, { mappedTo: null, leftOut: false })
                    }
                  >
                    Undo
                  </Button>
                )}
              </>
            ) : (
              <>
                <Select
                  size="small"
                  displayEmpty
                  value=""
                  disabled={readOnly}
                  onChange={(event) =>
                    edit(column.name, { mappedTo: event.target.value })
                  }
                  renderValue={() => "Map to a column"}
                  inputProps={{
                    "aria-label": `Map ${column.name} to a column`,
                  }}
                  sx={{ typography: "s3", flex: 1, minWidth: 0 }}
                >
                  {(WORLD_COLUMNS[table] || []).map((name) => (
                    <MenuItem key={name} value={name} sx={{ typography: "s3" }}>
                      {`${name}${name === column.suggest ? " · suggested" : ""}`}
                    </MenuItem>
                  ))}
                </Select>
                <Button
                  size="small"
                  variant="outlined"
                  disabled={readOnly}
                  onClick={() => edit(column.name, { leftOut: true })}
                >
                  Leave out
                </Button>
              </>
            )}
          </Stack>
        ))}
      </Box>
    </Box>
  );
}

Unmatched.propTypes = {
  source: PropTypes.object.isRequired,
  readOnly: PropTypes.bool,
  onEdit: PropTypes.func.isRequired,
};

function StoredAsText({ column, readOnly, onRead }) {
  return (
    <Box>
      <Heading note="Text in a number column">
        {`${column.name} is stored as text · ${column.textRows.length} rows`}
      </Heading>
      <Box sx={{ ...LINE, mt: 0.75 }}>
        {column.textRows.map((row) => (
          <Stack
            key={row.row}
            direction="row"
            spacing={1.5}
            sx={{ ...ROW_SX, py: 0.5, bgcolor: "action.selected" }}
          >
            <Typography
              sx={{ typography: "s3", color: "text.tertiary", width: 64 }}
            >
              {`Row ${row.row}`}
            </Typography>
            <Typography
              sx={{ typography: "s1", fontFamily: MONO_FONT, flex: 1 }}
            >
              {`"${row.value}"`}
            </Typography>
            <Typography sx={{ typography: "s3", color: "text.secondary" }}>
              {`reads as ${row.reads}`}
            </Typography>
          </Stack>
        ))}
      </Box>
      {!readOnly && (
        <Button
          size="small"
          variant="outlined"
          onClick={onRead}
          sx={{ mt: 0.75 }}
        >
          Read as a number
        </Button>
      )}
    </Box>
  );
}

StoredAsText.propTypes = {
  column: PropTypes.object.isRequired,
  readOnly: PropTypes.bool,
  onRead: PropTypes.func.isRequired,
};

const HEADERS = [
  ["Column", 120],
  ["Type", 104],
  ["Stable ID", 64],
  ["Points at", null],
];

function Columns({ source, targets, readOnly, onEdit }) {
  const edit = (name, fields) => onEdit((one) => editColumn(one, name, fields));
  return (
    <Box>
      <Heading>Columns</Heading>
      <Box sx={{ ...LINE, mt: 0.75 }}>
        <Stack
          direction="row"
          spacing={1}
          sx={{
            px: 1.25,
            py: 0.5,
            borderBottom: "1px solid",
            borderColor: "divider",
          }}
        >
          {HEADERS.map(([label, width]) => (
            <Typography
              key={label}
              sx={{
                typography: "s3",
                color: "text.tertiary",
                ...(width ? { width, flexShrink: 0 } : { flex: 1 }),
              }}
            >
              {label}
            </Typography>
          ))}
        </Stack>
        {source.ui.parsed.columns.map((column) => (
          <Stack
            key={column.name}
            direction="row"
            alignItems="center"
            spacing={1}
            sx={{
              ...ROW_SX,
              py: 0.5,
              ...(column.textRows?.length &&
                column.type !== "number" && { bgcolor: "action.selected" }),
            }}
          >
            <Typography
              noWrap
              sx={{
                typography: "s1",
                fontFamily: MONO_FONT,
                width: 120,
                flexShrink: 0,
              }}
            >
              {column.name}
            </Typography>
            <Select
              size="small"
              value={column.type}
              disabled={readOnly}
              onChange={(event) =>
                edit(column.name, { type: event.target.value })
              }
              inputProps={{ "aria-label": `Type of ${column.name}` }}
              sx={{ typography: "s3", width: 104, flexShrink: 0 }}
            >
              {[...new Set([column.type, ...COLUMN_TYPES])].map((type) => (
                <MenuItem key={type} value={type} sx={{ typography: "s3" }}>
                  {type}
                </MenuItem>
              ))}
            </Select>
            <Box sx={{ width: 64, flexShrink: 0 }}>
              <Checkbox
                size="small"
                checked={Boolean(column.stableId || column.key)}
                disabled={readOnly || Boolean(column.key)}
                onChange={(event) =>
                  edit(column.name, { stableId: event.target.checked })
                }
                inputProps={{ "aria-label": `${column.name} is a stable ID` }}
                sx={{ p: 0.25 }}
              />
            </Box>
            <Select
              size="small"
              displayEmpty
              value={column.ref || ""}
              disabled={readOnly || Boolean(column.key)}
              onChange={(event) =>
                edit(column.name, { ref: event.target.value || null })
              }
              renderValue={(value) => value || "—"}
              inputProps={{ "aria-label": `${column.name} points at` }}
              sx={{ typography: "s3", flex: 1, minWidth: 0 }}
            >
              <MenuItem value="" sx={{ typography: "s3" }}>
                Nothing
              </MenuItem>
              {[
                ...new Set([...(column.ref ? [column.ref] : []), ...targets]),
              ].map((target) => (
                <MenuItem key={target} value={target} sx={{ typography: "s3" }}>
                  {target}
                </MenuItem>
              ))}
            </Select>
          </Stack>
        ))}
      </Box>
    </Box>
  );
}

Columns.propTypes = {
  source: PropTypes.object.isRequired,
  targets: PropTypes.arrayOf(PropTypes.string).isRequired,
  readOnly: PropTypes.bool,
  onEdit: PropTypes.func.isRequired,
};

const joinLine = (join) => {
  if (join.resolution === "omit")
    return `${join.missing} rows left out. The cases that need them stay blocked.`;
  if (!join.present)
    return `${join.table} isn't attached, all ${join.missing} rows refer to it.`;
  if (join.missing)
    return `${join.missing} rows point at records that aren't there.`;
  return "Every row resolves.";
};

function Relationships({ source, joins, readOnly, onEdit, onSupply }) {
  const omit = (join) => {
    onEdit((one) => setJoin(one, join.field, "omit"));
    enqueueSnackbar(`Left ${join.missing} rows of ${source.name} out.`, {
      variant: "info",
      action: (key) => (
        <Button
          size="small"
          color="inherit"
          onClick={() => {
            closeSnackbar(key);
            onEdit((one) => setJoin(one, join.field, null));
          }}
        >
          Undo
        </Button>
      ),
    });
  };

  return (
    <Stack spacing={0.75}>
      <Heading>Relationships</Heading>
      {joins.length === 0 && (
        <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
          No references to other tables.
        </Typography>
      )}
      {joins.map((join) => (
        <Box key={join.field} sx={{ ...LINE, p: 1.25 }}>
          <Typography sx={{ typography: "s1" }}>
            {mono(`${source.ui.parsed.table}.${join.field} → ${join.to}`)}
          </Typography>
          <Typography
            sx={{
              typography: "s3",
              color: join.blocking ? "status.warning.fg" : "text.tertiary",
            }}
          >
            {joinLine(join)}
          </Typography>
          {join.rows.length > 0 && (
            <Box sx={{ ...LINE, mt: 0.75 }}>
              {join.rows.map((row) => (
                <Typography
                  key={row.row}
                  sx={{
                    ...ROW_SX,
                    py: 0.5,
                    typography: "s3",
                    color: join.resolution ? "text.tertiary" : "text.primary",
                    ...(!join.resolution && { bgcolor: "action.selected" }),
                  }}
                >
                  {`Row ${row.row}: ${row.reason}`}
                </Typography>
              ))}
            </Box>
          )}
          {!readOnly && join.blocking && (
            <Stack direction="row" spacing={1} sx={{ mt: 0.75 }}>
              <Button size="small" variant="outlined" onClick={onSupply}>
                Supply the records
              </Button>
              <Button
                size="small"
                variant="outlined"
                onClick={() => omit(join)}
              >
                Leave those rows out
              </Button>
            </Stack>
          )}
          {!readOnly && join.resolution === "omit" && (
            <Button
              size="small"
              variant="outlined"
              onClick={() => onEdit((one) => setJoin(one, join.field, null))}
              sx={{ mt: 0.75 }}
            >
              Put those rows back
            </Button>
          )}
        </Box>
      ))}
    </Stack>
  );
}

Relationships.propTypes = {
  source: PropTypes.object.isRequired,
  joins: PropTypes.array.isRequired,
  readOnly: PropTypes.bool,
  onEdit: PropTypes.func.isRequired,
  onSupply: PropTypes.func.isRequired,
};

/**
 * A records file's mapping into the world: the table it becomes, IDs the
 * builder proposes to match, fields it couldn't place, numbers stored as
 * text, each column's type, stable ID and reference, and the relationships
 * with rows that point at missing records. Nothing applies until the person
 * edits it; edits go through `onEdit`. Supply the records calls `onSupply`.
 */
export default function MappingTab({
  envId,
  source,
  readOnly = false,
  onEdit,
  onSupply,
}) {
  const { data: summary } = useEnvironmentSources(envId);

  if (source.status === "reading")
    return <ShimmerText>{SOURCES_COPY.state.reading}</ShimmerText>;

  const sources = summary?.sources || [source];
  const parsed = source.ui.parsed;
  const review = toReviewOf(source);
  const snapshot = summary?.snapshot_digest
    ?.replace(/^sha256:/, "")
    .slice(0, 12);
  const name =
    source.origin?.kind === "knowledge_base"
      ? `${source.name} · ${SOURCES_COPY.yourCopy}`
      : `${source.name} v${source.version}`;

  return (
    <Stack spacing={1.75}>
      <Box>
        <Row label="Becomes table">{mono(parsed.table)}</Row>
        <Row label="Mapping version">
          {`v${mappingVersionOf(source)} · ${review ? `${review} to review` : "nothing to review"}`}
        </Row>
        <Row label="Source snapshot">
          {`${name} · ${isSaved(source) && snapshot ? `snapshot ${snapshot}` : "not saved into the environment yet"}`}
        </Row>
        <Row label="Duplicates">
          Rejected. A later row with the same stable ID is listed, not merged.
        </Row>
      </Box>

      {parsed.columns
        .filter((column) => column.proposal)
        .map((column) => (
          <IdProposal
            key={column.name}
            source={source}
            column={column}
            sources={sources}
            readOnly={readOnly}
            onEdit={onEdit}
            onSupply={onSupply}
          />
        ))}

      <Unmatched source={source} readOnly={readOnly} onEdit={onEdit} />

      {parsed.columns
        .filter((column) => column.textRows?.length && column.type !== "number")
        .map((column) => (
          <StoredAsText
            key={column.name}
            column={column}
            readOnly={readOnly}
            onRead={() =>
              onEdit((one) =>
                editColumn(one, column.name, { type: "number", textRows: [] }),
              )
            }
          />
        ))}

      <Columns
        source={source}
        targets={relationshipTargets(source, sources)}
        readOnly={readOnly}
        onEdit={onEdit}
      />

      <Relationships
        source={source}
        joins={joinsOf(source, sources)}
        readOnly={readOnly}
        onEdit={onEdit}
        onSupply={onSupply}
      />
    </Stack>
  );
}

MappingTab.propTypes = {
  envId: PropTypes.string,
  source: PropTypes.shape({
    name: PropTypes.string.isRequired,
    status: PropTypes.string,
    version: PropTypes.number,
    origin: PropTypes.shape({ kind: PropTypes.string }),
    ui: PropTypes.object,
  }).isRequired,
  readOnly: PropTypes.bool,
  onEdit: PropTypes.func.isRequired,
  // Closes the file and opens Sources' Add source menu.
  onSupply: PropTypes.func,
};
