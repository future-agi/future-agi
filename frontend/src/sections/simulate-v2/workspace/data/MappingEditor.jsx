import PropTypes from "prop-types";
import { useState } from "react";
import {
  Box,
  Button,
  Checkbox,
  MenuItem,
  Select,
  Stack,
  Typography,
} from "@mui/material";
import {
  COLUMN_TYPES,
  WORLD_COLUMNS,
  approveIdMappingPatch,
  columnEditPatch,
  dismissIdMappingPatch,
  idCheckOf,
  mappingReviewOf,
  readAsNumberPatch,
  relationshipTargets,
  standInId,
} from "../../_mock/dataMapping";
import { joinsOf, missingRecordsOf } from "../../_mock/orgData";
import { neutralCheckboxSx } from "../../components/primitives";

const MONO = "ui-monospace, Menlo, monospace";
const LINE = { border: "1px solid", borderColor: "divider", borderRadius: 1 };

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
      <Box sx={{ flex: 1, minWidth: 0, typography: "s2" }}>{children}</Box>
    </Stack>
  );
}
Row.propTypes = { label: PropTypes.string, children: PropTypes.node };

function Heading({ children, note }) {
  return (
    <Box>
      <Typography sx={{ typography: "s2", fontWeight: 600 }}>
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

/**
 * A records file's mapping into the world (data.md "Field mapping"): the ID
 * the builder proposes to match, a type and stable-ID marker per column, the
 * relationships between tables, fields it couldn't place, and a column stored
 * as the wrong type. Every edit is the person's; nothing is applied silently.
 */
export default function MappingEditor({
  source,
  envState,
  patch,
  readOnly = false,
  onSupply,
  onOmit,
  onPutBack,
}) {
  const p = source.parsed;
  const all = envState.sources || [];
  const review = mappingReviewOf(source);
  const joins = joinsOf(source, all);
  const gaps = missingRecordsOf(envState).filter(
    (m) => m.sourceId === source.id,
  );
  const targets = relationshipTargets(source, all);
  /* Replacing IDs is suggested; the person ticks it off to keep them. */
  const [replace, setReplace] = useState(true);
  const edit = (name, fields, detail) =>
    patch(columnEditPatch(envState, source, name, fields, detail));
  const snapshot = source.kb
    ? `${source.name} rev ${source.kb.revision}`
    : `${source.name} v${source.version}`;

  return (
    <Stack spacing={1.75}>
      <Box>
        <Row label="Becomes table">
          <Box component="span" sx={{ fontFamily: MONO }}>
            {p.table}
          </Box>
        </Row>
        <Row label="Key">{p.columns.find((c) => c.key)?.name}</Row>
        <Row label="Mapping version">{`v${source.mapping?.v || 1}${
          review.proposals.length +
          review.unmatched.length +
          review.wrongType.length
            ? ` · ${review.proposals.length + review.unmatched.length + review.wrongType.length} to review`
            : " · nothing to review"
        }`}</Row>
        <Row label="Source snapshot">{`${snapshot} · ${source.snapshot ? `snapshot ${source.snapshot}` : "not saved into the environment yet"}`}</Row>
        <Row label="Duplicates">
          Rejected — a later row with the same key is listed, not merged
        </Row>
      </Box>

      {/* The same ID under another name. */}
      {p.columns
        .filter((c) => c.proposal)
        .map((c) => {
          const check = idCheckOf(c, all);
          const done = c.proposal.approved;
          const was = c.proposal.id;
          return (
            <Box key={c.name} sx={{ ...LINE, p: 1.25 }}>
              <Heading
                note={
                  done
                    ? `Approved · mapping v${source.mapping?.v || 1}`
                    : "Suggested by the builder — not applied until you approve it."
                }
              >
                {done
                  ? `${p.table}.${c.name} is ${c.ref}`
                  : `Match ${p.table}.${c.name} to ${c.proposal.to}`}
              </Heading>
              <Box sx={{ mt: 0.75 }}>
                <Row label="Check">
                  {check.found ? (
                    `${was} exists in ${check.target.name}`
                  ) : check.target ? (
                    `${was} isn't in ${check.target.name}`
                  ) : (
                    <Box component="span" sx={{ color: "status.warning.fg" }}>
                      {`${check.table} isn't attached, so ${was} can't be checked.`}
                    </Box>
                  )}
                </Row>
                <Row label="Same person">
                  {`${p.table}.${c.name} = ${was} · ${check.table}.${check.field} = ${was}`}
                </Row>
                <Row label="IDs replaced">
                  <Box component="span" sx={{ fontFamily: MONO }}>
                    {`${was} → ${standInId(was)}`}
                  </Box>
                  {` in ${p.table}.${c.name} and in ${check.table}.${check.field} — the same stand-in in both files, so the relationship holds.`}
                </Row>
                {done && (
                  <Row label="Mismatches">
                    {p.rejected.length
                      ? `${p.rejected.length} orders name buyers that aren't in ${check.table} — listed under Relationships.`
                      : "None"}
                  </Row>
                )}
              </Box>
              {!done && !readOnly && (
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
                      onChange={(e) => setReplace(e.target.checked)}
                      sx={{ ...neutralCheckboxSx, p: 0.25 }}
                    />
                    <Typography sx={{ typography: "s2" }}>
                      Replace the IDs with synthetic ones in both files
                    </Typography>
                    <Typography
                      sx={{ typography: "s3", color: "text.tertiary" }}
                    >
                      Suggested
                    </Typography>
                  </Stack>
                  <Stack direction="row" spacing={1}>
                    <Button
                      size="small"
                      variant="contained"
                      color="primary"
                      onClick={() =>
                        patch(
                          approveIdMappingPatch(envState, source, c.name, {
                            replace,
                          }),
                        )
                      }
                    >
                      Approve mapping
                    </Button>
                    <Button
                      size="small"
                      variant="outlined"
                      onClick={() =>
                        patch(dismissIdMappingPatch(envState, source, c.name))
                      }
                    >
                      Not the same ID
                    </Button>
                    {!check.target && (
                      <Button
                        size="small"
                        variant="outlined"
                        onClick={(e) =>
                          onSupply?.(e.currentTarget, {
                            to: c.proposal.to,
                            field: c.name,
                            present: false,
                          })
                        }
                      >
                        Supply the records
                      </Button>
                    )}
                  </Stack>
                </Stack>
              )}
            </Box>
          );
        })}

      {/* Fields the builder couldn't place. */}
      {(p.columns.some((c) => c.unmatched) || review.unmatched.length > 0) && (
        <Box>
          <Heading
            note={
              review.unmatched.length
                ? "No matching column in the world"
                : "All resolved."
            }
          >{`Unmatched fields · ${review.unmatched.length}`}</Heading>
          <Box sx={{ ...LINE, mt: 0.75 }}>
            {p.columns
              .filter((c) => c.unmatched)
              .map((c) => (
                <Stack
                  key={c.name}
                  direction="row"
                  alignItems="center"
                  spacing={1}
                  sx={{
                    px: 1.25,
                    py: 0.75,
                    "& + &": { borderTop: "1px solid", borderColor: "divider" },
                  }}
                >
                  <Box sx={{ width: 150, flexShrink: 0, minWidth: 0 }}>
                    <Typography
                      noWrap
                      sx={{ typography: "s2", fontFamily: MONO }}
                    >
                      {c.name}
                    </Typography>
                    <Typography
                      noWrap
                      sx={{
                        typography: "s3",
                        color: "text.tertiary",
                        fontFamily: MONO,
                      }}
                    >
                      {String(p.sample?.[c.name] ?? "")}
                    </Typography>
                  </Box>
                  {c.mappedTo || c.leftOut ? (
                    <>
                      <Typography sx={{ typography: "s2", flex: 1 }}>
                        {c.leftOut ? "Left out" : `Mapped to ${c.mappedTo}`}
                      </Typography>
                      {!readOnly && (
                        <Button
                          size="small"
                          variant="outlined"
                          onClick={() =>
                            edit(
                              c.name,
                              { mappedTo: null, leftOut: false },
                              `${source.name}.${c.name} put back for review`,
                            )
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
                        onChange={(e) =>
                          edit(
                            c.name,
                            { mappedTo: e.target.value },
                            `${source.name}.${c.name} mapped to ${e.target.value}`,
                          )
                        }
                        renderValue={() => "Map to a column"}
                        sx={{ typography: "s3", flex: 1, minWidth: 0 }}
                      >
                        {(WORLD_COLUMNS[p.table] || []).map((w) => (
                          <MenuItem key={w} value={w} sx={{ typography: "s3" }}>
                            {`${w}${w === c.suggest ? " · suggested" : ""}`}
                          </MenuItem>
                        ))}
                      </Select>
                      <Button
                        size="small"
                        variant="outlined"
                        disabled={readOnly}
                        onClick={() =>
                          edit(
                            c.name,
                            { leftOut: true },
                            `${source.name}.${c.name} left out`,
                          )
                        }
                      >
                        Leave out
                      </Button>
                    </>
                  )}
                </Stack>
              ))}
          </Box>
        </Box>
      )}

      {/* A column that holds numbers stored as text. */}
      {p.columns
        .filter((c) => c.textRows?.length && c.type !== "number")
        .map((c) => (
          <Box key={c.name}>
            <Heading note="Text in a number column">{`${c.name} is stored as text · ${c.textRows.length} rows`}</Heading>
            <Box sx={{ ...LINE, mt: 0.75 }}>
              {c.textRows.map((r) => (
                <Stack
                  key={r.row}
                  direction="row"
                  spacing={1.5}
                  sx={{
                    px: 1.25,
                    py: 0.5,
                    bgcolor: "action.selected",
                    "& + &": { borderTop: "1px solid", borderColor: "divider" },
                  }}
                >
                  <Typography
                    sx={{ typography: "s3", color: "text.tertiary", width: 64 }}
                  >{`Row ${r.row}`}</Typography>
                  <Typography
                    sx={{ typography: "s2", fontFamily: MONO, flex: 1 }}
                  >
                    {`"${r.value}"`}
                  </Typography>
                  <Typography
                    sx={{ typography: "s3", color: "text.secondary" }}
                  >{`reads as ${r.reads}`}</Typography>
                </Stack>
              ))}
            </Box>
            {!readOnly && (
              <Button
                size="small"
                variant="outlined"
                onClick={() =>
                  patch(readAsNumberPatch(envState, source, c.name))
                }
                sx={{ mt: 0.75 }}
              >
                Read as number
              </Button>
            )}
          </Box>
        ))}

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
            {[
              ["Column", 120],
              ["Type", 104],
              ["Stable ID", 64],
              ["Points at", null],
            ].map(([h, w]) => (
              <Typography
                key={h}
                sx={{
                  typography: "s3",
                  color: "text.tertiary",
                  ...(w ? { width: w, flexShrink: 0 } : { flex: 1 }),
                }}
              >
                {h}
              </Typography>
            ))}
          </Stack>
          {p.columns.map((c) => (
            <Stack
              key={c.name}
              direction="row"
              alignItems="center"
              spacing={1}
              sx={{
                px: 1.25,
                py: 0.5,
                ...(c.textRows?.length &&
                  c.type !== "number" && { bgcolor: "action.selected" }),
                "& + &": { borderTop: "1px solid", borderColor: "divider" },
              }}
            >
              <Typography
                noWrap
                sx={{
                  typography: "s2",
                  fontFamily: MONO,
                  width: 120,
                  flexShrink: 0,
                }}
              >
                {c.name}
                {c.key && (
                  <Box
                    component="span"
                    sx={{ typography: "s3", color: "text.tertiary", ml: 0.5 }}
                  >
                    key
                  </Box>
                )}
              </Typography>
              <Select
                size="small"
                value={c.type}
                disabled={readOnly}
                onChange={(e) =>
                  edit(
                    c.name,
                    { type: e.target.value },
                    `${source.name}.${c.name} is read as ${e.target.value}`,
                  )
                }
                inputProps={{ "aria-label": `Type of ${c.name}` }}
                sx={{ typography: "s3", width: 104, flexShrink: 0 }}
              >
                {[...new Set([c.type, ...COLUMN_TYPES])].map((t) => (
                  <MenuItem key={t} value={t} sx={{ typography: "s3" }}>
                    {t}
                  </MenuItem>
                ))}
              </Select>
              <Box sx={{ width: 64, flexShrink: 0 }}>
                <Checkbox
                  size="small"
                  checked={!!(c.stableId || c.key)}
                  disabled={readOnly || !!c.key}
                  onChange={(e) =>
                    edit(
                      c.name,
                      { stableId: e.target.checked },
                      `${source.name}.${c.name} ${e.target.checked ? "is" : "is no longer"} a stable ID`,
                    )
                  }
                  inputProps={{ "aria-label": `${c.name} is a stable ID` }}
                  sx={{ ...neutralCheckboxSx, p: 0.25 }}
                />
              </Box>
              <Select
                size="small"
                displayEmpty
                value={c.ref || ""}
                disabled={readOnly || !!c.key}
                onChange={(e) =>
                  edit(
                    c.name,
                    { ref: e.target.value || null },
                    e.target.value
                      ? `${source.name}.${c.name} points at ${e.target.value}`
                      : `${source.name}.${c.name} no longer points anywhere`,
                  )
                }
                renderValue={(v) => v || "—"}
                inputProps={{ "aria-label": `${c.name} points at` }}
                sx={{ typography: "s3", flex: 1, minWidth: 0 }}
              >
                <MenuItem value="" sx={{ typography: "s3" }}>
                  Nothing
                </MenuItem>
                {[...new Set([...(c.ref ? [c.ref] : []), ...targets])].map(
                  (t) => (
                    <MenuItem key={t} value={t} sx={{ typography: "s3" }}>
                      {t}
                    </MenuItem>
                  ),
                )}
              </Select>
            </Stack>
          ))}
        </Box>
      </Box>

      <Heading>Relationships</Heading>
      {joins.length === 0 && (
        <Typography sx={{ typography: "s3", color: "text.tertiary", mt: -1 }}>
          No references to other tables.
        </Typography>
      )}
      {joins.map((j) => {
        const gap = gaps.find((g) => g.field === j.field);
        return (
          <Box key={j.field} sx={{ ...LINE, p: 1.25, mt: -0.5 }}>
            <Typography sx={{ typography: "s2" }}>
              <Box
                component="span"
                sx={{ fontFamily: MONO }}
              >{`${p.table}.${j.field} → ${j.to}`}</Box>
            </Typography>
            <Typography
              sx={{
                typography: "s3",
                color: j.blocking ? "status.warning.fg" : "text.tertiary",
              }}
            >
              {j.resolution === "omit"
                ? `${j.missing} rows left out — the cases that need them stay blocked.`
                : j.resolution === "supplied"
                  ? "The missing records were supplied — every row resolves."
                  : !j.present
                    ? `${j.to.split(".")[0]} isn't attached — all ${j.missing} rows refer to it.`
                    : j.missing
                      ? `${j.missing} rows point at records that aren't there.`
                      : "Every row resolves."}
            </Typography>
            {j.present && j.rows.length > 0 && (
              <Box sx={{ ...LINE, mt: 0.75 }}>
                {j.rows.map((r) => (
                  <Typography
                    key={r.row}
                    sx={{
                      typography: "s3",
                      px: 1.25,
                      py: 0.5,
                      bgcolor: j.resolution ? "transparent" : "action.selected",
                      color: j.resolution ? "text.tertiary" : "text.primary",
                      "& + &": {
                        borderTop: "1px solid",
                        borderColor: "divider",
                      },
                    }}
                  >{`Row ${r.row}: ${r.reason}`}</Typography>
                ))}
              </Box>
            )}
            {gap?.scenarios.length > 0 && (
              <Typography
                sx={{ typography: "s3", color: "text.secondary", mt: 0.5 }}
              >{`Scenarios that use them: ${gap.scenarios.map((s) => s.name || s.title || s.id).join(", ")}.`}</Typography>
            )}
            {!readOnly && j.blocking && (
              <Stack direction="row" spacing={1} sx={{ mt: 0.75 }}>
                <Button
                  size="small"
                  variant="outlined"
                  onClick={(e) => onSupply?.(e.currentTarget, j)}
                >
                  Supply the records
                </Button>
                <Button
                  size="small"
                  variant="outlined"
                  onClick={() => onOmit?.(j)}
                >
                  Leave those rows out
                </Button>
              </Stack>
            )}
            {!readOnly && j.resolution === "omit" && (
              <Button
                size="small"
                variant="outlined"
                onClick={() => onPutBack?.(j)}
                sx={{ mt: 0.75 }}
              >
                Put those rows back
              </Button>
            )}
          </Box>
        );
      })}
    </Stack>
  );
}

MappingEditor.propTypes = {
  source: PropTypes.object.isRequired,
  envState: PropTypes.object.isRequired,
  patch: PropTypes.func.isRequired,
  readOnly: PropTypes.bool,
  onSupply: PropTypes.func,
  onOmit: PropTypes.func,
  onPutBack: PropTypes.func,
};
