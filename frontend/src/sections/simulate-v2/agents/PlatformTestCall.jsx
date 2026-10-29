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
import Iconify from "src/components/iconify";
import { StatusDot } from "../components/primitives";
import { COMPAT_STATUS } from "../_mock/compatibility";
import { assistantsFor } from "../_mock/connectAgent";
import { platformPreviewOf } from "../_mock/platformPreview";
import { testCallEstimateOf, usdRange } from "../_mock/pricing";

const MONO = "ui-monospace, Menlo, monospace";
const box = {
  border: "1px solid",
  borderColor: "divider",
  borderRadius: "8px",
};
const row = {
  px: 1.5,
  py: 0.75,
  "& + &": { borderTop: "1px solid", borderColor: "divider" },
};
const when = (iso) =>
  new Date(iso).toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });

/* What one test call shows, by channel: measured, untested, unavailable. */
const resultsOf = (mode, transfer, provider) => [
  {
    id: "transcript",
    label: "Transcript",
    status: "supported",
    line: "Every turn, both sides",
  },
  {
    id: "tools",
    label: "Tool calls",
    status: "supported",
    line: "check_availability answered by the test world",
  },
  {
    id: "audio",
    label: "Audio and timing",
    status: mode === "text" ? "unavailable" : "supported",
    line:
      mode === "text"
        ? "A text test has no audio — latency and interruptions aren't measured"
        : "Latency and interruptions measured on real audio",
  },
  {
    id: "transfer",
    label: "Transfer",
    status: mode === "phone" && transfer ? "supported" : "untested",
    line:
      mode === "phone" && transfer
        ? "Rang the test destination and connected"
        : "Not exercised — only a phone test that reaches a transfer proves it",
  },
  {
    id: "state",
    label: "State changes",
    status: "unavailable",
    line: `${provider} doesn't expose the records it writes in your account`,
  },
];

/**
 * Test call (roadmap platforms.md, Week 2): before a real call or message,
 * where it goes, consent for the platform actions that run for real, and
 * what it costs on your account — then what it showed, measured and not.
 * A text test never stands in for a phone transfer.
 */
