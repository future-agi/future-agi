import { Box, LinearProgress, Stack, Typography } from "@mui/material";
import React, { useMemo, useState } from "react";
import { useApiKeysStatus } from "src/api/model/api-keys";
import PropTypes from "prop-types";
import KeyCard from "src/sections/develop-detail/Common/ConfigureKeys/KeyCard";
import FormSearchField from "../FormSearchField/FormSearchField";
import {
  useDeleteApiKey,
  DELETE_MODAL_TYPE,
} from "src/hooks/use-delete-api-key";
import { ConfirmDialog } from "src/components/custom-dialog";
import { LoadingButton } from "@mui/lab";
import SvgColor from "../svg-color";

export default function ConfigureKeysContent({
  shouldFetch,
  cols = 2,
  gridSx,
  selectedModel,
}) {
  const [searchQuery, setSearchQuery] = useState("");

  const { data, isFetching, isLoading } = useApiKeysStatus({
    enabled: shouldFetch,
  });

  const {
    openDeleteModal,
    setOpenDeleteModal,
    handleDeleteApiKey,
    isDeleting,
  } = useDeleteApiKey();

  // const filteredData = useMemo(() => {
  //   if (!Array.isArray(data) || data.length === 0) return [];
  //   return data.filter((d) =>
  //     d.display_name.toLowerCase().includes(searchQuery.toLowerCase())
  //   );
  // }, [data, searchQuery]);

  // Function to filter keys based on displayName
  const filteredData = useMemo(() => {
    const search = searchQuery.toLowerCase();
    const selectedProvider = selectedModel?.providers?.toLowerCase();

    return (data || [])
      .filter((d) => d.display_name.toLowerCase().includes(search))
      .sort((a, b) => {
        if (!selectedProvider) return 0;
        const aMatch = a.provider.toLowerCase() === selectedProvider;
        const bMatch = b.provider.toLowerCase() === selectedProvider;
        return aMatch === bMatch ? 0 : aMatch ? -1 : 1;
      });
  }, [searchQuery, data, selectedModel?.providers]);

  return (
    <Stack direction={"column"}>
      {isLoading ? (
        <LinearProgress />
      ) : (
        <>
          <FormSearchField
            fullWidth
            size="small"
            placeholder="Search API provider"
            searchQuery={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            sx={{
              width: "100%",
              position: "sticky",
              top: 0,
              zIndex: 2,
              backgroundColor: "background.paper",
              paddingBottom: (theme) => theme.spacing(2),
              "& .MuiOutlinedInput-root": {
                boxShadow: "none",
                "& fieldset": {
                  borderColor: "action.hover",
                  borderRadius: (theme) => theme.spacing(0.5),
                },
                "&:hover fieldset": {
                  borderColor: "action.hover",
                  boxShadow: "none",
                  outline: "none",
                },
                "&.Mui-focused fieldset": {
                  borderColor: "action.hover",
                  boxShadow: "none",
                  outline: "none",
                },
                "&.Mui-focused": {
                  boxShadow: "none",
                },
                "& input": {
                  boxShadow: "none",
                  outline: "none",
                },
              },
            }}
          />

          <Box
            sx={{
              flexGrow: 1,
              minHeight: "300px",
              display: "grid",
              gridTemplateColumns: `repeat(${cols}, 1fr)`,
              gap: (theme) => theme.spacing(2),
              overflowY: "auto",
              ...gridSx,
            }}
          >
            {filteredData?.length > 0 ? (
              filteredData.map((d) => (
                <KeyCard
                  key={d?.id}
                  data={d}
                  isFetching={isFetching}
                  onDeleteClick={() =>
                    setOpenDeleteModal({
                      id: d?.id,
                      type: DELETE_MODAL_TYPE.NORMAL,
                    })
                  }
                />
              ))
            ) : (
              <Typography
                sx={{ textAlign: "center", gridColumn: "span 2", padding: 2 }}
              >
                No API keys found.
              </Typography>
            )}
          </Box>
        </>
      )}
      <ConfirmDialog
        content="Are you sure you want to delete this API key?"
        action={
          <LoadingButton
            loading={isDeleting}
            size="small"
            variant="contained"
            color="error"
            onClick={() => handleDeleteApiKey()}
            sx={{ color: "common.white" }}
            startIcon={
              <SvgColor
                // @ts-ignore
                width={2}
                sx={{ mt: -0.5 }}
                src={"/assets/icons/ic_delete.svg"}
              />
            }
          >
            Delete
          </LoadingButton>
        }
        open={!!openDeleteModal}
        onClose={() => setOpenDeleteModal(null)}
        title="Delete API key"
      />
    </Stack>
  );
}

ConfigureKeysContent.propTypes = {
  shouldFetch: PropTypes.bool,
  cols: PropTypes.number,
  gridSx: PropTypes.object,
  selectedModel: PropTypes.object,
};
