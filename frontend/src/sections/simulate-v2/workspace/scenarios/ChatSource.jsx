import PropTypes from "prop-types";
import { useState } from "react";
import {
  Box,
  Stack,
  Typography,
  Button,
  TextField,
  ButtonBase,
} from "@mui/material";
import Iconify from "src/components/iconify";

/* Asks that read like what people actually type — one per kind of gap. */
const EXAMPLES = [
  "Callers disputing a double charge",
  "Someone asking for a refund on behalf of a relative",
  "A caller who switches language halfway through",
];

/**
 * From chat — say what's missing and the builder chat writes it.
 *
 * The chat was a button in the drawer header, which made it read as a way
 * out of the drawer rather than one of the ways in. Here it is a source like
 * the others: write the ask, and it goes to the chat, which writes the
 * scenarios as a new batch marked From chat. With nothing typed, it just
 * opens the chat with the Add scenarios skill pinned.
 */
export default function ChatSource({ onSend, onOpenChat }) {
  const [ask, setAsk] = useState("");
  const text = ask.trim();

  return (
    <Box sx={{ p: 3 }}>
      <Typography
        sx={{
          typography: "s3",
          fontWeight: 600,
          color: "text.tertiary",
          textTransform: "none",
          letterSpacing: 0.4,
          mb: 1,
        }}
      >
        What&apos;s missing
      </Typography>
      <TextField
        fullWidth
        multiline
        minRows={3}
        size="small"
        value={ask}
        onChange={(e) => setAsk(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && text)
            onSend(text);
        }}
        placeholder="Describe the callers or situations this environment doesn't test yet…"
        sx={{ mb: 1.25, "& .MuiInputBase-input": { typography: "s2" } }}
      />
      <Stack
        direction="row"
        spacing={0.75}
        rowGap={0.75}
        flexWrap="wrap"
        sx={{ mb: 3 }}
      >
        {EXAMPLES.map((ex) => (
          <ButtonBase
            key={ex}
            type="button"
            onClick={() => setAsk(ex)}
            sx={{
              display: "inline-flex",
              alignItems: "center",
              height: 24,
              px: 1,
              border: "1px solid",
              borderColor: "divider",
              borderRadius: "4px",
              bgcolor: "transparent",
              cursor: "pointer",
              font: "inherit",
              typography: "s3",
              color: "text.secondary",
              "&:hover": {
                borderColor: "text.disabled",
                color: "text.primary",
              },
            }}
          >
            {ex}
          </ButtonBase>
        ))}
      </Stack>

      <Stack direction="row" alignItems="center" spacing={2}>
        <Button
          size="small"
          variant="contained"
          onClick={() => (text ? onSend(text) : onOpenChat())}
          startIcon={<Iconify icon="solar:chat-round-line-linear" width={16} />}
          sx={{
            bgcolor: "text.primary",
            color: (t) =>
              t.palette.mode === "dark"
                ? "background.default"
                : "background.paper",
            "&:hover": {
              bgcolor: "text.primary",
              opacity: 0.92,
              boxShadow: "none",
            },
          }}
        >
          {text ? "Write them in chat" : "Open chat"}
        </Button>
        <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
          They land as a new batch marked From chat — keep refining them there.
        </Typography>
      </Stack>
    </Box>
  );
}
ChatSource.propTypes = {
  onSend: PropTypes.func.isRequired,
  onOpenChat: PropTypes.func.isRequired,
};