export default function PlatformTestCall({ agent, onSaveValues }) {
  const v = agent?.values || {};
  const voice = v.agentType !== "chat";
  const provider = v.provider
    ? v.provider.charAt(0).toUpperCase() + v.provider.slice(1)
    : "The platform";
  const assistant = assistantsFor(v.provider).find(
    (a) => a.id === v.agentId,
  ) || { id: v.agentId, name: agent?.name };
  const preview = platformPreviewOf(v.provider, assistant, { voice });
  const risky = (preview?.tools || []).filter(
    (t) => t.handling === "live" && t.risky,
  );
  const phone = v.contact?.e164 || assistant.phone || null;
  const [mode, setMode] = useState(voice ? (phone ? "phone" : "web") : "text");
  const [transferTo, setTransferTo] = useState(false);
  const [consent, setConsent] = useState(false);
  const [calling, setCalling] = useState(false);
  const last = v.platformTest || null;
  const cost = testCallEstimateOf(v.provider);
  const destination = {
    phone: `Calls ${phone}`,
    web: "A web call — no phone line",
    text: "A chat session",
  }[mode];

  const call = () => {
    setCalling(true);
    setTimeout(() => {
      setCalling(false);
      setConsent(false);
      onSaveValues(
        {
          platformTest: {
            at: new Date().toISOString(),
            mode,
            transfer: mode === "phone" && transferTo ? "tested" : "untested",
            destination:
              mode === "phone" && transferTo
                ? "+1 415 555 0100 (test line)"
                : null,
            results: resultsOf(mode, transferTo, provider),
          },
        },
        "Test call finished — what it measured is on the agent's Compatibility tab too.",
      );
    }, 1400);
  };

  return (
    <Box sx={box}>
      <Stack spacing={1.25} sx={{ px: 1.5, py: 1.25 }}>
        <Stack direction="row" alignItems="center" spacing={1.25}>
          <Typography sx={{ typography: "s3", fontWeight: 600, width: 90 }}>
            Channel
          </Typography>
          <Select
            size="small"
            value={mode}
            onChange={(e) => setMode(e.target.value)}
            inputProps={{ "aria-label": "Test channel" }}
            sx={{
              typography: "s2",
              minWidth: 180,
              "& .MuiSelect-select": { py: 0.5 },
            }}
          >
            {voice && phone && (
              <MenuItem value="phone" sx={{ typography: "s2" }}>
                Phone call
              </MenuItem>
            )}
            {voice && (
              <MenuItem value="web" sx={{ typography: "s2" }}>
                Web call
              </MenuItem>
            )}
            <MenuItem value="text" sx={{ typography: "s2" }}>
              Text
            </MenuItem>
          </Select>
          <Typography sx={{ typography: "s3", color: "text.secondary" }}>
            {destination}
          </Typography>
        </Stack>
        {mode === "phone" && risky.length > 0 && (
          <Stack
            direction="row"
            alignItems="center"
            spacing={0.75}
            onClick={() => setTransferTo((x) => !x)}
            sx={{ cursor: "pointer" }}
          >
            <Checkbox
              size="small"
              checked={transferTo}
              tabIndex={-1}
              sx={{ p: 0 }}
              inputProps={{ "aria-label": "Try a transfer" }}
            />
            <Typography sx={{ typography: "s2" }}>
              Ask for a person, to try the transfer — it rings the test line{" "}
              <Box component="span" sx={{ fontFamily: MONO }}>
                +1 415 555 0100
              </Box>
              , not a real team
            </Typography>
          </Stack>
        )}
        <Stack direction="row" alignItems="center" spacing={1.25}>
          <Typography sx={{ typography: "s3", fontWeight: 600, width: 90 }}>
            Cost
          </Typography>
          <Typography sx={{ typography: "s2", color: "text.secondary" }}>
            {cost
              ? `${usdRange(cost.low, cost.high)} on your ${cost.label} account · ${cost.source}`
              : `On your ${provider} account — no public price to estimate from`}
          </Typography>
        </Stack>
        <Stack
          direction="row"
          alignItems="flex-start"
          spacing={0.75}
          onClick={() => setConsent((x) => !x)}
          sx={{ cursor: "pointer" }}
        >
          <Checkbox
            size="small"
            checked={consent}
            tabIndex={-1}
            sx={{ p: 0, mt: "1px" }}
            inputProps={{ "aria-label": "Consent to the test call" }}
          />
          <Typography sx={{ typography: "s2" }}>
            {`I'm OK placing this ${voice && mode !== "text" ? "call" : "conversation"}.${risky.length ? ` Platform actions run for real: ${risky.map((t) => t.name).join(", ")}${mode === "phone" && transferTo ? " (to the test line)" : " (not reached unless asked)"}.` : ""}`}
          </Typography>
        </Stack>
        <Box>
          <Button
            size="small"
            variant="contained"
            color="primary"
            disabled={!consent || calling}
            onClick={call}
            startIcon={
              <Iconify
                icon={
                  calling
                    ? "svg-spinners:180-ring"
                    : "solar:phone-calling-linear"
                }
                width={14}
              />
            }
          >
            {calling
              ? "Calling…"
              : `Make test ${voice && mode !== "text" ? "call" : "conversation"}`}
          </Button>
        </Box>
      </Stack>
      {last && (
        <Box sx={{ borderTop: "1px solid", borderColor: "divider" }}>
          <Typography
            sx={{ px: 1.5, pt: 1, typography: "s3", color: "text.tertiary" }}
          >
            {`Last test · ${when(last.at)} · ${{ phone: "phone call", web: "web call", text: "text" }[last.mode]}`}
          </Typography>
          <Box sx={{ pb: 0.5 }}>
            {last.results.map((r) => (
              <Stack
                key={r.id}
                direction="row"
                alignItems="center"
                spacing={1.25}
                sx={row}
              >
                <Typography
                  sx={{ typography: "s2", width: 140, flexShrink: 0 }}
                >
                  {r.label}
                </Typography>
                <Stack
                  direction="row"
                  alignItems="center"
                  spacing={0.5}
                  sx={{ width: 110, flexShrink: 0 }}
                >
                  <StatusDot
                    status={COMPAT_STATUS[r.status].dot}
                    size={6}
                    live={false}
                  />
                  <Typography
                    sx={{ typography: "s2", color: "text.secondary" }}
                  >
                    {r.status === "supported"
                      ? "Measured"
                      : COMPAT_STATUS[r.status].label}
                  </Typography>
                </Stack>
                <Typography
                  sx={{
                    typography: "s3",
                    color: "text.tertiary",
                    flex: 1,
                    minWidth: 0,
                  }}
                >
                  {r.line}
                </Typography>
              </Stack>
            ))}
          </Box>
        </Box>
      )}
    </Box>
  );
}
PlatformTestCall.propTypes = {
  agent: PropTypes.object.isRequired,
  /* (patch, message) — saves to the agent's values. */
  onSaveValues: PropTypes.func.isRequired,
};
