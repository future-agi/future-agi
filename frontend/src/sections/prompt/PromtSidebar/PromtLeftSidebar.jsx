import React, { useState, useCallback, useMemo } from "react";
import {
  Box,
  Button,
  InputAdornment,
  TextField,
  Typography,
  IconButton,
  List,
  ListItem,
  ListItemText,
  ListItemIcon,
} from "@mui/material";
import debounce from "lodash/debounce";
import Iconify from "src/components/iconify";
import { useNavigate } from "react-router";
import PropTypes from "prop-types";
import { useMutation, useQuery } from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";
import axios, { endpoints } from "src/utils/axios";
import { trackEvent, Events, PropertyName } from "src/utils/Mixpanel";
import { useScrollEnd } from "src/hooks/use-scroll-end";
import { useDebounce } from "src/hooks/use-debounce";

const page_size = 20;

const PromtLeftSidebar = ({
  onDelete,
  currentIndex,
  setCurrentIndex,
  closeSidebar,
  fromLeftMenu = false,
  setVersionList,
  setVersionIndex,
}) => {
  const navigate = useNavigate();
  const [searchQuery, setSearchQuery] = useState("");
  const [pageNumber, setPageNumber] = useState(0);
  const [promptList, setPromptList] = useState([]);
  const debouncedSearchQuery = useDebounce(searchQuery.trim(), 500);

  const { data, isPending } = useQuery({
    queryKey: ["prompts", pageNumber, debouncedSearchQuery],
    queryFn: async () => {
      const res = await axios.get(
        endpoints.develop.runPrompt.promptExecutions(),
        {
          params: { page: pageNumber + 1, name: searchQuery, page_size },
        },
      );
      return res.data;
    },
  });

  const ref = useScrollEnd(() => {
    if (data.next) {
      setPageNumber((pre) => pre + 1);
    }
  }, [data]);

  useMemo(() => {
    const arr = data?.results || [];
    setPromptList((pre) => (pageNumber > 0 ? [...pre, ...arr] : [...arr]));
  }, [data?.results, pageNumber]);

  const { mutate: createDraft } = useMutation({
    mutationFn: (body) =>
      axios.post(endpoints.develop.runPrompt.createPromptDraft, body),
    onSuccess: (data) => {
      enqueueSnackbar("Prompt created successfully.", {
        variant: "success",
      });
      const titleName = data?.data?.result;
      setPromptList((prev) => [
        { name: titleName.name, id: titleName.id },
        ...prev,
      ]);
      navigate(`/dashboard/prompt/add/${data?.data?.result?.rootTemplate}`);
    },
  });

  const handleCreateDraft = () => {
    const payload = {
      name: "",
      prompt_config: [
        {
          messages: [
            {
              role: "system",
              content: [
                {
                  type: "text",
                  text: "",
                },
              ],
            },
            {
              role: "user",
              content: [
                {
                  type: "text",
                  text: "",
                },
              ],
            },
          ],
        },
      ],
    };
    createDraft(payload);
    if (fromLeftMenu) {
      setVersionList && setVersionList([{ templateVersion: "V1" }]);
      setVersionIndex && setVersionIndex(0);
    }
  };

  // eslint-disable-next-line react-hooks/exhaustive-deps
  const debounceTrackEvent = useCallback(
    debounce((value) => {
      trackEvent(Events.savedPromptSearched, {
        [PropertyName.formFields]: { query: value },
      });
    }, 500),
    [],
  );

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        flex: "1",
      }}
    >
      <Typography
        color="text.disabled"
        textTransform={"uppercase"}
        typography={"subheader"}
        margin={"8px 0 22px 0"}
        padding={"0 12px"}
      >
        All prompts
      </Typography>

      {/* search bar */}
      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          padding: "0 12px",
        }}
      >
        <TextField
          size="small"
          placeholder="Search prompts"
          sx={{ width: "100%" }}
          defaultValue={searchQuery}
          onChange={(e) => {
            debounceTrackEvent(e.target.value);
            setSearchQuery(e.target.value);
            setPageNumber(0);
          }}
          InputProps={{
            startAdornment: (
              <InputAdornment position="start">
                <Iconify
                  icon="solar:magnifier-linear"
                  width={16}
                  sx={{ color: "text.disabled" }}
                />
              </InputAdornment>
            ),
          }}
        />
      </Box>

      {/* list */}
      <Box overflow={"auto"} ref={ref} flex={1} mb={"12px"}>
        <List
          sx={{
            my: "20px",
            px: "12px",
            py: 0,
            display: "flex",
            flexDirection: "column",
            gap: "4px",
            overflow: "auto",
            flex: "1",
          }}
        >
          {promptList?.map((title, index) => (
            <ListItem
              key={title?.id}
              onClick={() => {
                navigate(`/dashboard/prompt/add/${title?.id}`, {
                  state: { title: title?.name },
                });
                setCurrentIndex(index);
                if (fromLeftMenu) {
                  closeSidebar();
                }
              }}
              sx={{
                display: "flex",
                backgroundColor: `${index === currentIndex ? "action.selected" : "transparent"}`,
                color: "text.primary",
                alignItems: "center",
                padding: "0 8px",
                minHeight: 28,
                borderRadius: "6px",
                cursor: "pointer",
                "&:hover": {
                  backgroundColor:
                    index === currentIndex ? "action.selected" : "action.hover",
                },
                "&:hover .row-hover-action, &:focus-within .row-hover-action": {
                  opacity: 1,
                },
              }}
              secondaryAction={
                <IconButton
                  size="small"
                  className="row-hover-action"
                  onClick={(e) => {
                    e.stopPropagation();
                    onDelete(title?.id);
                  }}
                >
                  <Iconify
                    width={16}
                    sx={{
                      cursor: "pointer",
                      "&:hover": {
                        color: "error.main",
                      },
                    }}
                    icon="solar:trash-bin-trash-bold"
                    color="text.disabled"
                  />
                </IconButton>
              }
            >
              <ListItemIcon sx={{ minWidth: 24 }}>
                <Iconify
                  width={16}
                  icon="solar:calendar-linear"
                  color={`${index === currentIndex ? "primary.main" : "text.disabled"}`}
                />
              </ListItemIcon>
              <ListItemText
                primaryTypographyProps={{
                  fontSize: 13,
                  fontWeight: index === currentIndex ? 500 : 400,
                  width: "83%",
                }}
                primary={title?.name}
              />
            </ListItem>
          ))}
          {isPending ? (
            <ListItem>
              <ListItemText
                primaryTypographyProps={{
                  fontSize: 13,
                  fontWeight: "fontWeightRegular",
                  width: "83%",
                  color: "text.disabled",
                }}
                primary={"Loading More..."}
              />
            </ListItem>
          ) : null}
        </List>
      </Box>
      <Button
        variant="contained"
        color="primary"
        onClick={() => {
          setCurrentIndex(0);
          handleCreateDraft();
          if (fromLeftMenu) {
            closeSidebar();
          }
        }}
        sx={{ margin: "0 12px 0 12px" }}
      >
        Add New Prompt
      </Button>
    </Box>
  );
};

export default PromtLeftSidebar;

PromtLeftSidebar.propTypes = {
  onDelete: PropTypes.any,
  currentIndex: PropTypes.number,
  setCurrentIndex: PropTypes.func,
  closeSidebar: PropTypes.func,
  fromLeftMenu: PropTypes.bool,
  setVersionList: PropTypes.func,
  setVersionIndex: PropTypes.func,
};
