// A failed evaluation's stored reason becomes one plain
// sentence by its kind; raw server text (launcher logs, stack frames) never
// shows. The staging link's stored error was the launcher sample below.
import { describe, expect, it } from "vitest";
import { failedEvalSentence } from "../cellCommon";

const NOT_STARTED = "The code sandbox could not start; ask your administrator.";
const UNREACHABLE =
  "The code sandbox could not be reached; ask your administrator.";
const LAUNCHER_RAW =
  "Code eval input validation failed: No output. Exit code: 255.\n" +
  "[E][2026-11-11T05:40:12+0000][1] runChild():486 Launching child process failed";

describe("failedEvalSentence", () => {
  it("says the sandbox could not start for the raw launcher error", () => {
    expect(failedEvalSentence(LAUNCHER_RAW)).toBe(NOT_STARTED);
    expect(failedEvalSentence("Launching child process failed")).toBe(
      NOT_STARTED,
    );
  });

  it("does not blame the sandbox when the user's own code failed", () => {
    // The executor prefixes every no-output failure the same way.
    expect(
      failedEvalSentence("Code eval input validation failed: No output"),
    ).toBeNull();
    expect(
      failedEvalSentence(
        "Code eval input validation failed: No output. Exit: 1. NameError: name x is not defined",
      ),
    ).toBeNull();
    expect(failedEvalSentence("No output. Exit code: 255.")).not.toBe(
      NOT_STARTED,
    );
  });

  it("treats infrastructure one-liners as raw", () => {
    expect(
      failedEvalSentence(
        "Sandbox error: [Errno 13] Permission denied: '/sandbox/scripts/eval_1.py'",
      ),
    ).toBeNull();
    expect(failedEvalSentence('Invalid JSON: {"a": 1')).toBeNull();
    expect(failedEvalSentence("Code executor error: HTTP 500")).toBeNull();
    expect(failedEvalSentence("ValueError {'detail': 'x'}")).toBeNull();
  });

  it("says the sandbox could not be reached when the executor is down", () => {
    expect(
      failedEvalSentence(
        "Code executor unavailable: the code-executor service could not be reached",
      ),
    ).toBe(UNREACHABLE);
  });

  it("keeps a short plain sentence after `The evaluation failed:`", () => {
    expect(failedEvalSentence("The judge returned no score")).toBe(
      "The evaluation failed: The judge returned no score",
    );
  });

  it("adds nothing for the server's generic failure texts", () => {
    expect(failedEvalSentence("Native evaluator execution failed")).toBeNull();
    expect(
      failedEvalSentence("Native terminal prompt_chain is unavailable"),
    ).toBeNull();
  });

  it("shows nothing for raw or long text", () => {
    expect(failedEvalSentence("x".repeat(161))).toBeNull();
    expect(failedEvalSentence("handler():12 failed")).toBeNull();
    expect(failedEvalSentence("first line\nsecond line")).toBeNull();
    expect(failedEvalSentence('{"error": "boom"}')).toBeNull();
    expect(
      failedEvalSentence('Traceback (most recent call last): File "a.py"'),
    ).toBeNull();
  });

  it("shows nothing when no reason is stored", () => {
    expect(failedEvalSentence(undefined)).toBeNull();
    expect(failedEvalSentence(null)).toBeNull();
    expect(failedEvalSentence("   ")).toBeNull();
  });
});
