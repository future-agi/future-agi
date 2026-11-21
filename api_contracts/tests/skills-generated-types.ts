// Compile with tsc --strict --noEmit to pin the generated wire types.
import type {
  SkillsHubCompareResultApi,
  SkillsHubDraftOutApi,
  SkillsHubFileChunkApi,
  SkillsHubSkillDetailApi,
  SkillsHubSkillSummaryApi,
} from "../../frontend/src/generated/api-contracts/api.schemas";

type CompareResult = NonNullable<SkillsHubCompareResultApi>;
const comparison: SkillsHubCompareResultApi = null;
const base: SkillsHubDraftOutApi["base_version"] = null;
const from: CompareResult["from"] = 1;
const to: CompareResult["to"] = "draft";
const author: SkillsHubSkillSummaryApi["updated_by"] = null;
const latest: SkillsHubSkillDetailApi["latest"] = null;
const end: SkillsHubFileChunkApi["next_offset"] = null;
// @ts-expect-error Comparison refs are not arbitrary JSON objects.
const invalidRef: CompareResult["from"] = {};
// @ts-expect-error The only string comparison ref is draft.
const invalidLabel: CompareResult["to"] = "production";
// @ts-expect-error Draft bases are numbers or null, not strings.
const invalidBase: SkillsHubDraftOutApi["base_version"] = "1";
void [
  comparison,
  base,
  from,
  to,
  author,
  latest,
  end,
  invalidRef,
  invalidLabel,
  invalidBase,
];

type Detail = import("../../frontend/src/generated/api-contracts/api.schemas").SkillsContextDetailApi;
type Loads = import("../../frontend/src/generated/api-contracts/api.schemas").LatestLoadResultApi;
const nullableDetail: Pick<Detail, "latest" | "own_draft"> = { latest: null, own_draft: null };
const nullableReads: Loads = { load: null, fetch: null };
// @ts-expect-error A nullable load still has to be present.
const absentLoad: Loads = { fetch: null };
// @ts-expect-error A nullable draft is not an arbitrary object.
const wrongDraft: Detail["own_draft"] = {};
void [nullableDetail, nullableReads, absentLoad, wrongDraft];
