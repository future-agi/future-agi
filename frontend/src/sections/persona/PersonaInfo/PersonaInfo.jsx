import PropTypes from "prop-types";
import {
  Box,
  Button,
  IconButton,
  Stack,
  Tooltip,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import {
  callRowsOf,
  chatRowsOf,
  isVoicePersona,
  talkRowsOf,
  usageLineOf,
  whoRowsOf,
} from "./personaInfoRows";

/**
 * One persona, as a spec sheet: who it is, how it talks, and how it behaves
 * on a call or in a chat — each a short key-value table, every value said in
 * words. The header carries what you'd do with it (use it in an environment,
 * duplicate it, edit your own) and where it's already used.
 */
const PersonaInfo = ({
  persona,
  usage,
  onUse,
  onDuplicate,
  onEdit,
  onClose,
}) => {
  const voice = isVoicePersona(persona);
  const builtIn = !!persona?.isDefault;
  const meta = [
    voice ? "Voice" : "Chat",
    builtIn ? "Future AGI built" : "Custom",
    usage !== undefined ? usageLineOf(usage) : null,
  ].filter(Boolean);

  return (
    <Stack sx={{ height: "100%" }}>
      {/* ── header ── */}
      <Box
        sx={{
          px: 2.5,
          pt: 2,
          pb: 2,
          borderBottom: "1px solid",
          borderColor: "divider",
          flexShrink: 0,
        }}
      >
        <Stack direction="row" alignItems="flex-start" spacing={1.5}>
          <Box
            sx={{
              width: 32,
              height: 32,
              borderRadius: "50%",
              flexShrink: 0,
              display: "grid",
              placeItems: "center",
              border: "1px solid",
              borderColor: "divider",
              color: "text.secondary",
            }}
          >
            <Iconify
              icon={
                voice
                  ? "solar:microphone-3-linear"
                  : "solar:chat-round-line-linear"
              }
              width={16}
            />
          </Box>
          <Box flex={1} minWidth={0}>
            <Typography sx={{ typography: "m3", fontWeight: 600 }}>
              {persona?.name || "Persona"}
            </Typography>
            <Typography
              sx={{ typography: "s3", color: "text.subtitle", mt: 0.25 }}
            >
              {meta.join(" · ")}
            </Typography>
          </Box>
          <IconButton size="small" onClick={onClose} aria-label="Close">
            <Iconify
              icon="solar:close-linear"
              width={20}
              sx={{ color: "text.secondary" }}
            />
          </IconButton>
        </Stack>
        {persona?.description && (
          <Typography
            sx={{ typography: "s2", color: "text.secondary", mt: 1.5 }}
          >
            {persona.description}
          </Typography>
        )}
        {(onUse || onDuplicate || onEdit) && (
          <Stack direction="row" spacing={1} sx={{ mt: 1.75 }}>
            {onUse && (
              <Button
                size="small"
                variant="contained"
                color="primary"
                onClick={onUse}
                startIcon={<Iconify icon="solar:add-linear" width={14} />}
                sx={{ typography: "s2" }}
              >
                Use in an environment
              </Button>
            )}
            {onDuplicate && (
              <Tooltip
                arrow
                title={builtIn ? "Make your own copy to change" : ""}
              >
                <Button
                  size="small"
                  variant="outlined"
                  onClick={onDuplicate}
                  startIcon={<Iconify icon="solar:copy-linear" width={14} />}
                  sx={{
                    typography: "s2",
                    color: "text.primary",
                    borderColor: "divider",
                  }}
                >
                  Duplicate
                </Button>
              </Tooltip>
            )}
            {onEdit && !builtIn && (
              <Button
                size="small"
                variant="outlined"
                onClick={onEdit}
                startIcon={<Iconify icon="solar:pen-linear" width={14} />}
                sx={{
                  typography: "s2",
                  color: "text.primary",
                  borderColor: "divider",
                }}
              >
                Edit
              </Button>
            )}
          </Stack>
        )}
      </Box>

      {/* ── the spec sheet ── */}
      <Box
        sx={{
          flex: 1,
          overflowY: "auto",
          px: 2.5,
          py: 2,
          display: "flex",
          flexDirection: "column",
          gap: 2.5,
        }}
      >
        <Section title="Who they are" rows={whoRowsOf(persona)} />
        <Section title="How they talk" rows={talkRowsOf(persona)} />
        {voice ? (
          <Section title="On the call" rows={callRowsOf(persona)} />
        ) : (
          <Section title="In the chat" rows={chatRowsOf(persona)} />
        )}
        {usage?.scenarios > 0 && (
          <Section
            title="Used in"
            rows={usage.envs.map((name) => [name, null])}
          />
        )}
      </Box>
    </Stack>
  );
};

PersonaInfo.propTypes = {
  persona: PropTypes.object,
  /* `{ scenarios, envs: [name] }` where this persona is used; undefined when unknown. */
  usage: PropTypes.object,
  onUse: PropTypes.func,
  onDuplicate: PropTypes.func,
  onEdit: PropTypes.func,
  onClose: PropTypes.func,
};

export default PersonaInfo;

const LABEL_W = 200;

function Section({ title, rows }) {
  return (
    <Box>
      <Typography
        sx={{
          typography: "s3",
          fontWeight: 600,
          color: "text.subtitle",
          textTransform: "none",
          letterSpacing: 0.5,
          mb: 0.75,
        }}
      >
        {title}
      </Typography>
      {rows.length ? (
        <Box
          sx={{ border: "1px solid", borderColor: "divider", borderRadius: 1 }}
        >
          {rows.map(([label, value], i) => (
            <Stack
              key={label}
              direction="row"
              alignItems="baseline"
              spacing={2}
              sx={{
                px: 1.5,
                py: 1,
                borderTop: i ? "1px solid" : "none",
                borderColor: "divider",
              }}
            >
              <Typography
                sx={{
                  typography: "s2",
                  color: value == null ? "text.primary" : "text.subtitle",
                  width: value == null ? "auto" : LABEL_W,
                  flexShrink: 0,
                }}
              >
                {label}
              </Typography>
              {value != null && (
                <Typography
                  sx={{ typography: "s2", color: "text.primary", minWidth: 0 }}
                >
                  {value}
                </Typography>
              )}
            </Stack>
          ))}
        </Box>
      ) : (
        <Typography sx={{ typography: "s2", color: "text.disabled" }}>
          Not set
        </Typography>
      )}
    </Box>
  );
}
Section.propTypes = { title: PropTypes.string, rows: PropTypes.array };
