import type { ComponentType } from "react";
import { VersionBadge as VersionBadgeJs } from "../EnvVersionPin";
import { lockedVersionSplitLabel as lockedVersionSplitLabelJs } from "../workspace.constants";

// The locked version a run of a split ran under, as the run reads return it.
export interface RunLockedVersion {
  id: string;
  label: string;
  locked: boolean;
}

export type Split = "train" | "test";

interface LockedVersionSplitTagProps {
  lockedVersion: RunLockedVersion | null;
  split: Split | null;
}

// The JS modules this tag uses, typed here for what it passes them.
const VersionBadge = VersionBadgeJs as unknown as ComponentType<{
  label: string;
}>;
const lockedVersionSplitLabel = lockedVersionSplitLabelJs as unknown as (
  lockedVersion: RunLockedVersion | null,
  split: Split | null,
) => string;

// Names the locked version a run ran under and the split it ran.
export default function LockedVersionSplitTag({
  lockedVersion,
  split,
}: LockedVersionSplitTagProps) {
  const label = lockedVersionSplitLabel(lockedVersion, split);
  return label ? <VersionBadge label={label} /> : null;
}
