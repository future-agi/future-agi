import { useState, useRef, useEffect, useMemo, useCallback } from "react";
import PropTypes from "prop-types";
import {
  Box,
  TextField,
  Chip,
  Paper,
  Tooltip,
  Typography,
  InputAdornment,
} from "@mui/material";
import Iconify from "src/components/iconify";
import VirtualCard from "./VirtualCard";
import iconManifest from "./icon-manifest.json";

const ALL_CATEGORY = "__all__";

function IconCard({ icon, onCopy, onCopyPath, showCategory, isGrouped }) {
  const [copied, setCopied] = useState(false);
  const timerRef = useRef(null);

  useEffect(() => () => clearTimeout(timerRef.current), []);

  const handleClick = useCallback(async () => {
    const success = await onCopy(icon);
    if (success === false) return;
    setCopied(true);
    clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => setCopied(false), 1500);
  }, [icon, onCopy]);

  const handlePathCopy = useCallback(
    async (e) => {
      const success = await onCopyPath(e, icon);
      if (success === false) return;
      setCopied(true);
      clearTimeout(timerRef.current);
      timerRef.current = setTimeout(() => setCopied(false), 1500);
    },
    [icon, onCopyPath],
  );

  return (
    <Tooltip
      title={
        <Box>
          <Typography
            variant="caption"
            display="block"
            sx={{ fontFamily: "monospace" }}
          >
            {icon.filePath}
          </Typography>
          <Typography variant="caption" display="block" sx={{ mt: 0.5 }}>
            Click to copy SvgColor usage
          </Typography>
          {icon.isDuplicate && (
            <Box sx={{ mt: 0.5 }}>
              <Typography
                variant="caption"
                display="block"
                color="warning.main"
                sx={{ fontWeight: 600 }}
              >
                Also exists at:
              </Typography>
              {icon.duplicatePaths
                .filter((d) => d.filePath !== icon.filePath)
                .map((d) => (
                  <Typography
                    key={d.filePath}
                    variant="caption"
                    display="block"
                    sx={{ fontFamily: "monospace", fontSize: "0.65rem" }}
                  >
                    {d.filePath} ({d.category})
                  </Typography>
                ))}
            </Box>
          )}
        </Box>
      }
      arrow
    >
      <Paper
        elevation={0}
        onClick={handleClick}
        sx={{
          p: 1.5,
          flex: isGrouped ? 1 : undefined,
          height: "100%",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          cursor: "pointer",
          border: "1px solid",
          borderColor: copied ? "success.main" : "divider",
          borderRadius: 1,
          transition: "all 0.15s",
          position: "relative",
          bgcolor: copied ? "success.lighter" : "transparent",
          "&:hover": {
            borderColor: copied ? "success.main" : "primary.main",
            bgcolor: copied ? "success.lighter" : "action.hover",
            "& .copy-hint": { opacity: 1 },
          },
        }}
      >
        {/* Copied overlay */}
        {copied && (
          <Box
            sx={{
              position: "absolute",
              top: 4,
              right: 4,
              display: "flex",
              alignItems: "center",
              gap: 0.25,
              color: "success.main",
            }}
          >
            <Iconify icon="material-symbols:check-circle" width={14} />
            <Typography
              variant="caption"
              sx={{ fontSize: "0.6rem", fontWeight: 600 }}
            >
              Copied
            </Typography>
          </Box>
        )}

        {/* Copy path button */}
        {!copied && (
          <Box
            className="copy-hint"
            onClick={handlePathCopy}
            sx={{
              position: "absolute",
              top: 4,
              left: 4,
              color: "text.secondary",
              opacity: 0,
              transition: "opacity 0.15s",
              cursor: "pointer",
              "&:hover": { color: "primary.main" },
            }}
          >
            <Iconify icon="material-symbols:content-copy-outline" width={12} />
          </Box>
        )}

        {/* Icon preview */}
        <Box
          sx={{
            width: 36,
            height: 36,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            mb: 1,
            borderRadius: 0.5,
            backgroundImage:
              "linear-gradient(45deg, #e0e0e0 25%, transparent 25%), linear-gradient(-45deg, #e0e0e0 25%, transparent 25%), linear-gradient(45deg, transparent 75%, #e0e0e0 75%), linear-gradient(-45deg, transparent 75%, #e0e0e0 75%)",
            backgroundSize: "8px 8px",
            backgroundPosition: "0 0, 0 4px, 4px -4px, -4px 0px",
          }}
        >
          <img
            src={icon.filePath}
            alt={icon.fileName}
            style={{
              maxWidth: 32,
              maxHeight: 32,
              objectFit: "contain",
            }}
            loading="lazy"
          />
        </Box>

        {/* Filename */}
        <Typography
          variant="caption"
          sx={{
            textAlign: "center",
            wordBreak: "break-all",
            lineHeight: 1.2,
            fontSize: "0.65rem",
            color: "text.secondary",
          }}
        >
          {icon.fileName}
        </Typography>

        {/* Category badge */}
        {showCategory && (
          <Typography
            variant="caption"
            sx={{
              fontSize: "0.55rem",
              color: "text.disabled",
              mt: 0.25,
            }}
          >
            {icon.category}
          </Typography>
        )}
      </Paper>
    </Tooltip>
  );
}

