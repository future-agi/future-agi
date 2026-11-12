import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Autocomplete,
  Chip,
  FormControlLabel,
  MenuItem,
  Radio,
  RadioGroup,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import {
  daysLabel,
  formSample as sampleOf,
  isEmail,
  parseEmails,
  perReviewerCounts,
  sampleLabel,
  sampleSize,
} from "./inviteModel";

const items = (n) => `${n} ${n === 1 ? "item" : "items"}`;

/**
 * Outside reviewers' invite form (W4-4): email chips, a seeded sample by
 * percent or count from all items, failing ones or ones two runs disagree
 * on, the same items for everyone or a split, and an expiry within the
 * organization's policy. Controlled: `value` / `onChange`.
 */
export default function InviteForm({ policy, filters, value, onChange }) {
  const [text, setText] = useState("");
  const set = (patch) => onChange({ ...value, ...patch });
  const current = filters.find((f) => f.key === value.filter) ?? filters[0];
  const pool = current?.count ?? 0;
  const k = sampleSize(pool, sampleOf(value));
  // shares go to the valid addresses only (an invalid one is refused)
  const valid = value.emails.filter(isEmail);
  const per = perReviewerCounts(k, valid.length, value.split);
  const addFrom = (raw) => {
    const { emails, invalid } = parseEmails(raw);
    const next = [
      ...value.emails,
      ...emails.filter((e) => !value.emails.includes(e)),
      ...invalid.filter((e) => !value.emails.includes(e)),
    ];
    if (next.length !== value.emails.length) set({ emails: next });
  };
  const days = policy?.expiry_days ?? [];
  return (
    <Stack spacing={1.5} data-testid="invite-form">
      <Autocomplete
        multiple
        freeSolo
        size="small"
        options={[]}
        value={value.emails}
        inputValue={text}
        onInputChange={(_, v, reason) => {
          if (reason === "reset") return setText("");
          if (/[\s,;]/.test(v)) {
            addFrom(v);
            setText("");
          } else setText(v);
          return undefined;
        }}
        onChange={(_, list) => {
          setText("");
          const { emails, invalid } = parseEmails(list.join(","));
          set({ emails: [...emails, ...invalid] });
        }}
        onBlur={() => {
          if (text.trim()) addFrom(text);
          setText("");
        }}
        renderTags={(list, getTagProps) =>
          list.map((email, i) => {
            const { key, ...rest } = getTagProps({ index: i });
            return (
              <Chip
                key={key}
                size="small"
                label={email}
                color={isEmail(email) ? "default" : "error"}
                variant="outlined"
                {...rest}
              />
            );
          })
        }
        renderInput={(params) => (
          <TextField
            {...params}
            label="Emails"
            placeholder={value.emails.length ? "" : "name@company.com"}
            inputProps={{ ...params.inputProps, "aria-label": "Emails" }}
          />
        )}
      />
      <Stack direction="row" gap={1} alignItems="center" flexWrap="wrap">
        <TextField
          select
          size="small"
          label="Items"
          value={value.mode}
          onChange={(e) =>
            set({
              mode: e.target.value,
              count:
                e.target.value === "count" && value.count === ""
                  ? String(
                      sampleSize(pool, {
                        mode: "percent",
                        value: value.percent,
                      }),
                    )
                  : value.count,
            })
          }
          sx={{ minWidth: 220 }}
          SelectProps={{
            renderValue: () => sampleLabel(pool, sampleOf(value)),
          }}
        >
          <MenuItem value="all">All {items(pool)}</MenuItem>
          <MenuItem value="percent">A random percentage</MenuItem>
          <MenuItem value="count">A random number of items</MenuItem>
        </TextField>
        {value.mode === "percent" ? (
          <TextField
            size="small"
            type="number"
            label="Percent"
            value={value.percent}
            onChange={(e) => set({ percent: e.target.value })}
            inputProps={{ min: 1, max: 100, "aria-label": "Percent" }}
            sx={{ width: 110 }}
          />
        ) : null}
        {value.mode === "count" ? (
          <TextField
            size="small"
            type="number"
            label="Items"
            value={value.count}
            onChange={(e) => set({ count: e.target.value })}
            inputProps={{ min: 1, max: pool, "aria-label": "Item count" }}
            sx={{ width: 110 }}
          />
        ) : null}
        <TextField
          select
          size="small"
          label="From"
          value={current?.key ?? "all"}
          onChange={(e) => set({ filter: e.target.value })}
          sx={{ minWidth: 220 }}
        >
          {filters.map((f) => (
            <MenuItem key={f.key} value={f.key} disabled={!f.count}>
              {f.label} ({f.count})
            </MenuItem>
          ))}
        </TextField>
        <TextField
          size="small"
          type="number"
          label="Seed"
          value={value.seed}
          onChange={(e) => set({ seed: e.target.value })}
          inputProps={{ "aria-label": "Seed" }}
          helperText="The same seed picks the same items"
          sx={{ width: 120 }}
        />
      </Stack>
      <Stack direction="row" gap={2} alignItems="center" flexWrap="wrap">
        <RadioGroup
          row
          value={value.split}
          onChange={(e) => set({ split: e.target.value })}
          aria-label="Each reviewer gets"
        >
          <Typography typography="s2" sx={{ mr: 1, alignSelf: "center" }}>
            Each reviewer gets
          </Typography>
          <FormControlLabel
            value="same"
            control={<Radio size="small" />}
            label={<Typography typography="s2">the same items</Typography>}
          />
          <FormControlLabel
            value="split"
            control={<Radio size="small" />}
            label={<Typography typography="s2">their own share</Typography>}
          />
        </RadioGroup>
        <TextField
          select
          size="small"
          label="Link expires in"
          value={value.days}
          onChange={(e) => set({ days: e.target.value })}
          sx={{ minWidth: 150 }}
        >
          {days.map((d) => (
            <MenuItem key={d} value={d}>
              {daysLabel(d)}
            </MenuItem>
          ))}
        </TextField>
      </Stack>
      {valid.length && k ? (
        <Typography
          typography="s2"
          color="text.secondary"
          data-testid="invite-preview"
        >
          {value.split === "split"
            ? `Shares of ${per.join(", ")} items from the ${items(k)} sampled.`
            : `Each reviewer gets the same ${items(k)}, so their agreement can be measured.`}
        </Typography>
      ) : null}
    </Stack>
  );
}

InviteForm.propTypes = {
  policy: PropTypes.object,
  // [{key, label, count}] from the server (summary) or R3 counts (Start review)
  filters: PropTypes.array.isRequired,
  value: PropTypes.object.isRequired,
  onChange: PropTypes.func.isRequired,
};
