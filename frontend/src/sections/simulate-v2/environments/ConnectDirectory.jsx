import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { alpha } from "@mui/material/styles";
import { Box, Button, ButtonBase, InputBase, Stack, Tooltip, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { BrandMark, LogoTile, QuietTag } from "./LandingBits";
import { PANEL_SX, focusRing } from "./landingStyles";

/*
  Paste anything — we say what it is and open the right setup. The list of
  every way to connect is agents/connect/ConnectCatalog.
*/

/* ── paste anything ────────────────────────────────────────────────────── */

export function PasteBar({ onPick, detect, examples, title = "Start with anything you have", hint = "Paste a link, an ID or a sentence. We open the right setup.", placeholder }) {
  const [value, setValue] = useState("");
  const found = useMemo(() => detect(value), [detect, value]);
  const canGo = !!found && !found.blocked && !!found.choice;
  const go = () => { if (canGo) onPick(found.choice, found.prefill, { name: found.label, detail: found.detail, logo: found.logo, icon: found.icon }); };

  return (
    <Box sx={{ ...PANEL_SX, p: { xs: 2, md: 2.5 } }}>
      <Stack direction={{ xs: "column", sm: "row" }} justifyContent="space-between" alignItems={{ sm: "baseline" }} spacing={0.5} sx={{ mb: 1.5 }}>
        <Typography component="h2" sx={{ typography: "m3", fontWeight: 600 }}>{title}</Typography>
        <Typography sx={{ typography: "s2", color: "text.secondary" }}>{hint}</Typography>
      </Stack>
      <Stack direction={{ xs: "column", sm: "row" }} spacing={1}>
        <Box
          sx={{
            flex: 1, minWidth: 0, display: "flex", alignItems: "center", gap: 1, px: 1.5, height: 44,
            border: "1px solid", borderRadius: 1,
            borderColor: (t) => (found?.warn ? alpha(t.palette.error.main, 0.55) : t.palette.divider),
            bgcolor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.025 : 0.015),
            transition: "border-color .12s ease",
            "&:focus-within": { borderColor: (t) => (found?.warn ? t.palette.error.main : alpha(t.palette.text.primary, 0.45)) },
          }}
        >
          <Iconify icon="solar:clipboard-text-linear" width={16} sx={{ color: "text.disabled", flexShrink: 0 }} />
          <InputBase
            fullWidth
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") go(); if (e.key === "Escape") setValue(""); }}
            placeholder={placeholder || "Paste a GitHub repo, Vapi or Retell ID, MCP URL or endpoint — or describe the agent"}
            inputProps={{ "aria-label": "Paste a repository, ID, URL or description", spellCheck: false }}
            sx={{
              typography: "s1",
              fontFamily: value && found?.kind !== "describe" ? "ui-monospace, SFMono-Regular, Menlo, monospace" : undefined,
              "& input::placeholder": { color: "text.disabled", opacity: 1 },
            }}
          />
          {value && (
            <Tooltip arrow title="Clear">
              <ButtonBase onClick={() => setValue("")} aria-label="Clear" sx={{ borderRadius: 0.75, p: 0.25, color: "text.disabled", "&:hover": { color: "text.primary" } }}>
                <Iconify icon="eva:close-fill" width={16} />
              </ButtonBase>
            </Tooltip>
          )}
        </Box>
        <Button
          variant="contained" color="primary" disabled={!canGo} onClick={go}
          endIcon={<Iconify icon="solar:arrow-right-linear" width={14} />}
          sx={{ height: 44, px: 2.25, typography: "s1", fontWeight: 700, flexShrink: 0 }}
        >
          Continue
        </Button>
      </Stack>

      {/* What the paste was read as; with `examples`, a Try row until then. */}
      {(found || examples?.length > 0) && (
      <Box sx={{ minHeight: 34, mt: 1.25 }}>
        {found ? <Detection found={found} /> : (
          <Stack direction="row" alignItems="center" spacing={0.75} useFlexGap flexWrap="wrap">
            <Typography sx={{ typography: "s2", color: "text.secondary", mr: 0.25 }}>Try</Typography>
            {examples.map((ex) => (
              <ButtonBase
                key={ex.id}
                onClick={() => setValue(ex.value)}
                sx={{
                  gap: 0.75, px: 1, height: 26, borderRadius: 0.75,
                  border: "1px solid", borderColor: "divider",
                  typography: "s2", fontWeight: 600, color: "text.secondary",
                  transition: "color .12s ease, border-color .12s ease",
                  "&:hover": { color: "text.primary", borderColor: (t) => alpha(t.palette.text.primary, 0.3) },
                  "&.Mui-focusVisible": focusRing,
                }}
              >
                <BrandMark logo={ex.logo} icon={ex.icon} size={12} />
                {ex.label}
              </ButtonBase>
            ))}
          </Stack>
        )}
      </Box>
      )}
    </Box>
  );
}
PasteBar.propTypes = {
  onPick: PropTypes.func.isRequired, detect: PropTypes.func.isRequired,
  /* Sample pastes to try; left out where the connect list below already offers each one. */
  examples: PropTypes.array,
  title: PropTypes.node, hint: PropTypes.node, placeholder: PropTypes.string,
};

export function Detection({ found }) {
  return (
    <Stack direction="row" alignItems="center" spacing={1.25} sx={{ minWidth: 0 }}>
      {found.warn ? (
        <Box sx={{ width: 24, height: 24, display: "grid", placeItems: "center", flexShrink: 0, color: "error.main" }}>
          <Iconify icon={found.icon} width={18} />
        </Box>
      ) : <LogoTile logo={found.logo} icon={found.icon} size={24} />}
      <Box sx={{ minWidth: 0, flex: 1 }}>
        <Stack direction="row" alignItems="center" spacing={0.75} sx={{ minWidth: 0 }}>
          <Typography sx={{ typography: "s2", fontWeight: 700, flexShrink: 0 }}>{found.label}</Typography>
          {found.detail && (
            <Typography noWrap sx={{ typography: "s2", color: "text.secondary", fontFamily: found.kind === "describe" ? undefined : "ui-monospace, SFMono-Regular, Menlo, monospace", minWidth: 0 }}>
              {found.detail}
            </Typography>
          )}
          {found.tag && <QuietTag>{found.tag}</QuietTag>}
        </Stack>
        <Typography sx={{ typography: "s2", color: "text.secondary" }}>{found.note}</Typography>
      </Box>
      {!found.blocked && (
        <Typography sx={{ typography: "s3", color: "text.disabled", flexShrink: 0, display: { xs: "none", md: "block" } }}>Press Enter</Typography>
      )}
    </Stack>
  );
}
Detection.propTypes = { found: PropTypes.object.isRequired };
