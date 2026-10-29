import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";
import { SectionCard } from "../components/primitives";
import { twinById } from "../_mock/twins";

const PEOPLE = [
  {
    name: "Dana Liu",
    handle: "dana",
    email: "dana.liu@acme.test",
    crm: "C-881",
  },
  {
    name: "Arjun Mehta",
    handle: "arjun",
    email: "arjun.mehta@acme.test",
    crm: "C-902",
  },
  {
    name: "Priya Shah",
    handle: "priya",
    email: "priya.shah@acme.test",
    crm: "C-913",
  },
];

/* How one person appears in a service. */
const idIn = (service, p) => {
  if (/slack|discord|teams/.test(service)) return `@${p.handle}`;
  if (/crm|salesforce|hubspot/.test(service)) return `contact ${p.crm}`;
  if (/gmail|calendar|outlook|drive|docs|sheets|notion/.test(service))
    return p.email;
  return p.handle;
};

/**
 * One identity map across an app world's services (roadmap actors.md,
 * Week 2): the same person is the email sender, the CRM contact and the
 * calendar attendee, so a workflow that crosses apps meets one person —
 * not three records to rebuild by hand.
 */
export default function IdentityMapCard({ envState }) {
  const services = envState?.twinBacking?.services || [];
  if (services.length < 2) return null;
  const cols = services.map((id) => twinById(id)?.name || id);
  const grid = `minmax(140px, 1fr) ${cols.map(() => "minmax(120px, 1fr)").join(" ")}`;
  return (
    <SectionCard
      title="Shared identities"
      subtitle="The same people across every app in this world — one identity map, reset with the world."
      sx={{ mb: 2 }}
    >
      <Box sx={{ overflowX: "auto" }}>
        <Box
          sx={{
            display: "grid",
            gridTemplateColumns: grid,
            gap: 1.5,
            px: 2,
            py: 0.75,
            borderBottom: "1px solid",
            borderColor: "divider",
          }}
        >
          {["Person", ...cols].map((h) => (
            <Typography
              key={h}
              sx={{ typography: "s3", color: "text.tertiary" }}
            >
              {h}
            </Typography>
          ))}
        </Box>
        {PEOPLE.map((p) => (
          <Box
            key={p.name}
            sx={{
              display: "grid",
              gridTemplateColumns: grid,
              gap: 1.5,
              px: 2,
              py: 0.875,
              "& + &": { borderTop: "1px solid", borderColor: "divider" },
            }}
          >
            <Typography sx={{ typography: "s2", fontWeight: 600 }}>
              {p.name}
            </Typography>
            {services.map((s) => (
              <Typography
                key={s}
                noWrap
                sx={{
                  typography: "s2",
                  fontFamily: "ui-monospace, Menlo, monospace",
                }}
              >
                {idIn(s, p)}
              </Typography>
            ))}
          </Box>
        ))}
      </Box>
    </SectionCard>
  );
}

IdentityMapCard.propTypes = { envState: PropTypes.object };
