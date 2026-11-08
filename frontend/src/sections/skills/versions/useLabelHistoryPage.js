import { endpoints } from "src/api/skills/endpoints";
import { skillsKeys } from "src/api/skills/keys";
import { mapLabelEvent, mapPage } from "src/api/skills/mappers";
import { useSkillV11Query } from "src/api/skills/testing";

export default function useLabelHistoryPage(id, label, page, pageSize = 25) {
  return useSkillV11Query({
    parts: ["legacy", ...skillsKeys.labelHistory(id, label).slice(1), { page, pageSize }],
    url: () => endpoints.labelHistory(id),
    mapper: (data) => mapPage(data, mapLabelEvent),
    params: { label: label || undefined, page, page_size: pageSize },
    enabled: Boolean(id),
  });
}
