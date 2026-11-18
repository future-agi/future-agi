import type { MutableRefObject, RefCallback } from "react";

type MergeableRef<T> =
  | RefCallback<T>
  | MutableRefObject<T | null>
  | null
  | undefined;

export function mergeRefs<T>(...refs: MergeableRef<T>[]): RefCallback<T> {
  return (node) => {
    refs.forEach((ref) => {
      if (typeof ref === "function") {
        ref(node);
      } else if (ref != null) {
        ref.current = node;
      }
    });
  };
}
