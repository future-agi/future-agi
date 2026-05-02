import { Box } from "@mui/material";
import { useInfiniteQuery } from "@tanstack/react-query";
import React, { useMemo, useRef, useState } from "react";
import { useDebounce } from "src/hooks/use-debounce";
import axios, { endpoints } from "src/utils/axios";
import PropTypes from "prop-types";
import ShowModelDropdown from "./ShowModelDropdown";
import SearchField from "./SearchField";
import KeysDrawer from "./KeysDrawer";
import { ShowComponent } from "../show";
import ModelButtonField from "./ModelButtonField";
import AddCustomModal from "src/pages/dashboard/settings/AddCustomModal";

const CustomModelDropdown = ({
  isModalContainer = false,
  buttonTitle = "Select Model",
  buttonIcon,
  searchDropdown,
  disabledClick = false,
  disabledHover = false,
  onChange,
  value,
  hoverPlacement = "bottom",
  modelDetail,
  openSelectModel = false,
  setOpenSelectModel,
  onModelConfigOpen,
  inputSx,
  showButtons = false,
  excludeCustomProviders = false,
  onClick = () => {},
  extraParams = {},
  customTrigger,
  onModelTypeChange,
  modelType,
  // Filter ``shrink`` out of ``rest`` — see rhf-text-field.jsx note.
  // eslint-disable-next-line no-unused-vars
  shrink: _shrink,
  ...rest
}) => {
  const btnRef = useRef(null);
  const inputRef = useRef(null);
  const [isFocus, setIsFocus] = useState(false);
  const [openDropdown, setOpenDropdown] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const search = useDebounce(searchQuery.trim(), 400);

  const [modalState, setModalState] = useState({
    type: null, // 'keys' | 'custom'
    data: null,
    isOpen: false,
  });

  const onConfigOpen = (model) => {
    if (onModelConfigOpen) {
      onModelConfigOpen(model);
      return;
    }
    setModalState((existing) => ({
      ...existing,
      type: "keys",
      data: model,
      isOpen: true,
    }));
  };

  const handleCustomModelClick = () => {
    setModalState((existing) => ({
      ...existing,
      type: "keys",
      isOpen: true,
    }));
    setOpenDropdown(false);
  };

  const {
    data: modelList,
    isLoading: isLoadingModelList,
    fetchNextPage,
    isFetchingNextPage,
  } = useInfiniteQuery({
    queryKey: [
      "model-list",
      search,
      excludeCustomProviders,
      JSON.stringify(extraParams || {}),
    ],
    queryFn: ({ pageParam }) =>
      axios.get(endpoints.develop.modelList, {
        params: {
          page: pageParam,
          search: search,
          ...extraParams,
          ...(excludeCustomProviders && {
            exclude_providers: "custom",
          }),
        },
      }),
    getNextPageParam: (o) => (o.data.next ? o.data.current_page + 1 : null),
    initialPageParam: 1,
  });

  const options = useMemo(() => {
    const filtered =
      modelList?.pages.reduce(
        (acc, curr) => [...acc, ...curr.data.results],
        [],
      ) || [];
    return filtered.length > 0
      ? filtered
      : search
        ? [{ label: "No model found", value: "no", disabled: true }]
        : [{ label: "No model provided", value: "no", disabled: true }];
  }, [modelList?.pages, search]);

  const handleOnChange = (e, clear) => {
    if (rest?.multiple) {
      if (clear) {
        onChange({ target: { value: [] } });
      } else {
        onChange(e);
      }
      return;
    }
    let value = {};
    if (clear) {
      value = {
        model_name: "",
        providers: "",
        isAvailable: false,
        logoUrl: "",
        id: "",
        type: "",
      };
    } else {
      const { modelName, providers, isAvailable, logoUrl, id, type } =
        e.target.option;
      value = {
        model_name: modelName,
        providers,
        isAvailable,
        logoUrl,
        id,
        type,
      };
    }

    onChange({ target: { value: value } });
  };

  useMemo(() => {
    if (!openDropdown) {
      setSearchQuery("");
    }
  }, [openDropdown]);

  useMemo(() => {
    if (openSelectModel) {
      setOpenDropdown(true);
    }
  }, [openSelectModel]);

  const id = useMemo(
    () => (openDropdown || isFocus ? `model-popper` : undefined),
    [isFocus, openDropdown],
  );

  return (
    <>
      <Box sx={{ ...(isModalContainer ? { flexGrow: 1 } : {}) }}>
        <ShowComponent condition={searchDropdown}>
          <Box ref={btnRef} aria-describedby={id} sx={{ display: "block" }}>
            <SearchField
              ref={inputRef}
              label={"Search Model"}
              searchedValue={searchQuery}
              setSearchedValue={setSearchQuery}
              getValue={
                rest?.multiple ? value?.map((v) => v?.value)?.join(", ") : value
              }
              onClick={onClick}
              onChange={handleOnChange}
              isFocus={isFocus}
              setIsFocus={setIsFocus}
              setOpenDropdown={setOpenDropdown}
              openDropdown={openDropdown}
              size="small"
              sx={inputSx}
              logoUrl={modelDetail?.logoUrl}
              InputLabelProps={{
                shrink: true,
                style: {
                  paddingLeft: 2,
                  paddingRight: 2,
                  flexDirection: "row",
                  background: "var(--bg-paper)",
                },
              }}
              {...rest}
            />
          </Box>
        </ShowComponent>
        <ShowComponent condition={!searchDropdown && !customTrigger}>
          <ModelButtonField
            isModalContainer={isModalContainer}
            disabledHover={disabledHover}
            openDropdown={openDropdown}
            value={value}
            modelDetail={modelDetail}
            hoverPlacement={hoverPlacement}
            disabledClick={disabledClick}
            setOpenDropdown={setOpenDropdown}
            buttonIcon={buttonIcon}
            buttonTitle={buttonTitle}
            onClick={onClick}
          />
          <Box
            ref={btnRef}
            aria-describedby={id}
            sx={{ display: "block", height: "0px" }}
          />
        </ShowComponent>
        <ShowComponent condition={!!customTrigger}>
          <Box ref={btnRef} aria-describedby={id}>
            {typeof customTrigger === "function"
              ? customTrigger({
                  openDropdown,
                  setOpenDropdown,
                  value,
                  modelDetail,
                })
              : customTrigger}
          </Box>
        </ShowComponent>
        <ShowModelDropdown
          open={openDropdown}
          onClose={() => {
            setOpenDropdown(false);
            setOpenSelectModel?.(false);
          }}
          id={id}
          searchDropdown={searchDropdown}
          setIsFocus={setIsFocus}
          searchedValue={searchQuery}
          setSearchQuery={setSearchQuery}
          onChange={handleOnChange}
          options={options}
          value={rest?.multiple ? value : value || ""}
          isLoadingModelList={isLoadingModelList}
          fetchNextPage={fetchNextPage}
          isFetchingNextPage={isFetchingNextPage}
          ref={rest?.modelContainerRef ?? btnRef}
          inputRef={inputRef}
          disableClickOutside={modalState.type === "keys"}
          openKeyConfig={modalState.data}
          onConfigOpen={onConfigOpen}
          onCustomModelClick={handleCustomModelClick}
          showIcon={rest?.showIcon}
          iconUrl={modelDetail?.logoUrl}
          showButtons={showButtons}
          onModelTypeChange={onModelTypeChange}
          modelType={modelType}
          {...rest}
        />
      </Box>
      <KeysDrawer
        open={modalState.type === "keys" && modalState.isOpen}
        selectedModel={modalState.type === "keys" ? modalState.data : null}
        onClose={() => {
          setOpenDropdown(true);
          setModalState(() => ({
            type: null,
            data: null,
            isOpen: false,
          }));
        }}
        onAddCustomModel={() => {
          setModalState((existing) => ({
            ...existing,
            type: "custom",
            isOpen: true,
          }));
        }}
      />
      <AddCustomModal
        open={modalState.type === "custom" && modalState.isOpen}
        onClose={() => {
          setModalState((existing) => ({
            ...existing,
            type: "keys",
            isOpen: true,
          }));
        }}
        data={null}
        edit={false}
      />
    </>
  );
};

export default CustomModelDropdown;

CustomModelDropdown.propTypes = {
  isModalContainer: PropTypes.bool,
  buttonTitle: PropTypes.string,
  buttonIcon: PropTypes.any,
  searchDropdown: PropTypes.bool,
  disabledClick: PropTypes.bool,
  disabledHover: PropTypes.bool,
  onChange: PropTypes.func,
  value: PropTypes.string,
  modelDetail: PropTypes.object,
  hoverPlacement: PropTypes.string,
  openSelectModel: PropTypes.bool,
  setOpenSelectModel: PropTypes.func,
  onModelConfigOpen: PropTypes.func,
  inputSx: PropTypes.object,
  showButtons: PropTypes.bool,
  excludeCustomProviders: PropTypes.bool,
  onClick: PropTypes.func,
  extraParams: PropTypes.object,
  customTrigger: PropTypes.oneOfType([PropTypes.node, PropTypes.func]),
  onModelTypeChange: PropTypes.func,
  modelType: PropTypes.string,
};
