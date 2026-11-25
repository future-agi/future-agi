import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Stack, Typography } from "@mui/material";
import { alpha } from "@mui/material/styles";
import { GridTable, Section } from "./trialChangesParts";
import {
  MONO,
  QUIET as quiet,
  TRIAL_CHANGES_COPY as C,
  showValue,
  KIND_TONE,
} from "./trialChanges.constants";

/* One record: its table and key, what happened to it, then a Field ·
   Before · After table. Changed fields come first, highlighted; the
   unchanged ones fold away behind a toggle. */
export function RecordChange({ one }) {
  const [open, setOpen] = useState(false);
  const fields = [
    ...new Set([
      ...Object.keys(one.before || {}),
      ...Object.keys(one.after || {}),
    ]),
  ];
  const changedSet = new Set(
    one.kind === "changed" ? one.fields || [] : fields,
  );
  const changed = fields.filter((f) => changedSet.has(f));
  const unchanged = fields.filter((f) => !changedSet.has(f));
  const shown = open ? [...changed, ...unchanged] : changed;
  const label =
    one.kind === "changed"
      ? C.recordChanged(one.fields || [])
      : C.recordKinds[one.kind] || one.kind;
  return (
    <Box>
      <Stack
        direction="row"
        alignItems="baseline"
        spacing={1}
        aria-label={`${one.table} ${JSON.stringify(one.key)} · ${label}`}
        sx={{ mb: 0.5, flexWrap: "wrap" }}
      >
        <Typography
          sx={{
            typography: "s2",
            fontWeight: "fontWeightSemiBold",
            fontFamily: MONO,
          }}
        >
          {`${one.table} ${JSON.stringify(one.key)}`}
        </Typography>
        <Typography
          sx={{
            typography: "s3",
            fontWeight: "fontWeightMedium",
            color: KIND_TONE[one.kind] || "text.secondary",
          }}
        >
          {label}
        </Typography>
      </Stack>
      {one.kind !== "changed" ? (
        <GridTable
          cols="minmax(96px, 0.8fr) minmax(0, 2fr)"
          head={[
            C.recordsHead.field,
            one.kind === "removed" ? C.recordsHead.before : C.recordsHead.after,
          ]}
          rows={fields.map((f) => ({
            key: f,
            cells: [
              <Typography
                key="f"
                sx={{
                  typography: "s3",
                  fontFamily: MONO,
                  fontWeight: "fontWeightMedium",
                  wordBreak: "break-all",
                }}
              >
                {f}
              </Typography>,
              <Typography
                key="v"
                sx={{
                  ...quiet,
                  fontFamily: MONO,
                  color: "text.primary",
                  textDecoration:
                    one.kind === "removed" ? "line-through" : "none",
                }}
              >
                {showValue(
                  (one.kind === "removed" ? one.before : one.after)?.[f],
                )}
              </Typography>,
            ],
          }))}
        />
      ) : (
        <GridTable
          cols="minmax(96px, 0.8fr) minmax(0, 1fr) minmax(0, 1fr)"
          head={[
            C.recordsHead.field,
            C.recordsHead.before,
            C.recordsHead.after,
          ]}
          rows={shown.map((f) => {
            const isChanged = changedSet.has(f);
            return {
              key: f,
              sx: (theme) =>
                isChanged && one.kind === "changed"
                  ? { bgcolor: alpha(theme.palette.info.main, 0.04) }
                  : {},
              cells: [
                <Typography
                  key="f"
                  sx={{
                    typography: "s3",
                    fontFamily: MONO,
                    fontWeight: isChanged
                      ? "fontWeightSemiBold"
                      : "fontWeightRegular",
                    color: isChanged ? "text.primary" : "text.secondary",
                    wordBreak: "break-all",
                  }}
                >
                  {f}
                </Typography>,
                <Typography
                  key="b"
                  sx={{
                    ...quiet,
                    fontFamily: MONO,
                    color: one.before ? "text.secondary" : "text.disabled",
                    textDecoration:
                      isChanged && one.kind !== "added"
                        ? "line-through"
                        : "none",
                  }}
                >
                  {one.before ? showValue(one.before[f]) : "—"}
                </Typography>,
                <Typography
                  key="a"
                  sx={{
                    ...quiet,
                    fontFamily: MONO,
                    color: one.after
                      ? isChanged
                        ? "text.primary"
                        : "text.secondary"
                      : "text.disabled",
                  }}
                >
                  {one.after ? showValue(one.after[f]) : "—"}
                </Typography>,
              ],
            };
          })}
        />
      )}
      {one.kind === "changed" && unchanged.length > 0 && (
        <Typography
          component="button"
          type="button"
          onClick={() => setOpen((v) => !v)}
          sx={{
            mt: 0.5,
            p: 0,
            border: 0,
            bgcolor: "transparent",
            cursor: "pointer",
            typography: "s3",
            fontWeight: "fontWeightMedium",
            color: "primary.main",
          }}
        >
          {C.unchangedFields(unchanged.length, open)}
        </Typography>
      )}
    </Box>
  );
}
RecordChange.propTypes = { one: PropTypes.object.isRequired };

// `outside` replaces "no record changed" when the agent committed changes outside the test world.
export function RecordsSection({ records, outside }) {
  const changes = records?.changes || [];
  return (
    <Section
      title={C.recordsHeading}
      count={changes.length || null}
      data-testid="records-before-after"
    >
      {changes.length === 0 && (
        <Typography
          sx={outside ? { ...quiet, color: "warning.dark" } : quiet}
          data-testid={outside ? "changed-outside-world" : undefined}
        >
          {outside ? outside.message : C.recordsEmpty}
        </Typography>
      )}
      <Stack spacing={1.5}>
        {changes.map((one, index) => (
          <RecordChange key={`${one.table}-${index}`} one={one} />
        ))}
      </Stack>
      {(records?.skipped || []).map((one) => (
        <Typography key={`skipped-${one.table}`} sx={{ ...quiet, mt: 0.75 }}>
          {C.notCompared(one.table, one.why)}
        </Typography>
      ))}
      {records?.truncated > 0 && (
        <Typography sx={{ ...quiet, mt: 0.75 }}>
          {C.truncated(records.truncated)}
        </Typography>
      )}
    </Section>
  );
}

RecordsSection.propTypes = {
  outside: PropTypes.shape({
    code: PropTypes.string,
    message: PropTypes.string,
  }),
  records: PropTypes.shape({
    changes: PropTypes.arrayOf(PropTypes.object),
    skipped: PropTypes.arrayOf(PropTypes.object),
    truncated: PropTypes.number,
  }),
};