IconCard.propTypes = {
  icon: PropTypes.object.isRequired,
  onCopy: PropTypes.func.isRequired,
  onCopyPath: PropTypes.func.isRequired,
  showCategory: PropTypes.bool,
  isGrouped: PropTypes.bool,
};

export default function IconGallery({ defaultCategory }) {
  const [search, setSearch] = useState("");
  const [selectedCategory, setSelectedCategory] = useState(
    defaultCategory || ALL_CATEGORY,
  );
  const categories = useMemo(() => iconManifest.categories || [], []);

  const categoryCounts = useMemo(() => {
    const counts = new Map();
    for (const icon of iconManifest.icons || []) {
      counts.set(icon.category, (counts.get(icon.category) || 0) + 1);
    }
    return counts;
  }, []);

  const filteredIcons = useMemo(() => {
    let icons = iconManifest.icons || [];

    if (selectedCategory !== ALL_CATEGORY) {
      icons = icons.filter((i) => i.category === selectedCategory);
    }

    if (search.trim()) {
      const q = search.toLowerCase().trim();
      icons = icons.filter(
        (i) =>
          i.fileName.toLowerCase().includes(q) ||
          i.category.toLowerCase().includes(q) ||
          i.keywords.some((k) => k.includes(q)),
      );
    }

    return icons;
  }, [search, selectedCategory]);

  // Build layout items: singles + grouped duplicates
  const layoutItems = useMemo(() => {
    const items = [];
    const seen = new Set();

    for (const icon of filteredIcons) {
      if (seen.has(icon.filePath)) continue;

      if (icon.isDuplicate) {
        if (seen.has(icon.fileName)) continue;
        seen.add(icon.fileName);
        // Collect all variants of this name that are in the filtered set
        const group = filteredIcons.filter((f) => f.fileName === icon.fileName);
        group.forEach((g) => seen.add(g.filePath));
        if (group.length > 1) {
          items.push({ type: "group", fileName: icon.fileName, icons: group });
        } else {
          items.push({ type: "single", icon });
        }
      } else {
        seen.add(icon.filePath);
        items.push({ type: "single", icon });
      }
    }

    return items;
  }, [filteredIcons]);

  const handleCopy = useCallback(async (icon) => {
    const code = `<SvgColor src="${icon.filePath}" sx={{ width: 24, height: 24 }} />`;
    try {
      await navigator.clipboard.writeText(code);
    } catch {
      return false;
    }
  }, []);

  const handleCopyPath = useCallback(async (e, icon) => {
    e.stopPropagation();
    try {
      await navigator.clipboard.writeText(icon.filePath);
    } catch {
      return false;
    }
  }, []);

  return (
    <Box sx={{ p: 3 }}>
      {/* Header */}
      <Typography variant="h5" sx={{ mb: 1 }}>
        Local SVG Icons
      </Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
        {filteredIcons.length} of {iconManifest.total} icons
        {iconManifest.duplicateCount > 0 && (
          <Chip
            icon={
              <Iconify icon="material-symbols:warning-outline" width={16} />
            }
            label={`${iconManifest.duplicateCount} duplicates`}
            size="small"
            color="warning"
            variant="outlined"
            sx={{ ml: 1 }}
          />
        )}
      </Typography>

      {/* Search */}
      <TextField
        fullWidth
        size="small"
        placeholder="Search icons by name or keyword..."
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        slotProps={{
          input: {
            startAdornment: (
              <InputAdornment position="start">
                <Iconify icon="material-symbols:search" width={20} />
              </InputAdornment>
            ),
          },
        }}
        sx={{ mb: 2 }}
      />

      {/* Category filter */}
      <Box sx={{ display: "flex", flexWrap: "wrap", gap: 0.5, mb: 3 }}>
        <Chip
          label={`All (${iconManifest.total})`}
          size="small"
          variant={selectedCategory === ALL_CATEGORY ? "filled" : "outlined"}
          color={selectedCategory === ALL_CATEGORY ? "primary" : "default"}
          onClick={() => setSelectedCategory(ALL_CATEGORY)}
        />
        {categories.map((cat) => (
          <Chip
            key={cat}
            label={`${cat} (${categoryCounts.get(cat) || 0})`}
            size="small"
            variant={selectedCategory === cat ? "filled" : "outlined"}
            color={selectedCategory === cat ? "primary" : "default"}
            onClick={() => setSelectedCategory(cat)}
          />
        ))}
      </Box>

      {/* Icon grid */}
      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fill, minmax(140px, 1fr))",
          gridAutoRows: "160px",
          gap: 1.5,
        }}
      >
        {layoutItems.map((item) => {
          if (item.type === "group") {
            return (
              <VirtualCard
                key={`group-${item.fileName}`}
                height={160}
                style={{
                  gridColumn: `span ${Math.min(item.icons.length, 3)}`,
                }}
              >
                <Box
                  sx={{
                    border: "2px solid",
                    borderColor: "warning.main",
                    borderRadius: 1.5,
                    p: 1,
                    bgcolor: "warning.lighter",
                    height: "100%",
                  }}
                >
                  <Box
                    sx={{
                      display: "flex",
                      alignItems: "center",
                      gap: 0.5,
                      mb: 1,
                      px: 0.5,
                    }}
                  >
                    <Iconify
                      icon="material-symbols:warning-outline"
                      width={14}
                      sx={{ color: "warning.main" }}
                    />
                    <Typography
                      variant="caption"
                      sx={{
                        color: "warning.dark",
                        fontWeight: 600,
                        fontSize: "0.65rem",
                      }}
                    >
                      {item.icons.length} files named &quot;{item.fileName}
                      &quot;
                    </Typography>
                  </Box>
                  <Box sx={{ display: "flex", flexWrap: "wrap", gap: 1 }}>
                    {item.icons.map((variant) => (
                      <IconCard
                        key={variant.filePath}
                        icon={variant}
                        onCopy={handleCopy}
                        onCopyPath={handleCopyPath}
                        showCategory
                        isGrouped
                      />
                    ))}
                  </Box>
                </Box>
              </VirtualCard>
            );
          }

          return (
            <VirtualCard key={item.icon.filePath} height={160}>
              <IconCard
                icon={item.icon}
                onCopy={handleCopy}
                onCopyPath={handleCopyPath}
                showCategory={selectedCategory === ALL_CATEGORY}
              />
            </VirtualCard>
          );
        })}
      </Box>

      {/* Empty state */}
      {filteredIcons.length === 0 && (
        <Box sx={{ textAlign: "center", py: 8 }}>
          <Typography color="text.secondary">
            No icons found matching &quot;{search}&quot;
          </Typography>
        </Box>
      )}
    </Box>
  );
}

IconGallery.propTypes = {
  defaultCategory: PropTypes.string,
};
