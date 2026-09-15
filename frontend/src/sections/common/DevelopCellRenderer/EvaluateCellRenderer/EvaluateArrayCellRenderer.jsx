import { Box, Chip, useTheme } from "@mui/material";
import React, { useMemo } from "react";
import RenderMeta from "../RenderMeta";
import PropTypes from "prop-types";

const choicesBorderColourMap = {
  neutral: "primary.main",
  pass: "green.500",
  fail: "red.500",
};

const choicesFontColourMap = {
  neutral: "primary.main",
  pass: "green.500",
  fail: "red.500",
};

const MAX_CHOICE_TEXT = 16_384;
const MAX_CHOICES = 256;
const MAX_DEPTH = 4;

// Match Python/ClickHouse character bounds, not UTF-16 code units. The cheap
// first check also avoids expanding an arbitrarily large supplied string.
const choiceTextTooLong = (text) =>
  text.length > MAX_CHOICE_TEXT * 2 ||
  (text.length > MAX_CHOICE_TEXT && Array.from(text).length > MAX_CHOICE_TEXT);

const invalidChoice = () => {
  throw new Error("Invalid evaluation choice cell");
};

// The historical TextField contains repr of a flat list or a scored choice
// dict. Read only that literal grammar; never execute code or replace quotes
// globally (apostrophes inside a label are data, including in valid JSON).
const readChoiceLiteral = (text, jsonMode = false) => {
  let offset = 0;
  let nodes = 0;
  const whitespace = () => {
    while (/\s/.test(text[offset] ?? "") && offset < text.length) offset++;
  };
  const read = (depth = 0) => {
    whitespace();
    if (depth > MAX_DEPTH || ++nodes > 1024) invalidChoice();
    const start = text[offset++];
    if (start === "'" || start === '"') {
      let value = "";
      while (offset < text.length) {
        const char = text[offset++];
        if (char === start) return value;
        if (char.charCodeAt(0) < 32) invalidChoice();
        if (char !== "\\") {
          value += char;
          continue;
        }
        const escape = text[offset++];
        const escapes = {
          "'": "'",
          '"': '"',
          "\\": "\\",
          "/": "/",
          a: "\x07",
          b: "\b",
          f: "\f",
          n: "\n",
          r: "\r",
          t: "\t",
          v: "\v",
        };
        if (Object.hasOwn(escapes, escape)) value += escapes[escape];
        else if (["x", "u", "U"].includes(escape)) {
          const length = { x: 2, u: 4, U: 8 }[escape];
          const hex = text.slice(offset, offset + length);
          if (hex.length !== length || !/^[0-9a-f]+$/i.test(hex))
            invalidChoice();
          const code = parseInt(hex, 16);
          // Python repr escapes denote individual code points, unlike JSON's
          // UTF-16 surrogate pairs. Neither reader accepts lone surrogates.
          if (
            code > 0x10ffff ||
            (!jsonMode && code >= 0xd800 && code <= 0xdfff)
          )
            invalidChoice();
          value += String.fromCodePoint(code);
          offset += length;
        } else invalidChoice();
      }
      invalidChoice();
    }
    if (start === "[" || start === "{") {
      const object = start === "{";
      const end = object ? "}" : "]";
      const value = object ? Object.create(null) : [];
      whitespace();
      while (text[offset] !== end) {
        if (object) {
          const key = read(depth + 1);
          whitespace();
          if (
            typeof key !== "string" ||
            Object.hasOwn(value, key) ||
            text[offset++] !== ":"
          )
            invalidChoice();
          value[key] = read(depth + 1);
        } else value.push(read(depth + 1));
        whitespace();
        if (text[offset] === end) break;
        if (text[offset++] !== ",") invalidChoice();
        whitespace();
      }
      offset++;
      return value;
    }
    offset--;
    const number = text
      .slice(offset)
      .match(/^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?/);
    if (!number || !Number.isFinite(Number(number[0]))) invalidChoice();
    offset += number[0].length;
    return Number(number[0]);
  };
  const value = read();
  whitespace();
  if (offset !== text.length) invalidChoice();
  return value;
};

