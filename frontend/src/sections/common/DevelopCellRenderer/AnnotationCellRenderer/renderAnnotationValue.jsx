import { Chip, Stack } from "@mui/material";
import React from "react";
import Iconify from "src/components/iconify";

export const parseAnnotationValue = (value) => {
  if (Array.isArray(value)) return value;
  if (typeof value !== "string" || !value) return value;
  try {
    return JSON.parse(value.replaceAll("'", '"'));
  } catch {
    return value;
  }
};

const renderChips = (items, theme) =>
  items.map((item) => (
    <Chip
      key={item}
      label={item}
      size="small"
      color="primary"
      sx={{
        backgroundColor: theme.palette.action.hover,
        color: theme.palette.primary.main,
        fontWeight: 400,
      }}
    />
  ));

export const renderAnnotationValue = (value, theme) => {
  const parsed = parseAnnotationValue(value);

  if (Array.isArray(parsed)) return renderChips(parsed, theme);

  if (parsed && typeof parsed === "object") {
    if (Array.isArray(parsed.selected))
      return renderChips(parsed.selected, theme);

    if (typeof parsed.rating === "number") {
      const count = Math.max(0, Math.floor(parsed.rating));
      return (
        <Stack direction="row" spacing={0.25} alignItems="center">
          {Array.from({ length: count }, (_, i) => (
            <Iconify
              key={i}
              icon="solar:star-bold"
              width={18}
              sx={{ color: "#ef4444" }}
            />
          ))}
        </Stack>
      );
    }

    if (parsed.value === "up" || parsed.value === "down") {
      const isUp = parsed.value === "up";
      return (
        <Iconify
          icon={isUp ? "solar:like-bold" : "solar:dislike-bold"}
          width={20}
          sx={{ color: isUp ? "#22c55e" : "#ef4444" }}
        />
      );
    }

    if (typeof parsed.value === "number" || typeof parsed.value === "string") {
      return String(parsed.value);
    }

    if (typeof parsed.text === "string") return parsed.text;

    return null;
  }

  if (parsed === null || parsed === undefined || parsed === "") return null;

  return String(parsed);
};
