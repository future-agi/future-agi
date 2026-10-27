import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import CountryCodeSelect from "../../components/CountryCodeSelect";
import { COUNTRY_BY_ISO } from "../../components/countryCodes";
import { phoneNumberError } from "../../components/phoneNumber";
import { INPUT_SX, ROW_BOX_SX, T12 } from "../drawerStyles";
import { Field, SegmentedToggle, ToggleRow } from "./formBits";

/** The contact number's problem, or null; an empty number says to add one. */
export const contactNumberError = (countryIso, number) =>
  number.trim()
    ? phoneNumberError(COUNTRY_BY_ISO[countryIso]?.dial, number)
    : "Add the number to call";

/** The country code picker with its label, at the drawer's input size. */
export function CountryCode({ value, onChange, width = 180 }) {
  return (
    <Box sx={{ width, flexShrink: 0 }}>
      <Typography sx={{ ...T12, fontWeight: 600, mb: 0.5 }}>
        Country Code
      </Typography>
      <Box
        sx={{
          ...INPUT_SX,
          "& .MuiInputBase-root": { minHeight: 28, py: "0 !important" },
        }}
      >
        <CountryCodeSelect value={value} onChange={onChange} />
      </Box>
    </Box>
  );
}
CountryCode.propTypes = {
  value: PropTypes.string,
  onChange: PropTypes.func.isRequired,
  width: PropTypes.number,
};

/**
 * How a voice agent is reached in simulation, as Vel's ContactInformation:
 * over the web or by a real call, and who speaks first.
 */
export default function VoiceContact({ form, set, phoneError }) {
  const phone = form.simMode === "phone";
  return (
    <Stack spacing={1}>
      <Stack
        direction="row"
        alignItems="center"
        spacing={1.5}
        sx={{ ...ROW_BOX_SX, px: 1.75, py: 1.25 }}
      >
        <Box flex={1} minWidth={0}>
          <Typography sx={{ ...T12, fontWeight: 600 }}>
            {phone ? "Telephony simulation (PSTN)" : "Web simulation (WebRTC)"}
          </Typography>
          <Typography sx={{ ...T12, color: "text.secondary", mt: 0.25 }}>
            {phone
              ? "Places a real call. Needs a telephony provider."
              : "No phone call or telephony provider needed."}
          </Typography>
        </Box>
        <SegmentedToggle
          label="Simulation"
          value={form.simMode}
          onChange={set("simMode")}
          options={[
            { value: "web", label: "Web" },
            { value: "phone", label: "Phone" },
          ]}
        />
      </Stack>
      {phone && (
        <>
          <Stack direction="row" spacing={1}>
            <CountryCode value={form.countryIso} onChange={set("countryIso")} />
            <Field
              label="Contact Number"
              required
              mono
              value={form.contactNumber}
              onChange={set("contactNumber")}
              placeholder="Number to call for the simulation"
              error={form.contactNumber.trim() ? phoneError : null}
              sx={{ flex: 1 }}
            />
          </Stack>
          <ToggleRow
            title="Inbound Calls"
            body="Allows the agent to take inbound calls."
            checked={form.inboundCalls}
            onChange={set("inboundCalls")}
          />
        </>
      )}
      <ToggleRow
        title="Agent speaks first"
        body="The simulator waits for its greeting."
        checked={form.agentSpeaksFirst}
        onChange={set("agentSpeaksFirst")}
      />
    </Stack>
  );
}
VoiceContact.propTypes = {
  form: PropTypes.object.isRequired,
  set: PropTypes.func.isRequired,
  phoneError: PropTypes.string,
};