const choiceLabels = (value) => {
  // Match the producer's Python str.strip(): FEFF is a label, while NEL and
  // the C0 information separators count as whitespace. Never trim the label.
  const blank = (text) =>
    // eslint-disable-next-line no-control-regex -- Match Python str.strip() control whitespace exactly.
    /^[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]*$/u.test(
      text,
    );
  if (value && typeof value === "object" && !Array.isArray(value)) {
    const keys = Object.keys(value);
    const choiceKeys = keys.filter(
      (key) => key === "choice" || key === "choices",
    );
    if (
      choiceKeys.length !== 1 ||
      keys.some((key) => !["choice", "choices", "score"].includes(key))
    )
      invalidChoice();
    if (
      Object.hasOwn(value, "score") &&
      (typeof value.score !== "number" || !Number.isFinite(value.score))
    )
      invalidChoice();
    value = value[choiceKeys[0]];
    if (choiceKeys[0] === "choice" && typeof value !== "string")
      invalidChoice();
  }
  if (typeof value === "string") value = blank(value) ? [] : [value];
  if (
    !Array.isArray(value) ||
    value.length > MAX_CHOICES ||
    value.some(
      (item) =>
        typeof item !== "string" ||
        blank(item) ||
        choiceTextTooLong(item) ||
        /[\ud800-\udfff]/u.test(item),
    )
  )
    invalidChoice();
  if (
    value.reduce((size, item) => size + Array.from(item).length, 0) >
    MAX_CHOICE_TEXT
  )
    invalidChoice();
  return value;
};

const structuredChoiceResult = (valueInfos) => {
  if (valueInfos?.output !== "choices") return undefined;
  const data = valueInfos.data;
  if (data && typeof data === "object" && !Array.isArray(data)) {
    const keys = ["result", "choice"].filter((key) => Object.hasOwn(data, key));
    return keys.length === 1 ? data[keys[0]] : undefined;
  }
  return data;
};

const EvaluateArrayCellRenderer = ({
  meta,
  isFutureAgiEval,
  value,
  valueInfos,
  choicesMap,
}) => {
  const finalArray = useMemo(() => {
    try {
      if (value == null || value === "") return [];
      const structuredResult = structuredChoiceResult(valueInfos);
      if (typeof value === "string" && structuredResult === value) {
        // Exact structured evidence disambiguates literal labels such as
        // "[west]" from a serialized container, without guessing.
        return choiceLabels(structuredResult);
      }
      let parsed = value;
      if (typeof value === "string") {
        if (choiceTextTooLong(value)) invalidChoice();
        // Use the same Python whitespace set for container detection; e.g.
        // FEFF followed by "[west]" remains an ordinary literal choice.
        const trimmed = value.replace(
          // eslint-disable-next-line no-control-regex -- Match Python str.strip() control whitespace exactly.
          /^[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+|[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+$/gu,
          "",
        );
        if (!trimmed) return [];
        if (trimmed.startsWith("[") || trimmed.startsWith("{")) {
          try {
            parsed = JSON.parse(trimmed);
          } catch {
            parsed = undefined;
          }
          // Also validate JSON against the bounded literal grammar: duplicate
          // keys must not silently choose the last of two disagreeing labels.
          const literal = readChoiceLiteral(trimmed, parsed !== undefined);
          if (parsed === undefined) parsed = literal;
        }
      }
      const storedLabels = choiceLabels(parsed);
      // value_infos is the actual evaluation response, not timing/cost `meta`.
      // Prefer its structured result only after agreement with this cell's
      // stored value. Stale/disagreeing metadata must never invent labels.
      if (structuredResult != null) {
        try {
          const resultLabels = choiceLabels(structuredResult);
          if (JSON.stringify(resultLabels) === JSON.stringify(storedLabels))
            return resultLabels;
        } catch {
          /* Invalid metadata does not override a valid stored value. */
        }
      }
      return storedLabels;
    } catch {
      return [];
    }
  }, [value, valueInfos]);
  const theme = useTheme();

  return (
    <Box
      sx={{
        padding: 1,
        display: "flex",
        flexDirection: "column",
        gap: 1,
        height: "100%",
      }}
    >
      <Box
        sx={{
          lineHeight: "1.5",
          display: "flex",
          gap: 1,
          flexWrap: "wrap",
          overflow: "auto",
        }}
      >
        {/* Empty result renders nothing (it means "no result yet", not "None"). */}
        {finalArray?.length
          ? finalArray?.map((item) => (
              <Chip
                key={item}
                label={item}
                size="small"
                variant="outlined"
                sx={{
                  borderRadius: theme.spacing(0.5),
                  borderColor:
                    choicesBorderColourMap?.[choicesMap?.[item] ?? "neutral"],
                  color:
                    choicesFontColourMap?.[choicesMap?.[item] ?? "neutral"],
                  fontWeight: 400,
                  typography: "s3",
                }}
              />
            ))
          : null}
      </Box>
      <RenderMeta
        originType="evaluation"
        meta={meta}
        showToken={!isFutureAgiEval}
      />
    </Box>
  );
};

EvaluateArrayCellRenderer.propTypes = {
  meta: PropTypes.object,
  isFutureAgiEval: PropTypes.bool,
  value: PropTypes.any,
  valueInfos: PropTypes.object,
  choicesMap: PropTypes.object,
};

export default EvaluateArrayCellRenderer;
