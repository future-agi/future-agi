import React, { useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { notify } from "src/sections/common/ux/toast";
import {
  collabError,
  useTranslate,
  useTranslateInfo,
} from "src/api/develop/row-collab";
import useRowViewUrl from "src/sections/develop-detail/DataTab/DatapointDrawerV2/useRowViewUrl";
import { useDataGridView } from "src/sections/develop-detail/DataTab/DataGrid/useDataGridView";
import TranslateMenu from "./TranslateMenu";
import TranslationBlock from "./TranslationBlock";
import {
  OTHER_LANGUAGE_VIEW,
  sectionFields,
  sectionTargets,
} from "./translateModel";

/**
 * W4-8: "Translate section" for one Row view section. `names` = the
 * section's field names. Returns `{menu, block}` (null when the section's
 * fields offer no target, so the option is absent).
 */
export default function useSectionTranslate(names) {
  const { dataset: datasetId } = useParams();
  const { rowId } = useRowViewUrl();
  const enabled = Boolean(datasetId && rowId && names?.length);
  const info = useTranslateInfo(
    enabled ? datasetId : undefined,
    enabled ? rowId : undefined,
  );
  const translate = useTranslate(datasetId);
  const gridView = useDataGridView(datasetId);
  const [result, setResult] = useState(null);
  const key = (names ?? []).join("|");
  // `key` stands for `names` (a new array each render, the same fields).
  const fields = useMemo(
    () => sectionFields(info.data?.fields, key ? key.split("|") : []),
    [info.data, key],
  );
  const targets = useMemo(
    () => sectionTargets(fields, info.data?.languages),
    [fields, info.data],
  );
  const [forRow, setForRow] = useState(null);
  if (!enabled || !targets.length) return { menu: null, block: null };
  const pick = async (t) => {
    try {
      const res = await translate.mutateAsync({
        row_id: rowId,
        column_ids: t.fields,
        target: t.code,
      });
      setResult(res);
      setForRow(rowId);
    } catch (e) {
      notify(collabError(e, "Couldn't translate this section."), {
        variant: "error",
      });
    }
  };
  // W4 §9 (M6): the live `system:language` view (the server answers it).
  const showOthers = () =>
    gridView.setCollabView({
      key: "system:language",
      value: "__other__",
      label: OTHER_LANGUAGE_VIEW,
    });
  return {
    menu: (
      <TranslateMenu
        targets={targets}
        onPick={pick}
        busy={translate.isPending}
      />
    ),
    block:
      result && forRow === rowId ? (
        <TranslationBlock
          result={result}
          info={info.data}
          onShowOtherRows={showOthers}
          onClose={() => setResult(null)}
        />
      ) : null,
  };
}
