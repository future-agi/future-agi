import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Button, Collapse, Typography } from "@mui/material";
import SectionCard from "../../components/SectionCard";
import CodeBlock from "./CodeBlock";
import { MONO, WORLD_FACTS_COPY as F, storeFacts } from "./world.constants";

export default function WorldFactsCard({
  dataStore,
  refusalSignature,
  runtime,
  sx,
}) {
  const [runtimeOpen, setRuntimeOpen] = useState(false);
  const facts = storeFacts(dataStore);
  const label = {
    typography: "s3",
    color: "text.tertiary",
  };
  return (
    <SectionCard
      title={F.title}
      subtitle={F.subtitle}
      sx={sx}
      action={
        runtime ? (
          <Button
            size="small"
            variant="outlined"
            color="inherit"
            onClick={() => setRuntimeOpen(!runtimeOpen)}
            aria-expanded={runtimeOpen}
            sx={{ flexShrink: 0 }}
          >
            {runtimeOpen ? F.hideRuntime : F.showRuntime}
          </Button>
        ) : null
      }
    >
      {facts.length > 0 && (
        <Box
          sx={{
            px: 1.5,
            py: 1.25,
            display: "grid",
            gridTemplateColumns: {
              xs: "1fr",
              sm: "repeat(2, minmax(0, 1fr))",
            },
            gap: 1.25,
          }}
        >
          {facts.map((fact) => (
            <Box key={fact.key} minWidth={0}>
              <Typography sx={label}>{fact.label}</Typography>
              <Typography
                sx={{
                  typography: "s1",
                  fontFamily: MONO,
                  overflowWrap: "anywhere",
                }}
              >
                {fact.value}
              </Typography>
            </Box>
          ))}
        </Box>
      )}
      {refusalSignature && (
        <Box
          sx={{
            px: 1.5,
            py: 1.25,
            borderTop: facts.length ? "1px solid" : "none",
            borderColor: "divider",
          }}
        >
          <Typography sx={label}>{F.refusal}</Typography>
          <Typography sx={{ typography: "s1", fontFamily: MONO }}>
            {refusalSignature}
          </Typography>
        </Box>
      )}
      {runtime && (
        <Collapse in={runtimeOpen} unmountOnExit>
          <Box sx={{ p: 1.5, borderTop: "1px solid", borderColor: "divider" }}>
            <CodeBlock
              label={F.runtime}
              file={F.runtimeFile}
              code={JSON.stringify(runtime, null, 2)}
            />
          </Box>
        </Collapse>
      )}
    </SectionCard>
  );
}

WorldFactsCard.propTypes = {
  dataStore: PropTypes.object,
  refusalSignature: PropTypes.string,
  runtime: PropTypes.object,
  sx: PropTypes.object,
};
