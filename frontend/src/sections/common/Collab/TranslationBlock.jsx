import React, { useState } from "react";
import PropTypes from "prop-types";
import { Box, ButtonBase, Link, Stack, Typography } from "@mui/material";
import { copyText } from "src/sections/common/ux/CopyButton";
import { notify } from "src/sections/common/ux/toast";
import {
  otherLanguagesLine,
  translationStatus,
  translationTitle,
} from "./translateModel";

const linkSx = {
  typography: "s3",
  color: "text.secondary",
  borderRadius: 0.5,
  px: 0.25,
  "&:hover": { color: "primary.main" },
};

/**
 * W4-8: the "Translations" block under a section's fields. Each field:
 * "customer_message · Spanish → English", the text, "Machine translation ·
 * <model> · not saved" (or "· cached"), Copy · Hide. The header line counts
 * the dataset's rows in other languages and opens them as a grid view.
 */
const TranslationBlock = ({ result, info, onShowOtherRows, onClose }) => {
  const [hidden, setHidden] = useState([]);
  if (!result) return null;
  const visible = (result.translations ?? []).filter(
    (t) => !hidden.includes(t.column_id),
  );
  const line = otherLanguagesLine(info);
  return (
    <Box
      data-testid="translation-block"
      sx={{
        mt: 0.5,
        p: 1.25,
        borderRadius: 1,
        bgcolor: "background.neutral",
        border: "1px dashed",
        borderColor: "divider",
      }}
    >
      <Stack
        direction="row"
        alignItems="baseline"
        gap={1}
        sx={{ mb: 0.75, flexWrap: "wrap" }}
      >
        <Typography typography="s2" fontWeight={600}>
          Translations
        </Typography>
        {line && (
          <Typography
            typography="s3"
            color="text.secondary"
            data-testid="translation-other-rows"
          >
            {line}
            {onShowOtherRows && (
              <>
                {" · "}
                <Link
                  component="button"
                  type="button"
                  typography="s3"
                  underline="always"
                  onClick={onShowOtherRows}
                  data-testid="translation-show-them"
                >
                  Show them
                </Link>
              </>
            )}
          </Typography>
        )}
        <Box sx={{ flex: 1 }} />
        {onClose && (
          <ButtonBase onClick={onClose} sx={linkSx}>
            Close
          </ButtonBase>
        )}
      </Stack>
      <Stack gap={1.25}>
        {visible.map((t) => (
          <Box key={t.column_id} data-testid="translation-item">
            <Typography
              typography="s3"
              color="text.secondary"
              sx={{ mb: 0.25 }}
            >
              {translationTitle(t, {
                languages: info?.languages,
                target: result.target,
              })}
            </Typography>
            <Typography
              typography="s1"
              sx={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}
              data-testid="translation-text"
            >
              {t.text}
            </Typography>
            <Stack
              direction="row"
              alignItems="center"
              gap={0.75}
              sx={{ mt: 0.25 }}
            >
              <Typography
                typography="s3"
                color="text.disabled"
                data-testid="translation-status"
              >
                {translationStatus(result.model, t.cached)}
              </Typography>
              <Box sx={{ flex: 1 }} />
              <ButtonBase
                sx={linkSx}
                onClick={async () => {
                  if (await copyText(t.text))
                    notify("Translation copied", { key: "tr-copy" });
                }}
              >
                Copy
              </ButtonBase>
              <ButtonBase
                sx={linkSx}
                onClick={() => setHidden((h) => [...h, t.column_id])}
              >
                Hide
              </ButtonBase>
            </Stack>
          </Box>
        ))}
        {visible.length === 0 && (
          <Typography typography="s3" color="text.disabled">
            Every translation here is hidden.
          </Typography>
        )}
      </Stack>
    </Box>
  );
};

TranslationBlock.propTypes = {
  result: PropTypes.object,
  info: PropTypes.object,
  onShowOtherRows: PropTypes.func,
  onClose: PropTypes.func,
};

export default TranslationBlock;
