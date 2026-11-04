import {
  Box,
  Chip,
  MenuItem,
  Popover,
  Skeleton,
  Typography,
  ButtonBase,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useMemo, useRef, useState } from "react";
import Iconify from "src/components/iconify";
import { ShowComponent } from "src/components/show";
import { usePromptVersions } from "src/api/develop/prompt";
import { getVersionLabel } from "src/utils/utils";
import { useScrollEnd } from "src/hooks/use-scroll-end";

const VERSION_SHAPE = PropTypes.shape({
  id: PropTypes.string,
  template_version: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
  is_default: PropTypes.bool,
});

const VersionPopoverContent = ({
  versions,
  selectedVersion,
  onSelect,
  onCreateNew,
  fetchNextPage,
  hasNextPage,
  isFetchingNextPage,
}) => {
  const scrollRef = useScrollEnd(() => {
    if (!hasNextPage || isFetchingNextPage) return;
    fetchNextPage();
  }, [hasNextPage, isFetchingNextPage]);

  return (
    <>
      <Box ref={scrollRef} sx={{ p: 0.5, overflowY: "auto", flex: 1 }}>
        {versions?.map((version) => (
          <MenuItem
            key={version.id}
            selected={version.id === selectedVersion}
            onClick={() => onSelect(version.id)}
          >
            <Box display="flex" alignItems="center" gap={0.5}>
              <span>{getVersionLabel(version?.template_version)}</span>
              <ShowComponent condition={version.is_default}>
                <Chip
                  label="Default"
                  size="small"
                  color="primary"
                />
              </ShowComponent>
            </Box>
          </MenuItem>
        ))}
        <ShowComponent condition={isFetchingNextPage}>
          {Array.from({ length: 2 }).map((_, index) => (
            <MenuItem key={`version-skeleton-${index}`} disabled>
              <Skeleton
                variant="text"
                width={48}
                sx={{ fontSize: "0.8125rem" }}
              />
            </MenuItem>
          ))}
        </ShowComponent>
      </Box>
      <Box sx={{ borderTop: "1px solid", borderColor: "divider", p: 0.5 }}>
        <ButtonBase
          component="div"
          onClick={onCreateNew}
          sx={{
            display: "flex",
            justifyContent: "flex-start",
            textAlign: "left",
            alignItems: "center",
            gap: 0.5,
            color: "primary.main",
            py: 0.5,
            px: 1,
            borderRadius: "4px",
            "&:hover": { bgcolor: "action.hover" },
          }}
        >
          <Iconify
            icon="solar:add-linear"
            width={16}
            sx={{ color: "primary.main" }}
          />
          <Typography variant="caption" color="primary">
            New Version
          </Typography>
        </ButtonBase>
      </Box>
    </>
  );
};

VersionPopoverContent.propTypes = {
  versions: PropTypes.arrayOf(VERSION_SHAPE).isRequired,
  selectedVersion: PropTypes.string,
  onSelect: PropTypes.func.isRequired,
  onCreateNew: PropTypes.func.isRequired,
  fetchNextPage: PropTypes.func,
  hasNextPage: PropTypes.bool,
  isFetchingNextPage: PropTypes.bool,
};

const VersionSelect = ({
  promptTemplateId,
  value,
  onChange,
  versionDetail,
  disabled,
}) => {
  const [open, setOpen] = useState(false);
  const anchorRef = useRef(null);

  const { data, isLoading, fetchNextPage, hasNextPage, isFetchingNextPage } =
    usePromptVersions(promptTemplateId);

  const versions = useMemo(
    () => data?.pages?.flatMap((page) => page?.results || []) || [],
    [data],
  );

  const selectedLabel = useMemo(() => {
    if (!value) return "Select";
    const match = versions.find((v) => v.id === value);
    if (match) return getVersionLabel(match.template_version);
    if (versionDetail?.id === value)
      return getVersionLabel(versionDetail.template_version);
    return "Select";
  }, [value, versions, versionDetail]);

  const isDisabled = disabled || isLoading;

  return (
    <>
      <ButtonBase
        component="div"
        ref={anchorRef}
        onClick={() => !isDisabled && setOpen(true)}
        sx={{
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 0.5,
          minWidth: 80,
          px: 1,
          py: 0.5,
          border: "1px solid",
          borderColor: "divider",
          borderRadius: "4px",
          cursor: isDisabled ? "default" : "pointer",
          bgcolor: "background.paper",
          "&:hover": { borderColor: "text.disabled" },
        }}
      >
        <Typography variant="s2" sx={{ color: "text.primary" }}>
          {selectedLabel}
        </Typography>
        <Iconify
          icon="solar:alt-arrow-down-linear"
          width={16}
          sx={{ color: "text.secondary", flexShrink: 0 }}
        />
      </ButtonBase>
      <Popover
        open={open}
        anchorEl={anchorRef.current}
        onClose={() => setOpen(false)}
        anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
        transformOrigin={{ vertical: "top", horizontal: "left" }}
        PaperProps={{
          sx: {
            mt: 0.5,
            minWidth: 200,
            maxHeight: 320,
            overflow: "hidden",
            display: "flex",
            flexDirection: "column",
          },
        }}
      >
        <VersionPopoverContent
          versions={versions}
          selectedVersion={value}
          onSelect={(id) => {
            onChange(id);
            setOpen(false);
          }}
          onCreateNew={() => {
            onChange("create-new");
            setOpen(false);
          }}
          fetchNextPage={fetchNextPage}
          hasNextPage={hasNextPage}
          isFetchingNextPage={isFetchingNextPage}
        />
      </Popover>
    </>
  );
};

VersionSelect.propTypes = {
  promptTemplateId: PropTypes.string,
  value: PropTypes.string,
  onChange: PropTypes.func.isRequired,
  versionDetail: VERSION_SHAPE,
  disabled: PropTypes.bool,
};

export default VersionSelect;
