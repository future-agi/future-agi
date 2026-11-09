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
