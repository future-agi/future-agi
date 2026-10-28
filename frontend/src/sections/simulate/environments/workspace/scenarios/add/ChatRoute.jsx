import PropTypes from "prop-types";
import { useState } from "react";
import {
  Box,
  Button,
  ButtonBase,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";

// Asks that read like what people actually type, one per kind of gap.
const EXAMPLES = [
  "Callers disputing a double charge",
  "Someone asking for a refund on behalf of a relative",
  "A caller who switches language halfway through",
];

// Vel's From chat: write what's missing and it goes to the builder chat,
// which writes the scenarios.
export default function ChatRoute({ onSend }) {
  const [ask, setAsk] = useState("");
  const text = ask.trim();
  return (
    <Box sx={{ p: 3 }}>
      <Typography
        sx={{
          typography: "s3",
          fontWeight: 600,
          color: "text.subtitle",
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
          if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) onSend(text);
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
            onClick={() => setAsk(ex)}
            sx={{
              height: 24,
              px: 1,
              border: "1px solid",
              borderColor: "divider",
              borderRadius: "4px",
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
          color="primary"
          onClick={() => onSend(text)}
          startIcon={<Iconify icon="solar:chat-round-line-linear" width={16} />}
        >
          {text ? "Write them in chat" : "Open chat"}
        </Button>
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
          It lands in the builder chat as an Add scenarios ask. Send it from
          there and keep refining.
        </Typography>
      </Stack>
    </Box>
  );
}
ChatRoute.propTypes = { onSend: PropTypes.func.isRequired };
