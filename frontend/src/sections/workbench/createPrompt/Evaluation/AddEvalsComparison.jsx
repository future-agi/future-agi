import { Box, Button, Drawer, IconButton, Typography } from "@mui/material";
import React, { useState } from "react";
import { useParams } from "react-router";
import { useScrollEnd } from "src/hooks/use-scroll-end";
import { ShowComponent } from "src/components/show";
import Iconify from "src/components/iconify";
import PropTypes from "prop-types";
import { usePromptVersions } from "../hooks/use-prompt-versions";
import EmptyVersions from "../VersionHistory/EmptyVersions";
import VersionCardSkeleton from "../VersionHistory/VersionCardSkeleton";
import VersionCard from "../VersionHistory/VersionCard";

import { useWorkbenchEvaluationContext } from "./context/WorkbenchEvaluationContext";

const AddEvalsComparisonChild = ({
  onClose,
  showCheckbox = false,
  title = "",
  description = "",
  maxSelectLimit = 0,
}) => {
  const { versions: selectedVersions, setVersions: setSelectedVersions } =
    useWorkbenchEvaluationContext();
  const selectedVersion = selectedVersions[0];
  const { id } = useParams();
  const [checkedVersion, setCheckedVersion] = useState(selectedVersions);

  const { versions, fetchNextPage, isPending, isFetchingNextPage } =
    usePromptVersions(id);

  const scrollContainer = useScrollEnd(() => {
    if (isPending || isFetchingNextPage) {
      return;
    }
    fetchNextPage();
  }, [fetchNextPage, isFetchingNextPage, isPending]);

  const handleChecked = (version) => {
    setCheckedVersion((pre) => {
      const match = pre.some((item) => item === version.template_version);
      if (match) {
        return pre.filter((item) => item !== version.template_version);
      }
      return [...pre, version.template_version];
    });
  };

  return (
    <Box
      sx={{
        padding: 2,
        display: "flex",
        gap: 2,
        flexDirection: "column",
        overflow: "hidden",
        height: "100%",
      }}
    >
      <Box>
        <Box sx={{ display: "flex", flexDirection: "column", gap: "2px" }}>
          <ShowComponent condition={Boolean(title)}>
            <Typography typography="m3" fontWeight={"fontWeightSemiBold"}>
              {title}
            </Typography>
          </ShowComponent>
          <ShowComponent condition={Boolean(description)}>
            <Typography
              typography="s1"
              fontWeight={"fontWeightRegular"}
              color="text.secondary"
            >
              {description}
            </Typography>
          </ShowComponent>
        </Box>
        <IconButton
          onClick={onClose}
          sx={{ position: "absolute", top: "10px", right: "12px" }}
        >
          <Iconify icon="mingcute:close-line" color="text.primary" />
        </IconButton>
      </Box>
      <ShowComponent condition={!isPending && versions.length === 0}>
        <EmptyVersions />
      </ShowComponent>
      <ShowComponent condition={isPending}>
        <Box
          sx={{
            flex: 1,
            gap: 2,
            flexDirection: "column",
            display: "flex",
            overflowY: "auto",
          }}
        >
          {Array.from({ length: 7 }).map((_, index) => (
            <VersionCardSkeleton key={index} />
          ))}
        </Box>
      </ShowComponent>
      <ShowComponent condition={versions.length > 0}>
        <Box
          sx={{
            flex: 1,
            gap: 2,
            flexDirection: "column",
            display: "flex",
            overflowY: "auto",
          }}
          ref={scrollContainer}
        >
          {versions
            ?.filter((version) => !version?.is_draft || false)
            .map((version, _) => {
              const checked = checkedVersion?.some(
                (item) => item === version.template_version,
              );
              const isDisabled =
                !checked && checkedVersion.length >= maxSelectLimit + 1;
              return (
                <VersionCard
                  key={version.id}
                  version={version}
                  showCheckbox={showCheckbox}
                  showRestore={false}
                  setChecked={() => handleChecked(version)}
                  checked={checked}
                  disableCheckbox={
                    isDisabled || selectedVersion === version?.template_version
                  }
                  checkboxMessage={
                    isDisabled
                      ? "Compare limit is upto 2 version only, Deselect other options to select this one"
                      : ""
                  }
                />
              );
            })}
          <ShowComponent condition={isFetchingNextPage}>
            {Array.from({ length: 3 }).map((_, index) => (
              <VersionCardSkeleton key={index} />
            ))}
          </ShowComponent>
        </Box>
      </ShowComponent>
      <Box sx={{ display: "flex", justifyContent: "flex-end" }}>
        <Button
          variant="contained"
          color="primary"
          sx={{ width: "200px", height: "38px" }}
          onClick={() => {
            setSelectedVersions(checkedVersion);
            onClose();
          }}
          disabled={checkedVersion?.length < 2}
        >
          Compare
        </Button>
      </Box>
    </Box>
  );
};

AddEvalsComparisonChild.propTypes = {
  onClose: PropTypes.func,
  maxSelectLimit: PropTypes.number,
  showCheckbox: PropTypes.bool,
  title: PropTypes.string,
  description: PropTypes.string,
};

const AddEvalsComparison = () => {
  const { compareOpen, setCompareOpen } = useWorkbenchEvaluationContext();
  const onClose = () => setCompareOpen(false);

  return (
    <Drawer
      anchor="right"
      open={compareOpen}
      onClose={onClose}
      PaperProps={{
        sx: {
          height: "100vh",
          position: "fixed",
          zIndex: 9999,
          width: "570px",
          borderRadius: "10px",
          backgroundColor: "background.paper",
        },
      }}
      ModalProps={{
        BackdropProps: {
          style: { backgroundColor: "transparent" },
        },
      }}
    >
      <AddEvalsComparisonChild
        onClose={onClose}
        showCheckbox={compareOpen}
        maxSelectLimit={2}
        title="Add version to compare"
        description="You can select maximum 2 version to compare"
      />
    </Drawer>
  );
};

AddEvalsComparison.propTypes = {};

export default AddEvalsComparison;
