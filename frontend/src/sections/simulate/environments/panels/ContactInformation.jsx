import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography, Switch } from "@mui/material";
import Field from "../components/Field";
import CountryCodeSelect from "../components/CountryCodeSelect";

/*
  Voice contact details — how the test call reaches the agent. `mode` is web
  (WebRTC, no telephony provider) or phone (PSTN, needs a country code + number).
  `phoneOnly` (Others) has no in-browser WebRTC target, so it collapses to phone
  and hides the mode header. `inboundCalls` mirrors the old call-direction binary
  (on = agent takes inbound); `agentSpeaksFirst` tells the simulator to wait for
  the agent's greeting before replying.

  NOTE: most of this is UI-only today — the backend has no WebRTC/PSTN field,
  telephony dialing is runtime-not-ready, and call_direction / agent-speaks-first
  are inert/absent server-side. See the Hosted-platform plan's backend gaps.
*/
export default function ContactInformation({
  mode,
  onMode,
  countryIso,
  onCountryIso,
  contactNumber,
  onContactNumber,
  inboundCalls,
  onInboundCalls,
  agentSpeaksFirst,
  onAgentSpeaksFirst,
  phoneOnly = false,
}) {
  const effectiveMode = phoneOnly ? "phone" : mode;
  const header =
    effectiveMode === "phone"
      ? {
          title: "Telephony simulation (PSTN)",
          body: "A real phone call is placed over PSTN — requires a configured telephony provider.",
        }
      : {
          title: "Web simulation (WebRTC)",
          body: "No phone call is placed and no telephony provider is needed.",
        };

  return (
    <Stack spacing={1}>
      {!phoneOnly && (
        <Stack
          direction="row"
          alignItems="center"
          spacing={1.5}
          sx={{ px: 1.75, py: 1.25, borderRadius: 1, border: "1px solid", borderColor: "divider" }}
        >
          <Box sx={{ flex: 1, minWidth: 0 }}>
            <Typography sx={{ typography: "s2", fontWeight: 700 }}>{header.title}</Typography>
            <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.25 }}>
              {header.body}
            </Typography>
          </Box>
          <SegmentedToggle
            value={mode}
            onChange={onMode}
            options={[
              { value: "web", label: "Web" },
              { value: "phone", label: "Phone" },
            ]}
          />
        </Stack>
      )}

      {effectiveMode === "phone" && (
        <Stack direction={{ xs: "column", sm: "row" }} spacing={1}>
          <Box sx={{ width: { xs: "100%", sm: 180 } }}>
            <Typography sx={{ typography: "s3", fontWeight: 600, mb: 0.5 }}>Country Code</Typography>
            <CountryCodeSelect value={countryIso} onChange={onCountryIso} />
          </Box>
          <Box sx={{ flex: 1 }}>
            <Field
              label="Contact Number"
              required
              placeholder="Number to call for the simulation"
              value={contactNumber}
              onChange={onContactNumber}
              mono
            />
          </Box>
        </Stack>
      )}

      {/* A phone call always runs inbound — the platform dials the agent — so
          the switch is shown locked on rather than offering a choice the
          backend overrides. */}
      {effectiveMode === "phone" && (
        <ToggleRow
          checked
          disabled
          onChange={onInboundCalls}
          title="Inbound Calls"
          body="The platform calls your agent's number, so phone calls are always inbound."
        />
      )}

      <ToggleRow
        checked={agentSpeaksFirst}
        onChange={onAgentSpeaksFirst}
        title="Agent speaks first"
        body="Turn on if your agent greets first. The simulator waits for it before replying."
      />
    </Stack>
  );
}
ContactInformation.propTypes = {
  mode: PropTypes.oneOf(["web", "phone"]),
  onMode: PropTypes.func,
  countryIso: PropTypes.string,
  onCountryIso: PropTypes.func,
  contactNumber: PropTypes.string,
  onContactNumber: PropTypes.func,
  inboundCalls: PropTypes.bool,
  onInboundCalls: PropTypes.func,
  agentSpeaksFirst: PropTypes.bool,
  onAgentSpeaksFirst: PropTypes.func,
  phoneOnly: PropTypes.bool,
};

// Two-value segmented control; the active pill reuses the tinted selected
// surface the ChipCards use so it doesn't read as a foreign primitive.
function SegmentedToggle({ value, onChange, options }) {
  return (
    <Stack
      direction="row"
      sx={{
        display: "inline-flex",
        alignSelf: "flex-start",
        p: 0.375,
        borderRadius: 1,
        border: "1px solid",
        borderColor: "divider",
        bgcolor: (th) => alpha(th.palette.text.primary, th.palette.mode === "dark" ? 0.03 : 0.02),
      }}
    >
      {options.map((o) => {
        const on = value === o.value;
        return (
          <Box
            key={o.value}
            role="button"
            tabIndex={0}
            aria-pressed={on}
            onClick={() => onChange(o.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                onChange(o.value);
              }
            }}
            sx={{
              px: 1.75,
              py: 0.625,
              borderRadius: 0.75,
              cursor: "pointer",
              typography: "s2",
              fontWeight: 600,
              color: on ? "text.primary" : "text.subtitle",
              bgcolor: (th) =>
                on ? (th.palette.mode === "dark" ? alpha(th.palette.text.primary, 0.1) : "#fff") : "transparent",
              boxShadow: (th) =>
                on ? (th.palette.mode === "dark" ? "none" : `0 1px 2px ${alpha(th.palette.text.primary, 0.08)}`) : "none",
              transition: "background-color 120ms ease, color 120ms ease",
            }}
          >
            {o.label}
          </Box>
        );
      })}
    </Stack>
  );
}
SegmentedToggle.propTypes = {
  value: PropTypes.string,
  onChange: PropTypes.func,
  options: PropTypes.arrayOf(PropTypes.shape({ value: PropTypes.string, label: PropTypes.node })),
};

function ToggleRow({ checked, disabled = false, onChange, title, body }) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1.5}
      sx={{ px: 1.75, py: 1.25, borderRadius: 1, border: "1px solid", borderColor: "divider" }}
    >
      <Box sx={{ flex: 1, minWidth: 0 }}>
        <Typography sx={{ typography: "s2", fontWeight: 700 }}>{title}</Typography>
        <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.25 }}>{body}</Typography>
      </Box>
      <Switch
        size="small"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
        inputProps={{ "aria-label": typeof title === "string" ? title : undefined }}
      />
    </Stack>
  );
}
ToggleRow.propTypes = {
  checked: PropTypes.bool,
  disabled: PropTypes.bool,
  onChange: PropTypes.func,
  title: PropTypes.node,
  body: PropTypes.node,
};
