import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { alpha } from "@mui/material/styles";
import { Box, ButtonBase, InputBase, MenuItem, Popover, Select, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { branchesOf, codeRefOf, commitsOn } from "../_mock/codeRefs";
import { MONO, ago } from "./agentViewUtils";
import { clickable, focusRing } from "../components/clickable";

/**
 * Which version of a code agent you're looking at, picked the way Git names
 * it: a branch, then a commit on it — each commit titled with its version
 * number, like every agent's. "v5 · main @ 3f9a2c1 · in production".
 *
 * The release branch comes first; a fix waiting as a draft pull request is
 * its own branch (future-agi/fix-…) until it's merged. Commits list newest
 * first with their message, author and time, and a search box narrows them
 * by message, hash or author. Picking one only changes what this page
 * shows — environments choose their own default.
 */
export default function CommitPicker({ agent, value, production, onChange }) {
  const [anchor, setAnchor] = useState(null);
  const current = codeRefOf(agent, agent.versions.find((v) => v.label === value));
  const branches = useMemo(() => branchesOf(agent), [agent]);
  const [branch, setBranch] = useState(current?.branch || branches[0]?.name);
  const [query, setQuery] = useState("");
  const commits = useMemo(() => commitsOn(agent, branch), [agent, branch]);
  const q = query.trim().toLowerCase();
  const shown = q ? commits.filter((c) => `${c.label} ${c.sha} ${c.message} ${c.author}`.toLowerCase().includes(q)) : commits;
  const head = commits[0]?.label;

  const open = (e) => { setBranch(current?.branch || branches[0]?.name); setQuery(""); setAnchor(e.currentTarget); };
  const pick = (label) => { onChange(label); setAnchor(null); };
  const tagOf = (label) => (label === production ? "in production" : null);

  return (
    <>
      <ButtonBase
        onClick={open}
        aria-haspopup="dialog"
        sx={{
          height: 32, px: 1.25, gap: 0.75, borderRadius: 1, border: "1px solid", borderColor: "divider",
          typography: "s2", "&:hover": { borderColor: "text.disabled" }, "&.Mui-focusVisible": focusRing,
        }}
      >
        <Iconify icon="solar:branching-paths-up-linear" width={14} sx={{ color: "text.secondary" }} />
        <Box component="span" sx={{ fontFamily: MONO, fontWeight: 600 }}>{current ? `${value} · ${current.branch} @ ${current.sha}` : value}</Box>
        {tagOf(value) && <Box component="span" sx={{ color: "text.subtitle" }}>{`· ${tagOf(value)}`}</Box>}
        {current?.draft && <Box component="span" sx={{ color: "text.subtitle" }}>· draft</Box>}
        <Iconify icon="solar:alt-arrow-down-linear" width={14} sx={{ color: "text.secondary" }} />
      </ButtonBase>

      <Popover
        open={!!anchor} anchorEl={anchor} onClose={() => setAnchor(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }} transformOrigin={{ vertical: "top", horizontal: "right" }}
        slotProps={{ paper: { sx: { mt: 0.75, width: 460, maxWidth: "calc(100vw - 32px)", borderRadius: 1.5, border: "1px solid", borderColor: "divider", backgroundImage: "none" } } }}
      >
        <Stack spacing={1} sx={{ p: 1.5, borderBottom: "1px solid", borderColor: "divider" }}>
          <Stack direction="row" alignItems="center" spacing={1}>
            <Typography sx={{ typography: "s3", fontWeight: 700, color: "text.subtitle", width: 56, flexShrink: 0 }}>Branch</Typography>
            <Select
              size="small" fullWidth value={branch}
              onChange={(e) => { setBranch(e.target.value); setQuery(""); }}
              renderValue={(b) => <Box component="span" sx={{ fontFamily: MONO }}>{b}</Box>}
              sx={{ typography: "s2", height: 32 }}
            >
              {branches.map((b) => (
                <MenuItem key={b.name} value={b.name} sx={{ typography: "s2", gap: 1 }}>
                  <Box component="span" sx={{ fontFamily: MONO, flex: 1 }}>{b.name}</Box>
                  <Box component="span" sx={{ color: "text.subtitle" }}>
                    {[b.release ? "release branch" : "draft pull request", `${b.count} commit${b.count === 1 ? "" : "s"}`].join(" · ")}
                  </Box>
                </MenuItem>
              ))}
            </Select>
          </Stack>
          <Box
            sx={{
              display: "flex", alignItems: "center", gap: 0.75, px: 1, height: 32,
              border: "1px solid", borderColor: "divider", borderRadius: 1,
              "&:focus-within": { borderColor: (t) => alpha(t.palette.text.primary, 0.45) },
            }}
          >
            <Iconify icon="solar:magnifer-linear" width={14} sx={{ color: "text.disabled", flexShrink: 0 }} />
            <InputBase
              autoFocus fullWidth value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && shown.length === 1) pick(shown[0].label); }}
              placeholder="Search version, commit message, hash or author"
              inputProps={{ "aria-label": "Search commits", spellCheck: false }}
              sx={{ typography: "s2", "& input::placeholder": { color: "text.disabled", opacity: 1 } }}
            />
          </Box>
        </Stack>

        <Box sx={{ maxHeight: 360, overflowY: "auto", py: 0.5 }}>
          {shown.length ? shown.map((c) => {
            const on = c.label === value;
            const tags = [c.label === head && "latest", tagOf(c.label)].filter(Boolean);
            return (
              <Stack
                key={c.label}
                direction="row" alignItems="flex-start" spacing={1.25}
                {...clickable(() => pick(c.label), { role: "radio", checked: on })}
                sx={{
                  ...focusRing,
                  px: 1.5, py: 1, cursor: "pointer",
                  bgcolor: (t) => (on ? alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.08 : 0.05) : "transparent"),
                  "&:hover": { bgcolor: "action.hover" },
                }}
              >
                <Iconify icon={on ? "solar:check-circle-bold" : "solar:circle-linear"} width={14} sx={{ mt: 0.375, color: on ? "text.primary" : "text.disabled", flexShrink: 0 }} />
                <Box flex={1} minWidth={0}>
                  <Stack direction="row" alignItems="baseline" spacing={1}>
                    <Typography sx={{ typography: "s2", fontFamily: MONO, fontWeight: 600, flexShrink: 0 }}>
                      {c.label}
                      <Box component="span" sx={{ fontWeight: 400, color: "text.secondary" }}>{` · ${c.sha}`}</Box>
                    </Typography>
                    <Typography noWrap sx={{ typography: "s2", flex: 1, minWidth: 0 }}>{c.message}</Typography>
                  </Stack>
                  <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>
                    {[c.author, c.at && ago(c.at), ...tags].filter(Boolean).join(" · ")}
                  </Typography>
                </Box>
              </Stack>
            );
          }) : (
            <Typography sx={{ px: 1.5, py: 1.5, typography: "s2", color: "text.subtitle" }}>
              {q ? `No commit on ${branch} matches “${query.trim()}”.` : `No commits on ${branch} yet.`}
            </Typography>
          )}
        </Box>
      </Popover>
    </>
  );
}

CommitPicker.propTypes = {
  /* A registry agent: { key, versions, … }. */
  agent: PropTypes.object.isRequired,
  /* The stored label of the version shown. */
  value: PropTypes.string,
  /* The label in production. */
  production: PropTypes.string,
  onChange: PropTypes.func.isRequired,
};
