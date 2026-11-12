import React, { useCallback, useEffect, useMemo, useState } from "react";
import { LoadingButton } from "@mui/lab";
import { Box, FormControlLabel, Skeleton, Typography } from "@mui/material";
import PropTypes from "prop-types";
import { FormSearchSelectFieldControl } from "src/components/FromSearchSelectField";
import { paths } from "src/routes/paths";
import FormTextFieldV2 from "src/components/FormTextField/FormTextFieldV2";
import { useKnowledgeBaseList } from "src/api/knowledge-base/files";
import { ShowComponent } from "src/components/show";
import { IOSSwitch } from "src/components/Switch/ISOSwitch";

const DetailForm = ({
  control,
  handleNextTab,
  setFocusField,
  disabledNext,
  knowledgeId,
  rowNumber,
  editMode,
}) => {
  const knowledgeBaseDropdownDisabled = Boolean(knowledgeId);
  const [sameRows, setSameRow] = useState(true);
  const {
    data: knowledgeBaseList,
    isPending: isKnowledgeBaseListPending,
    isFetching: isKnowledgeBaseListFetching,
    refetch: refetchKnowledgeBaseList,
  } = useKnowledgeBaseList("", null);

  // Knowledge bases are created in another tab, so read the list again when
  // the user comes back to this tab and each time the picker gets focus. A
  // browser can bring the tab back without a focus event, and the picker is
  // where the user looks for the knowledge base they just created. A read
  // already running is reused rather than started again.
  const refreshKnowledgeBaseList = useCallback(() => {
    refetchKnowledgeBaseList?.({ cancelRefetch: false });
  }, [refetchKnowledgeBaseList]);

  useEffect(() => {
    const handleVisibilityChange = () => {
      if (document.visibilityState === "visible") refreshKnowledgeBaseList();
    };
    window.addEventListener("focus", refreshKnowledgeBaseList);
    document.addEventListener("visibilitychange", handleVisibilityChange);
    return () => {
      window.removeEventListener("focus", refreshKnowledgeBaseList);
      document.removeEventListener("visibilitychange", handleVisibilityChange);
    };
  }, [refreshKnowledgeBaseList]);

  // A new tab keeps everything typed in this form. It is opened with an
  // opener so it starts with a copy of this tab's sessionStorage, which pins
  // the organization and workspace, and the knowledge base is created where
  // this form reads its list. The opener link is then cut, as noopener would.
  const handleCreateKnowledgeBase = () => {
    const knowledgeBaseTab = window.open(
      paths.dashboard.knowledge_base,
      "_blank",
    );
    if (knowledgeBaseTab) knowledgeBaseTab.opener = null;
  };

  const knowledgeBaseOptions = useMemo(
    () =>
      (knowledgeBaseList || []).map(({ id, name }) => ({
        label: name,
        value: id,
      })),
    [knowledgeBaseList],
  );

  const handleSwitchChange = (event) => {
    setSameRow(event.target.checked);
  };
  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        justifyContent: "space-between",
        height: "calc(100% - 70px)",
      }}
    >
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          gap: "24px",
          overflowY: "auto",
        }}
      >
        <Box sx={{ display: "flex", flexDirection: "column", gap: "2px" }}>
          <Typography
            color="text.primary"
            variant="s1"
            fontWeight={"fontWeightSemiBold"}
          >
            Add details
          </Typography>
          <Box sx={{ display: "flex", gap: 0.5, alignItems: "center" }}>
            <Typography
              variant="s2"
              color="text.secondary"
              fontWeight={"fontWeightRegular"}
            >
              Set up the name, title, description pattern, row count for
              synthetic data
            </Typography>
          </Box>
        </Box>
        <Box sx={{ display: "flex", flexDirection: "column", gap: "16px" }}>
          <Box sx={{ display: "flex", flexDirection: "row", gap: "16px" }}>
            <FormTextFieldV2
              label="Name"
              size="small"
              control={control}
              fieldName="name"
              placeholder="Enter name"
              fullWidth
              required
              onFocus={() => setFocusField("name")}
              onBlur={() => setFocusField("")}
            />
            <ShowComponent condition={isKnowledgeBaseListPending}>
              <Skeleton
                variant="rounded"
                sx={{ borderRadius: "4px" }}
                height="100%"
                width="100%"
              />
            </ShowComponent>
            <ShowComponent condition={!isKnowledgeBaseListPending}>
              <FormSearchSelectFieldControl
                disabled={knowledgeBaseDropdownDisabled}
                label="Knowledge base (optional)"
                placeholder="Select knowledge base here"
                size="small"
                control={control}
                fieldName={`kb_id`}
                fullWidth
                createLabel="Create knowledge base"
                handleCreateLabel={handleCreateKnowledgeBase}
                options={knowledgeBaseOptions}
                noOptions={
                  isKnowledgeBaseListFetching
                    ? "Loading knowledge bases…"
                    : "No knowledge bases yet"
                }
                onFocus={() => {
                  setFocusField("kb_id");
                  refreshKnowledgeBaseList();
                }}
                onBlur={() => setFocusField("")}
              />
            </ShowComponent>
          </Box>
          <FormTextFieldV2
            label="Description"
            control={control}
            fieldName="description"
            fullWidth
            placeholder=" Enter your dataset description."
            multiline
            rows={3}
            required
            onFocus={() => setFocusField("description")}
            onBlur={() => setFocusField("")}
          />
          <FormTextFieldV2
            label="Objective"
            control={control}
            fieldName="useCase"
            fullWidth
            placeholder="Enter objective here"
            multiline
            rows={3}
            onFocus={() => setFocusField("useCase")}
            onBlur={() => setFocusField("")}
          />
          <FormTextFieldV2
            label="Pattern"
            control={control}
            fieldName="pattern"
            fullWidth
            placeholder="Enter pattern here"
            multiline
            rows={3}
            onFocus={() => setFocusField("pattern")}
            onBlur={() => setFocusField("")}
          />
          <Box
            sx={{
              display: "flex",
              flexDirection: "column",
              gap: 1.5,
              border: editMode ? "solid 1px" : "none",
              borderColor: editMode ? "divider" : undefined,
              paddingX: editMode ? 1.5 : 0,
              paddingY: editMode ? 2 : 0,
              borderRadius: "4px",
            }}
          >
            {editMode && (
              <Box
                sx={{
                  display: "flex",
                  flex: "row",
                  justifyContent: "space-between",
                  alignItems: "center",
                }}
              >
                <Typography variant="s1" fontWeight={"Medium"}>
                  Keep no. of rows same
                </Typography>

                <FormControlLabel
                  control={
                    <IOSSwitch
                      checked={sameRows}
                      onChange={handleSwitchChange}
                      sx={{ m: 1 }}
                    />
                  }
                />
              </Box>
            )}

            <FormTextFieldV2
              fieldType="number"
              label="Enter No. of rows"
              size="small"
              disabled={editMode && sameRows}
              control={control}
              fieldName="rowNumber"
              placeholder="Enter number of rows"
              error={rowNumber === 0 || (rowNumber && rowNumber < 10)}
              required
              onFocus={() => setFocusField("rowNumber")}
              onBlur={() => setFocusField("")}
            />
            <Typography
              variant="s3"
              fontWeight={"fontWeightRegular"}
              color={
                rowNumber === 0 || (rowNumber && rowNumber < 10)
                  ? "error.main"
                  : "text.disabled"
              }
            >
              Add a minimum of 10 rows
            </Typography>
          </Box>
        </Box>
      </Box>
      <Box sx={{ padding: "8px 0px 0px", textAlign: "right" }}>
        <LoadingButton
          size="small"
          variant="contained"
          color="primary"
          type="button"
          sx={{ minWidth: "200px" }}
          disabled={disabledNext}
          onClick={() => handleNextTab(1)}
        >
          Next
        </LoadingButton>
      </Box>
    </Box>
  );
};

export default DetailForm;

DetailForm.propTypes = {
  control: PropTypes.any,
  handleNextTab: PropTypes.func,
  knowledgeId: PropTypes.string,
  setFocusField: PropTypes.func,
  disabledNext: PropTypes.bool,
  rowNumber: PropTypes.string,
  editMode: PropTypes.bool,
};
