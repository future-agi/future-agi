import { useId, useMemo } from "react";
import { useOrganization } from "src/contexts/OrganizationContext";

import {
  useCommandStore,
  useRegisterCommands,
} from "src/sections/common/CommandPalette/commandRegistry";

/** Register only the destinations already permitted by the rail's caller. */
export default function useGatewayCommands(groups) {
  const instanceId = useId();
  const { isReady, currentOrganizationId, orgLevel } = useOrganization();
  const canCreateKey = Boolean(isReady && currentOrganizationId && Number.isFinite(orgLevel) && orgLevel >= 1);
  const sourceId = `gateway-rail:${instanceId}`;
  const commands = useMemo(() => {
    const seen = new Set();
    const ownsCommand = (id) =>
      Object.entries(useCommandStore.getState().sources).find(
        ([, entries]) => entries.some((entry) => entry.id === id),
      )?.[0] === sourceId;
    const entries = groups.flatMap((group) =>
      group.items.flatMap((item) => {
        if (seen.has(item.path)) return [];
        seen.add(item.path);
        const id = `go.gateway:${item.path}`;
        return [
          {
            id,
            group: "Go to",
            label: `Go to ${item.title}`,
            keywords: ["gateway", "open", "navigate", item.title],
            hint: ["AI Gateway", group.subheader].filter(Boolean).join(" · "),
            href: item.path,
            requiresLiveRegistration: true,
            // useRegisterCommands wraps run even for href-only commands.
            run: ({ navigate }) => navigate(item.path),
            // Each rail owns its source, not a peer's lifetime. The first live
            // source for a destination presents it; removing/updating that
            // source immediately lets a still-mounted peer present it instead.
            // Leave launch filtering to the existing registeredCommands helper.
            when: () => ownsCommand(id),
          },
        ];
      }),
    );
    // Match the existing key form's membership gate. This only opens the form;
    // the form and server retain live membership checks and explicit submission.
    if (canCreateKey && seen.has("/dashboard/gateway/keys")) {
      const id = "gateway.create-personal-key";
      const href = "/dashboard/gateway/keys?create=1";
      entries.push({
        id,
        group: "Actions",
        label: "Create personal API key",
        keywords: ["gateway", "create", "new", "token", "key"],
        hint: "Open the API key form",
        href,
        requiresLiveRegistration: true,
        run: ({ navigate }) => navigate(href),
        when: () => ownsCommand(id),
      });
    }
    return entries;
  }, [canCreateKey, groups, sourceId]);

  useRegisterCommands(sourceId, commands, [commands]);
}
