import PropTypes from "prop-types";
import { useState } from "react";
import {
  Box,
  Button,
  ButtonBase,
  Checkbox,
  IconButton,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { neutralCheckboxSx } from "../runs/detail/trace/traceTable.constants";
import {
  PROPOSALS_COPY as COPY,
  PROPOSAL_KINDS,
  isPickable,
} from "./proposals";

const linkSx = {
  mt: 0.5,
  typography: "s3",
  color: "text.tertiary",
  textAlign: "left",
  display: "block",
  "&:hover": { color: "text.primary", textDecoration: "underline" },
};

/**
 * One proposed change: checkbox, what it adds, the passage it came from and
 * a link to that spot. Blocked items carry their way out (pick a side, supply
 * the records, leave the rows out); personal data carries its choice.
 */
export default function ProposalItem({
  item,
  inset,
  checked,
  hideCheckbox,
  edit,
  onToggle,
  onEdit,
  onOpenSource,
  onPick,
  onSupply,
  onLeaveOut,
  onChoose,
  personalReplaced,
}) {
  const [editing, setEditing] = useState(false);
  const pickable = isPickable(item);
  const kind = PROPOSAL_KINDS[item.kind];
  const ruleText = edit ?? item.rule ?? item.title.replace(/^Rule:\s*/, "");

  if (item.kind === "reference")
    return (
      <ReferenceItem
        item={item}
        inset={inset}
        onOpen={() => onOpenSource(item.cite?.sourceId, item.cite?.at)}
      />
    );

  return (
    <Stack
      direction="row"
      spacing={1.25}
      sx={{
        px: inset,
        py: 1.25,
        borderTop: "1px solid",
        borderColor: "divider",
        "& .pi-actions": { opacity: editing ? 1 : 0 },
        "&:hover .pi-actions, &:focus-within .pi-actions": { opacity: 1 },
      }}
    >
      <Checkbox
        size="small"
        checked={pickable && checked}
        disabled={!pickable}
        onChange={onToggle}
        inputProps={{ "aria-label": item.title }}
        sx={{
          ...neutralCheckboxSx,
          p: 0,
          mt: "1px",
          alignSelf: "flex-start",
          // A choice isn't a change to pick; Add and Reject decide it.
          ...(item.kind === "personal" && { visibility: "hidden" }),
          ...(hideCheckbox && { display: "none" }),
        }}
      />
      <Box sx={{ flex: 1, minWidth: 0 }}>
        <Stack direction="row" spacing={0.75} alignItems="flex-start">
          {item.blocking && kind && (
            <Iconify
              icon={kind.icon}
              width={14}
              sx={{ color: "status.warning.fg", flexShrink: 0, mt: "2px" }}
            />
          )}
          {/* The group heading names the kind, so a rule reads as the rule. */}
          <Typography
            sx={{ typography: "s1", fontWeight: 500, flex: 1, minWidth: 0 }}
          >
            {item.kind === "rule" ? ruleText : item.title}
          </Typography>
          {item.kind === "rule" && pickable && (
            <CustomTooltip show arrow describeChild title={COPY.editHint}>
              {editing ? (
                <Button
                  size="small"
                  variant="outlined"
                  onClick={() => setEditing(false)}
                  sx={{ minWidth: 0, py: 0, px: 1, flexShrink: 0 }}
                >
                  {COPY.editDone}
                </Button>
              ) : (
                <IconButton
                  size="small"
                  className="pi-actions"
                  aria-label={COPY.editRule}
                  onClick={() => setEditing(true)}
                  sx={{
                    p: 0.25,
                    color: "text.tertiary",
                    flexShrink: 0,
                    "&:hover": { color: "text.primary" },
                  }}
                >
                  <Iconify icon="solar:pen-linear" width={14} />
                </IconButton>
              )}
            </CustomTooltip>
          )}
          {item.inferred && (
            <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
              {COPY.inferred}
            </Typography>
          )}
        </Stack>
        {item.detail &&
          (item.blocking ||
            ["missing", "conflict", "unreadable"].includes(item.kind)) && (
            <Typography
              sx={{ typography: "s3", color: "text.secondary", mt: 0.25 }}
            >
              {item.detail}
            </Typography>
          )}
        {item.kind === "missing" && item.present !== false && (
          <Typography
            sx={{ typography: "s3", color: "text.secondary", mt: 0.25 }}
          >
            {item.affects?.length
              ? COPY.missingAffects(item.affects.length)
              : COPY.missingKept}
          </Typography>
        )}
        {editing && (
          <TextField
            size="small"
            fullWidth
            autoFocus
            label={COPY.ruleLabel}
            value={ruleText}
            onChange={(event) => onEdit(event.target.value)}
            onKeyDown={(event) => event.key === "Enter" && setEditing(false)}
            sx={{ mt: 0.75 }}
          />
        )}
        {personalReplaced && (
          <Typography
            sx={{ mt: 0.5, typography: "s3", color: "text.tertiary" }}
          >
            {COPY.personalReplaced}
          </Typography>
        )}
        {item.cite && (
          <>
            {item.cite.excerpt &&
              item.cite.excerpt.trim() !== String(item.rule || "").trim() &&
              item.kind !== "table" && (
                <Typography
                  sx={{
                    mt: 0.5,
                    pl: 1,
                    borderLeft: "1px solid",
                    borderColor: "divider",
                    typography: "s3",
                    color: "text.secondary",
                  }}
                >
                  {`“${item.cite.excerpt}”`}
                </Typography>
              )}
            <ButtonBase
              onClick={() => onOpenSource(item.cite.sourceId, item.cite.at)}
              sx={linkSx}
            >
              {COPY.from(item.cite.ref)}
            </ButtonBase>
          </>
        )}
        {item.kind === "conflict" && (
          <Stack spacing={0.5} sx={{ mt: 0.75 }} role="radiogroup">
            {item.options.map((option) => {
              const on = item.pick?.sourceId === option.sourceId;
              return (
                <ButtonBase
                  key={option.sourceId}
                  role="radio"
                  aria-checked={on}
                  onClick={() => onPick(option)}
                  sx={{
                    justifyContent: "flex-start",
                    gap: 0.75,
                    typography: "s3",
                    textAlign: "left",
                  }}
                >
                  <Iconify
                    icon={
                      on ? "solar:check-circle-bold" : "solar:circle-linear"
                    }
                    width={14}
                    sx={{ flexShrink: 0 }}
                  />
                  {COPY.conflictOption(option)}
                </ButtonBase>
              );
            })}
          </Stack>
        )}
        {item.kind === "missing" && (
          <Stack direction="row" spacing={1} sx={{ mt: 0.75 }}>
            <Button
              size="small"
              variant="outlined"
              onClick={(event) => onSupply(event.currentTarget)}
            >
              {COPY.supply}
            </Button>
            <Button size="small" variant="outlined" onClick={onLeaveOut}>
              {COPY.leaveOut}
            </Button>
          </Stack>
        )}
        {item.kind === "personal" && (
          <Box sx={{ mt: 0.75 }}>
            <PersonalDataChoice
              compact
              suggested={item.suggested}
              choice={item.choice}
              onChoose={onChoose}
            />
          </Box>
        )}
      </Box>
    </Stack>
  );
}

ProposalItem.propTypes = {
  item: PropTypes.shape({
    id: PropTypes.string.isRequired,
    kind: PropTypes.string.isRequired,
    title: PropTypes.string.isRequired,
    detail: PropTypes.string,
    rule: PropTypes.string,
    inferred: PropTypes.bool,
    blocking: PropTypes.bool,
    present: PropTypes.bool,
    affects: PropTypes.array,
    options: PropTypes.array,
    pick: PropTypes.object,
    suggested: PropTypes.string,
    choice: PropTypes.string,
    cite: PropTypes.shape({
      sourceId: PropTypes.string,
      ref: PropTypes.string,
      excerpt: PropTypes.string,
      at: PropTypes.string,
    }),
  }).isRequired,
  inset: PropTypes.number.isRequired,
  checked: PropTypes.bool,
  // One change only: Add and Reject already decide it.
  hideCheckbox: PropTypes.bool,
  // The rule's wording as edited, if it was.
  edit: PropTypes.string,
  onToggle: PropTypes.func.isRequired,
  onEdit: PropTypes.func.isRequired,
  // (sourceId, at): opens the file at the cited spot.
  onOpenSource: PropTypes.func.isRequired,
  onPick: PropTypes.func.isRequired,
  // Gets the button to anchor the Add source menu on.
  onSupply: PropTypes.func.isRequired,
  onLeaveOut: PropTypes.func.isRequired,
  onChoose: PropTypes.func.isRequired,
  personalReplaced: PropTypes.bool,
};

/*
  A whole file joining as background: what it is and how it's used, then
  what it says by section. No checkbox: Add and Reject decide it.
*/
function ReferenceItem({ item, inset, onOpen }) {
  const [all, setAll] = useState(false);
  const read = item.read || { sections: 0, words: 0, passages: [] };
  const rows = all ? read.passages : read.passages.slice(0, 6);
  const facts = [
    [
      COPY.reference.file,
      <ButtonBase
        key="file"
        onClick={onOpen}
        sx={{
          typography: "s1",
          fontWeight: 500,
          color: "text.primary",
          textDecoration: "underline",
          textUnderlineOffset: 3,
          textDecorationColor: (theme) => theme.palette.text.disabled,
          "&:hover": { textDecorationColor: "currentColor" },
        }}
      >
        {item.cite?.ref}
      </ButtonBase>,
    ],
    [COPY.reference.usedAs, COPY.reference.usedAsValue],
    [COPY.reference.rules, COPY.reference.rulesValue],
    [COPY.reference.read, COPY.reference.readValue(read)],
  ];
  return (
    <Box sx={{ px: inset, pb: 2 }}>
      <Box component="table" sx={{ borderCollapse: "collapse", mb: 1.5 }}>
        <Box component="tbody">
          {facts.map(([label, value]) => (
            <Box component="tr" key={label}>
              <Box
                component="td"
                sx={{
                  typography: "s1",
                  color: "text.tertiary",
                  width: 88,
                  py: 0.5,
                  pr: 1.5,
                  verticalAlign: "top",
                }}
              >
                {label}
              </Box>
              <Box
                component="td"
                sx={{ typography: "s1", color: "text.primary", py: 0.5 }}
              >
                {value}
              </Box>
            </Box>
          ))}
        </Box>
      </Box>
      {read.passages.length > 0 && (
        <>
          <Box
            sx={{
              display: "grid",
              gridTemplateColumns: "80px minmax(0, 1fr)",
              borderTop: "1px solid",
              borderColor: "divider",
              "& > *": {
                borderBottom: "1px solid",
                borderColor: "divider",
                py: 0.75,
              },
            }}
          >
            <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
              {COPY.reference.section}
            </Typography>
            <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
              {COPY.reference.says}
            </Typography>
            {rows.map((passage, n) => [
              <Typography
                key={`${passage.id}-where`}
                sx={{
                  typography: "s1",
                  color: "text.tertiary",
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                {n === 0 || rows[n - 1].where !== passage.where
                  ? passage.where.replace(/^section /, "")
                  : ""}
              </Typography>,
              <Typography
                key={`${passage.id}-text`}
                sx={{ typography: "s1", color: "text.secondary" }}
              >
                {passage.text}
              </Typography>,
            ])}
          </Box>
          {read.passages.length > 6 && (
            <ButtonBase
              onClick={() => setAll((value) => !value)}
              sx={{
                mt: 0.75,
                typography: "s1",
                fontWeight: 500,
                color: "text.secondary",
                textDecoration: "underline",
                textUnderlineOffset: 3,
              }}
            >
              {all
                ? COPY.reference.showFewer
                : COPY.reference.showAll(read.passages.length)}
            </ButtonBase>
          )}
        </>
      )}
    </Box>
  );
}

ReferenceItem.propTypes = {
  item: PropTypes.object.isRequired,
  inset: PropTypes.number.isRequired,
  onOpen: PropTypes.func.isRequired,
};

const CHOICES = ["keep", "replace", "none"];

// shortcut: the compact form only, until the full personal-data choice lands with the file's Personal data tab.
function PersonalDataChoice({ suggested = "replace", choice, onChoose }) {
  return (
    <Stack
      direction="row"
      role="radiogroup"
      aria-label={COPY.personal.label}
      spacing={1.5}
      useFlexGap
      flexWrap="wrap"
    >
      {CHOICES.map((id) => {
        const on = choice === id;
        return (
          <CustomTooltip
            key={id}
            show
            arrow
            describeChild
            title={COPY.personal[id].note}
          >
            <ButtonBase
              role="radio"
              aria-checked={on}
              onClick={() => onChoose(id)}
              sx={{ gap: 0.5, typography: "s3", py: 0.25 }}
            >
              <Iconify
                icon={on ? "solar:check-circle-bold" : "solar:circle-linear"}
                width={14}
              />
              <Box component="span" sx={{ fontWeight: on ? 600 : 400 }}>
                {COPY.personal[id].label}
                {id === suggested ? COPY.personal.suggested : ""}
              </Box>
            </ButtonBase>
          </CustomTooltip>
        );
      })}
    </Stack>
  );
}

PersonalDataChoice.propTypes = {
  suggested: PropTypes.string,
  choice: PropTypes.string,
  onChoose: PropTypes.func.isRequired,
  // Accepted so the full version can drop in; this one is always compact.
  compact: PropTypes.bool,
};
