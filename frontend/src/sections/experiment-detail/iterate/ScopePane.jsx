import React from "react";
import PropTypes from "prop-types";
import {
  Checkbox,
  FormControlLabel,
  Link,
  Skeleton,
  Stack,
  Typography,
} from "@mui/material";
import CustomTooltip from "src/components/tooltip";
import Iconify from "src/components/iconify";
import { MAX_DOCK_TRIALS, TRIAL_CHOICES, fmtUsd } from "./iterateModel";
import { SmallSelect } from "./changes/changeParts";

const rowsWord = (k) => `${k} ${k === 1 ? "row" : "rows"}`;

/**
 * IT-R5 G3 (P4): what happens to every row, in one line. Pure.
 * `13 rows rerun · 17 other rows keep their #2B results (not re-run)`.
 */
export function scopeSentence({
  n,
  rest,
  total,
  label,
  then,
  carry,
  quickTest,
}) {
  if (quickTest) return `${rowsWord(n)} run as a quick test`;
  if (!rest) return `All ${rowsWord(total)} rerun`;
  if (then)
    return `${rowsWord(n)} rerun with the fix, then the other ${rest} · one verdict on all ${total}`;
  return carry
    ? `${rowsWord(n)} rerun · ${rest} other ${rest === 1 ? "row keeps its" : "rows keep their"} ${label} results (not re-run)`
    : `${rowsWord(n)} rerun · the other ${rest} are left out of the new run`;
}

/** Column 2 · Rerun scope: `Rerun: Failed (13) ▾`, trials, what happens to the other rows, quick test. */
const ScopePane = ({ dock }) => {
  const {
    options,
    scopeType,
    draft,
    patch,
    label,
    sets,
    canEdit,
    quickTest,
    verifyPreview,
  } = dock;
  const trialChoices = [
    ...new Set([
      ...TRIAL_CHOICES,
      Math.min(MAX_DOCK_TRIALS, Number(draft.trials) || 1),
    ]),
  ].sort((a, b) => a - b);
  const picks = options.filter((o) => o.type !== "then");
  const help = picks.find((o) => o.type === scopeType)?.tip ?? null;
  const total = sets.all.length;
  const n = dock.rowIds?.length ?? 0;
  const rest = dock.others?.length ?? Math.max(0, total - n);
  const then = dock.then;
  const carry = draft.carryOver !== false;
  const canVerify = !quickTest && scopeType !== "all" && rest > 0 && n > 0;
  const sentence = scopeSentence({
    n,
    rest,
    total,
    label,
    then: Boolean(then),
    carry,
    quickTest,
  });
  return (
    <Stack spacing={1} sx={{ minWidth: 0 }} data-testid="iterate-scope">
      {/* IT-R5 G3 (P4): `Rerun: Failed (13) ▾`, one sentence that says
          what happens to every row, and `Also verify the other 17 rows`. */}
      {dock.matrixLoading ? (
        <Stack direction="row" gap={0.5} aria-label="Counting rows">
          {[52, 140].map((w) => (
            <Skeleton key={w} variant="rounded" width={w} height={28} />
          ))}
        </Stack>
      ) : (
        <Stack
          direction="row"
          alignItems="center"
          gap={1}
          flexWrap="wrap"
          sx={{ rowGap: 0.5 }}
        >
          <Typography typography="s2" color="text.secondary">
            Rerun:
          </Typography>
          <SmallSelect
            label="Rerun scope"
            value={scopeType}
            disabled={!canEdit}
            options={picks.map((o) => ({ value: o.type, label: o.label }))}
            onChange={(v) =>
              patch({
                scope: v,
                // Leaving `This row` drops the quick test it forced on.
                ...(scopeType === "row" && v !== "row"
                  ? { quickTest: false }
                  : {}),
              })
            }
            sx={{ minWidth: 150 }}
          />
          {help ? (
            <CustomTooltip show title={help} size="small" placement="top" arrow>
              <Iconify
                icon="mdi:information-outline"
                width={16}
                aria-label={help}
                sx={{ color: "text.disabled", cursor: "help" }}
              />
            </CustomTooltip>
          ) : null}
          <Stack direction="row" alignItems="center" gap={1} sx={{ ml: 1 }}>
            <Typography typography="s2" color="text.secondary">
              Trials
            </Typography>
            <CustomTooltip
              show={quickTest}
              title="A quick test runs 1 trial"
              size="small"
              arrow
            >
              <span>
                <SmallSelect
                  label="Trials"
                  value={quickTest ? 1 : draft.trials}
                  disabled={!canEdit || quickTest}
                  options={trialChoices.map((t) => ({
                    value: t,
                    label: String(t),
                  }))}
                  onChange={(v) => patch({ trials: v })}
                  sx={{ minWidth: 64 }}
                />
              </span>
            </CustomTooltip>
          </Stack>
        </Stack>
      )}
      {!dock.matrixLoading ? (
        <Typography
          typography="s2"
          data-testid="scope-sentence"
          sx={{ fontVariantNumeric: "tabular-nums" }}
        >
          {sentence}
          {!quickTest && !then && rest > 0 ? (
            <>
              {" "}
              <Link
                component="button"
                type="button"
                typography="s3"
                color="text.secondary"
                underline="always"
                disabled={!canEdit}
                onClick={() => patch({ carryOver: !carry })}
                data-testid="scope-carry-toggle"
                sx={{ verticalAlign: "baseline" }}
              >
                {carry ? "Leave them out" : `Keep their ${label} results`}
              </Link>
            </>
          ) : null}
        </Typography>
      ) : null}
      {canVerify ? (
        <FormControlLabel
          sx={{ m: 0, gap: 0.25, alignItems: "center", ml: -0.75 }}
          control={
            <Checkbox
              size="small"
              checked={Boolean(then)}
              disabled={!canEdit}
              onChange={(e) =>
                patch({
                  alsoVerify: e.target.checked,
                  ...(draft.scope === "then" ? { scope: "failed" } : {}),
                })
              }
              inputProps={{ "data-testid": "scope-also-verify" }}
            />
          }
          label={
            <Typography typography="s2">
              {`Also verify the other ${rest} ${rest === 1 ? "row" : "rows"} (+${fmtUsd(verifyPreview?.usd ?? 0)})`}
            </Typography>
          }
        />
      ) : null}
      {quickTest ? (
        <Typography typography="s3" color="text.secondary">
          Quick test: 1 trial, not listed on the leaderboard.
        </Typography>
      ) : null}
    </Stack>
  );
};

ScopePane.propTypes = { dock: PropTypes.object.isRequired };

export default ScopePane;
