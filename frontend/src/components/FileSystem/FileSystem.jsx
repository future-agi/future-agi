/* eslint-disable react/prop-types */
import { Button, Stack, useTheme, Skeleton, Box } from "@mui/material";
import React, { useState } from "react";
import PropTypes from "prop-types";
import Folder from "./Folder";
import SvgColor from "src/components/svg-color";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import { useMemo } from "react";

// Skeleton component for folder items
const FolderSkeleton = ({ isChildren = false, hasChildren = false }) => {
  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        gap: 1,
        py: 1,
        px: isChildren ? 2 : 1,
        minHeight: "40px", // Adjust based on your Folder component height
      }}
    >
      {/* Icon skeleton */}
      <Skeleton
        variant="rectangular"
        width={20}
        height={20}
        sx={{ borderRadius: "4px" }}
      />

      {/* Folder name skeleton */}
      <Skeleton
        variant="text"
        width={`${Math.floor(Math.random() * 100) + 80}px`} // Random width between 80-180px
        height={20}
      />

      {/* Expand/collapse icon skeleton (if has children) */}
      {hasChildren && (
        <Skeleton
          variant="rectangular"
          width={16}
          height={16}
          sx={{ ml: "auto", borderRadius: "2px" }}
        />
      )}
    </Box>
  );
};

FolderSkeleton.propTypes = {
  isChildren: PropTypes.bool,
  hasChildren: PropTypes.bool,
};

// Skeleton for nested folder structure
const FolderNodeSkeleton = ({ level = 0 }) => {
  const hasChildren = Math.random() > 0.6; // Randomly show some folders with children
  const [open] = useState(true);

  return (
    <>
      <FolderSkeleton isChildren={level > 0} hasChildren={hasChildren} />
      {hasChildren &&
        open &&
        level < 2 && ( // Limit nesting to prevent too many skeletons
          <Stack sx={{ pl: level > 0 ? 2 : 0 }}>
            {Array.from({ length: Math.floor(Math.random() * 3) + 1 }).map(
              (_, index) => (
                <FolderNodeSkeleton
                  key={`skeleton-${level}-${index}`}
                  level={level + 1}
                />
              ),
            )}
          </Stack>
        )}
    </>
  );
};

FolderNodeSkeleton.propTypes = {
  level: PropTypes.number,
};

function FolderNode({
  node,
  isChildren = false,
  isRootFolder = false,
  allHasChildren,
  isSample,
  onClick,
}) {
  const [open, setOpen] = useState(true);
  const hasChildren =
    isRootFolder && node?.children && node.children.length > 0;

  return (
    <>
      <Folder
        label={node?.name}
        isChildren={isChildren}
        isActive={open}
        hasChildren={hasChildren}
        createdBy={node.created_by}
        onToggle={() => setOpen((prev) => !prev)}
        id={node?.id}
        type={node?.type}
        sx={{
          ...(allHasChildren && node?.id === "my-templates" && { pl: "32px" }),
        }}
        isSample={isSample}
        onClick={onClick}
      />
      {hasChildren && open && (
        <Stack>
          {node?.children?.map((child) => (
            <FolderNode
              key={child?.id || child?.name}
              node={child}
              isChildren
              isSample={child?.isSample}
              onClick={onClick}
            />
          ))}
        </Stack>
      )}
    </>
  );
}

FolderNode.propTypes = {
  node: PropTypes.shape({
    id: PropTypes.string.isRequired,
    name: PropTypes.string.isRequired,
    type: PropTypes.string,
    createdBy: PropTypes.string,
    children: PropTypes.arrayOf(PropTypes.object),
  }).isRequired,
  isChildren: PropTypes.bool,
  isSample: PropTypes.bool,
  isRootFolder: PropTypes.bool,
  allHasChildren: PropTypes.bool,
  onClick: PropTypes.func,
};

export default function FileSystem({
  onAddNew,
  isLoading,
  mainFolders = {},
  nodes,
  onFolderClick,
}) {
  const { role } = useAuthContext();
  const theme = useTheme();

  const modFolders = useMemo(() => {
    return {
      ...mainFolders,
      children: mainFolders?.children?.map((child) =>
        child.id === "all"
          ? {
              ...child,
              children: nodes?.map((node) => ({
                id: node?.id,
                name: node?.name,
                isSample: node?.isSample,
                type: "FOLDER",
                createdBy: node?.created_by,
              })),
            }
          : child,
      ),
    };
  }, [nodes, mainFolders]);

  const allHasChildren = useMemo(() => {
    const allNode = modFolders?.children?.find((node) => node.id === "all");
    return allNode?.children?.length > 0;
  }, [modFolders]);

  // Show loading skeleton
  if (isLoading) {
    return (
      <Stack>
        {/* Render skeleton structure */}
        {Array.from({ length: 4 }).map((_, index) => (
          <FolderNodeSkeleton key={`main-skeleton-${index}`} level={0} />
        ))}

        {/* New Folder button skeleton */}
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            gap: 1,
            mt: theme.spacing(1),
            py: 1,
          }}
        >
          <Skeleton
            variant="rectangular"
            width={20}
            height={20}
            sx={{ borderRadius: "4px" }}
          />
          <Skeleton variant="text" width={100} height={20} />
        </Box>
      </Stack>
    );
  }

  return (
    <Stack>
      {modFolders?.children?.map((child) => (
        <FolderNode
          key={child.id || child.name}
          node={child}
          isChildren={false}
          isRootFolder={true}
          allHasChildren={allHasChildren}
          onClick={onFolderClick}
        />
      ))}

      <Box
        sx={{
          position: "sticky",
          bottom: 0,
          bgcolor: "background.paper",
          width: "100%",
        }}
      >
        <Button
          onClick={onAddNew}
          disabled={!RolePermission.PROMPTS[PERMISSIONS.CREATE][role]}
          sx={{
            color: "primary.main",
            textAlign: "left",
            mr: "auto",
            mt: theme.spacing(1),
          }}
          startIcon={
            <SvgColor
              sx={{
                color: "primary.main",
                height: 20,
                width: 20,
              }}
              src="/assets/icons/ic_add.svg"
            />
          }
        >
          New Folder
        </Button>
      </Box>
    </Stack>
  );
}

FileSystem.propTypes = {
  onAddNew: PropTypes.func,
  isLoading: PropTypes.bool,
  nodes: PropTypes.array,
  mainFolders: PropTypes.object,
  onFolderClick: PropTypes.func,
};
