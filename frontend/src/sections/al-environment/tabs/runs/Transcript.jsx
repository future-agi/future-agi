import PropTypes from "prop-types";
import { Box, Stack } from "@mui/material";
import { alpha } from "@mui/material/styles";
import { ALK_MONO } from "../../alkTokens";
import CodeBlock from "../../parts/CodeBlock";

const SPEAKER = /^(assistant|user|agent|customer|caller)\s*:\s*(.*)$/i;

/** A transcript arrives as flat "speaker: text" lines; draw it as the conversation it was. */
const parseTranscript = (spoken) => {
  const lines = [];
  for (const line of String(spoken).split("\n")) {
    const match = line.match(SPEAKER);
    if (match) {
      lines.push({
        who: /^(assistant|agent)$/i.test(match[1]) ? "agent" : "caller",
        text: match[2],
      });
    } else if (lines.length && line.trim()) {
      // A wrapped continuation belongs to whoever was last speaking.
      lines[lines.length - 1].text += `\n${line}`;
    }
  }
  return lines;
};

const Transcript = ({ spoken }) => {
  const lines = parseTranscript(spoken);
  // Nothing parsed means it was never a dialogue in this shape — show it verbatim rather
  // than swallowing it.
  if (!lines.length) return <CodeBlock wrap>{String(spoken)}</CodeBlock>;

  return (
    <Stack spacing={0.9} sx={{ py: 0.5 }}>
      {lines.map((line, index) => {
        const isAgent = line.who === "agent";
        return (
          <Box
            // Two identical utterances can repeat in one call, so position is the only key.
            // eslint-disable-next-line react/no-array-index-key
            key={index}
            sx={{
              maxWidth: "88%",
              alignSelf: isAgent ? "flex-start" : "flex-end",
              px: 2.6,
              py: 1.6,
              fontSize: 13.6,
              whiteSpace: "pre-wrap",
              overflowWrap: "anywhere",
              color: "text.primary",
              // The caller sits on a wash of the world colour, as in the harness itself.
              bgcolor: (theme) =>
                isAgent ? theme.palette.action.hover : alpha(theme.palette.success.main, 0.14),
              borderRadius: isAgent ? "9px 9px 9px 3px" : "9px 9px 3px 9px",
            }}
          >
            <Box
              component="span"
              sx={{
                display: "block",
                fontFamily: ALK_MONO,
                fontSize: 9.9,
                letterSpacing: "0.07em",
                textTransform: "uppercase",
                color: "text.secondary",
                mb: 0.4,
              }}
            >
              {/* "user", not "caller": most agents tested here are typed to, not phoned, and a
                  voice caller is a simulated user too. */}
              {isAgent ? "agent under test" : "simulated user"}
            </Box>
            {line.text}
          </Box>
        );
      })}
    </Stack>
  );
};

Transcript.propTypes = { spoken: PropTypes.string };

export default Transcript;
