import { describe, expect, it } from "vitest";
import { isPermissionError } from "../useSettingsWriteAccess";

describe("settings error transport", () => {
  it.each([401, 403])(
    "recognizes flattened Axios permission status %s",
    (statusCode) => {
      expect(
        isPermissionError({
          statusCode,
          message: "Write access denied to this workspace",
        }),
      ).toBe(true);
      expect(isPermissionError({ response: { status: statusCode } })).toBe(
        true,
      );
    },
  );
  it.each([400, 404, 500, 502, undefined])(
    "does not treat status %s as a permission denial",
    (statusCode) => {
      expect(isPermissionError({ statusCode })).toBe(false);
    },
  );
});
