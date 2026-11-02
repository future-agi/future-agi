import PropTypes from "prop-types";
import { useState } from "react";
import { enqueueSnackbar } from "notistack";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { alpha } from "@mui/material/styles";
import {
  Box,
  Button,
  Checkbox,
  FormControlLabel,
  Stack,
  Switch,
  TextField,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { extendHarnessJob } from "src/api/harness/harness";
import { harnessScenariosListKey } from "src/api/simulate-environments/scenariosHooks";
import SoonChip from "../../../components/SoonChip";
import { PACK_SIZE, RED_TEAM_PACKS, packGuidance } from "./redTeamPacks";

// Vel's coverage depths; the extend API adds at most 50 at a time.
const DEPTHS = [
  {
    id: "smoke",
    label: "Smoke",
    hint: "A quick check across the basics",
    size: 5,
  },
  {
    id: "standard",
    label: "Standard",
    hint: "A balanced mix, the default",
    size: 10,
  },
  {
    id: "deep",
    label: "Deep audit",
    hint: "Adds longer calls and poor call conditions",
    size: 48,
  },
];
export const MAX_EXTEND = 50;

const sectionLabel = {
  typography: "s3",
  fontWeight: 600,
  color: "text.subtitle",
  mb: 1,
};

// Vel's Generate route, sending one extend request: the depth (or a typed
// number) and each ticked red-team pack add up to the count, and the focus and
// the packs' asks become the guidance. The new scenarios join the list as the
// build writes them; there is no proposal step.
export default function GenerateRoute({ env, onClose }) {
  const queryClient = useQueryClient();
  const [depth, setDepth] = useState("standard");
  const [countText, setCountText] = useState("");
  const [focus, setFocus] = useState("");
  const [redTeam, setRedTeam] = useState(false);
  const [packs, setPacks] = useState(["baseline"]);

  const chosen = DEPTHS.find((d) => d.id === depth);
  const typed = Number.parseInt(countText, 10);
  const size = Number.isFinite(typed) && typed > 0 ? typed : chosen.size;
  const chosenPacks = redTeam
    ? RED_TEAM_PACKS.filter((p) => packs.includes(p.id))
    : [];
  const total = Math.min(MAX_EXTEND, size + chosenPacks.length * PACK_SIZE);
  const capped = size + chosenPacks.length * PACK_SIZE > MAX_EXTEND;

  const togglePack = (id) => {
    const next = packs.includes(id)
      ? packs.filter((p) => p !== id)
      : [...packs, id];
    setPacks(next);
    if (!next.length) setRedTeam(false);
  };

  const extend = useMutation({
    mutationFn: () =>
      extendHarnessJob(env.id, {
        count: total,
        guidance: [focus.trim(), ...packGuidance(chosenPacks)]
          .filter(Boolean)
          .join(" ")
          .slice(0, 2000),
        client_request_id: `add-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: harnessScenariosListKey(env.id),
      });
      enqueueSnackbar(
        `Adding ${total} scenario${total === 1 ? "" : "s"}. They arrive in the list as they're written.`,
        {
          variant: "success",
        },
      );
      onClose();
    },
    onError: (e) => {
      enqueueSnackbar(
        e?.response?.data?.message ||
          e?.message ||
          "Couldn't add scenarios. Try again.",
        { variant: "error" },
      );
    },
  });

  return (
    <Box sx={{ p: 3 }}>
      <Typography sx={sectionLabel}>Coverage depth</Typography>
      <Box
        role="radiogroup"
        sx={{
          display: "grid",
          gap: 1.5,
          gridTemplateColumns: { xs: "1fr", sm: "repeat(3, 1fr)" },
          mb: 2.5,
        }}
      >
        {DEPTHS.map((d) => {
          const active = d.id === depth;
          return (
            <Box
              key={d.id}
              role="radio"
              aria-checked={active}
              aria-label={d.label}
              tabIndex={0}
              onClick={() => {
                setDepth(d.id);
                setCountText("");
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  setDepth(d.id);
                  setCountText("");
                }
              }}
              sx={{
                p: 1.5,
                borderRadius: "8px",
                cursor: "pointer",
                border: "1px solid",
                borderColor: active ? "text.primary" : "divider",
                bgcolor: active
                  ? (t) => alpha(t.palette.primary.main, 0.08)
                  : "transparent",
                transition:
                  "border-color 0.15s ease, background-color 0.15s ease",
              }}
            >
              <Stack
                direction="row"
                alignItems="baseline"
                spacing={0.75}
                sx={{ mb: 0.5 }}
              >
                <Typography sx={{ typography: "s2", fontWeight: 600 }}>
                  {d.label}
                </Typography>
                <Typography
                  sx={{
                    typography: "s3",
                    color: "text.subtitle",
                    fontVariantNumeric: "tabular-nums",
                  }}
                >{`~${d.size}`}</Typography>
              </Stack>
              <Typography sx={{ typography: "s3", color: "text.secondary" }}>
                {d.hint}
              </Typography>
            </Box>
          );
        })}
      </Box>

      <Stack direction="row" alignItems="center" spacing={1.5} sx={{ mb: 3 }}>
        <TextField
          size="small"
          type="number"
          value={countText}
          onChange={(e) =>
            setCountText(e.target.value.replace(/[^0-9]/g, "").slice(0, 4))
          }
          placeholder={String(chosen.size)}
          inputProps={{ max: MAX_EXTEND, "aria-label": "How many scenarios" }}
          sx={{
            width: 96,
            "& .MuiInputBase-input": {
              typography: "s2",
              fontVariantNumeric: "tabular-nums",
            },
          }}
        />
        <Typography
          sx={{
            typography: "s3",
            color: capped ? "warning.main" : "text.subtitle",
          }}
        >
          {capped
            ? `Capped at ${MAX_EXTEND}. One request adds up to ${MAX_EXTEND} scenarios.`
            : `scenarios · one request adds up to ${MAX_EXTEND}`}
        </Typography>
      </Stack>

      <Typography component="label" htmlFor="add-focus" sx={sectionLabel}>
        Focus
      </Typography>
      <TextField
        id="add-focus"
        fullWidth
        size="small"
        value={focus}
        onChange={(e) => setFocus(e.target.value)}
        placeholder="Optional, e.g. angry callers with payment failures. More of the new scenarios will lean this way"
        inputProps={{ "aria-label": "Focus", maxLength: 1000 }}
        sx={{ mt: 1, mb: 3, "& .MuiInputBase-input": { typography: "s2" } }}
      />

      <Box
        sx={{
          p: 1.5,
          borderRadius: "8px",
          border: "1px solid",
          borderColor: "divider",
          mb: 3,
        }}
      >
        <FormControlLabel
          sx={{ m: 0, alignItems: "flex-start", width: "100%" }}
          control={
            <Switch
              checked={redTeam}
              onChange={(_, v) => {
                setRedTeam(v);
                if (v && !packs.length) setPacks(["baseline"]);
              }}
              inputProps={{
                role: "checkbox",
                "aria-label": "Safety & red-team tests",
              }}
              sx={{ mr: 1.25, mt: -0.5 }}
            />
          }
          label={
            <Box>
              <Stack direction="row" alignItems="center" spacing={1}>
                <Iconify
                  icon="solar:shield-warning-linear"
                  width={14}
                  sx={{ color: redTeam ? "error.main" : "text.subtitle" }}
                />
                <Typography sx={{ typography: "s2", fontWeight: 600 }}>
                  Safety & red-team tests
                </Typography>
                <SoonChip
                  label="Must-pass"
                  what="Red-team scenarios written as must-pass"
                  size="tiny"
                />
              </Stack>
              <Typography
                sx={{ typography: "s3", color: "text.secondary", mt: 0.5 }}
              >
                Adds callers who try to misuse the agent, on top of the
                scenarios above.
              </Typography>
            </Box>
          }
        />
        {redTeam && (
          <Stack spacing={0.25} sx={{ mt: 1.5, pl: 5.5 }}>
            {RED_TEAM_PACKS.map((p) => (
              <FormControlLabel
                key={p.id}
                sx={{ m: 0, alignItems: "flex-start" }}
                control={
                  <Checkbox
                    size="small"
                    checked={packs.includes(p.id)}
                    onChange={() => togglePack(p.id)}
                    inputProps={{ "aria-label": p.label }}
                    sx={{ p: 0.5, mr: 0.75 }}
                  />
                }
                label={
                  <Box sx={{ pt: 0.25 }}>
                    <Typography sx={{ typography: "s2" }}>
                      {p.label}
                      <Box
                        component="span"
                        sx={{
                          color: "text.subtitle",
                          fontVariantNumeric: "tabular-nums",
                        }}
                      >{` · ${PACK_SIZE}`}</Box>
                    </Typography>
                    <Typography
                      sx={{ typography: "s3", color: "text.subtitle" }}
                    >
                      {p.hint}
                    </Typography>
                  </Box>
                }
              />
            ))}
          </Stack>
        )}
      </Box>

      <Stack direction="row" alignItems="center" spacing={2}>
        <Button
          size="small"
          variant="contained"
          color="primary"
          onClick={() => extend.mutate()}
          disabled={extend.isPending || !env?.id}
          startIcon={<Iconify icon="solar:magic-stick-3-bold" width={14} />}
        >
          {extend.isPending
            ? "Asking the builder…"
            : `Generate ${total} scenario${total === 1 ? "" : "s"}`}
        </Button>
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
          {`${Math.min(size, MAX_EXTEND)} at ${chosen.label.toLowerCase()} depth${chosenPacks
            .map(
              (p) =>
                ` + ${PACK_SIZE} ${p.id === "baseline" ? "baseline" : p.label.toLowerCase()}`,
            )
            .join("")}. They arrive in the list as they're written.`}
        </Typography>
      </Stack>
    </Box>
  );
}

GenerateRoute.propTypes = {
  env: PropTypes.shape({ id: PropTypes.string }).isRequired,
  onClose: PropTypes.func.isRequired,
};
