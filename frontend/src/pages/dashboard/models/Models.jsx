import {
  Box,
  Button,
  Card,
  CircularProgress,
  InputAdornment,
  LinearProgress,
  TextField,
} from "@mui/material";
import React, { useState } from "react";
import { Helmet } from "react-helmet-async";
import Iconify from "src/components/iconify";
import ModelsTable from "./ModelsTable";
import { HeaderComponent } from "src/sections/HeaderComponent";
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { ModelsNoData } from "./ModelsNoData";
import { Xwrapper } from "react-xarrows";
import CreateModelModal from "./CreateModelModal";

function Models() {
  const [searchQuery, setSearchQuery] = useState("");

  const [showCreateModal, setShowCreateModal] = useState(false);

  const [paginationModel, setPaginationModel] = useState({
    page: 0,
    pageSize: 10,
  });

  const [sortModel, setSortModel] = useState([
    {
      field: "name",
    },
  ]);

  const { isLoading, data } = useQuery({
    queryKey: [
      "models",
      paginationModel.page,
      sortModel?.[0]?.sort,
      searchQuery,
    ],
    queryFn: () =>
      axios.get(endpoints.model.list, {
        params: {
          page: paginationModel.page + 1,
          sort_order: sortModel?.[0]?.sort,
          search_query: searchQuery,
        },
      }),
    select: (d) => d.data,
  });

  return (
    <>
      <Helmet>
        <title>Models</title>
      </Helmet>

      <HeaderComponent
        links={[
          {
            name: "Model",
            href: "/",
          },
        ]}
      />
      <Xwrapper>
        <Box sx={{ paddingX: "20px" }}>
          <Card>
            <Box sx={{ padding: 2.5, display: "flex", gap: 2, width: "100%" }}>
              <TextField
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                size="small"
                sx={{ flex: 1 }}
                placeholder="Search"
                InputProps={{
                  startAdornment: (
                    <InputAdornment position="start">
                      <Iconify
                        icon="eva:search-fill"
                        sx={{ color: "primary.main" }}
                      />
                    </InputAdornment>
                  ),
                  endAdornment: isLoading ? (
                    <InputAdornment position="end">
                      <CircularProgress size={20} color="primary" />
                    </InputAdornment>
                  ) : (
                    <></>
                  ),
                }}
              />
              <Button
                id="add-models-button"
                variant="contained"
                color="primary"
                onClick={() => {
                  // trackEvent(Events.createModelStart);
                  setShowCreateModal(true);
                }}
              >
                Add Model
              </Button>
            </Box>
            {isLoading && <LinearProgress />}
            {!isLoading && !data?.results?.length && <ModelsNoData />}
            {!isLoading && Boolean(data?.results?.length) && (
              <ModelsTable
                modelData={data}
                isLoading={isLoading}
                paginationModel={paginationModel}
                setPaginationModel={setPaginationModel}
                sortModel={sortModel}
                setSortModel={setSortModel}
              />
            )}
          </Card>
          <CreateModelModal
            open={showCreateModal}
            onClose={() => setShowCreateModal(false)}
          />
        </Box>
      </Xwrapper>
    </>
  );
}

export default Models;
