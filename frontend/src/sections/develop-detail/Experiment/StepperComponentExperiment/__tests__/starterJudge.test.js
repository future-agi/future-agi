import { afterEach, describe, expect, it, vi } from "vitest";
import axios, { endpoints } from "src/utils/axios";
import { strictRouteOf, strictViolations } from "src/api/contracts/strictBody";
import {
  STARTER_JUDGE,
  STARTER_JUDGE_MAPPING,
  ensureStarterJudge,
  starterEvalConfig,
  starterJudgeModelOf,
} from "../starterJudge";

// JRN-EXP-NO-PROMPT-DEADEND: dev's V2 create refuses an experiment with no
// evaluation, so the empty Evaluations step offers one ready judge. The two
// requests are the only seam; the bodies are the module's own.
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, default: { post: vi.fn() } };
});

const CREATE_V2 = "/model-hub/eval-templates/create-v2/";
// What the axios layer rejects with: the response body plus its status.
const refusal = (result) => ({ status: false, result, statusCode: 400 });

afterEach(() => {
  vi.clearAllMocks();
});

describe("starter judge (JRN-EXP-NO-PROMPT-DEADEND)", () => {
  it("FED-J1: is judged by the first prompt run's model, and only a prompt run's", () => {
    expect(
      starterJudgeModelOf([
        { targetType: "agent", agentId: "g", agentVersion: "v3" },
        {
          experimentType: "llm",
          targetType: "prompt",
          promptId: "p",
          model: [{ value: "" }, { value: "gpt-4o-mini" }],
        },
        { targetType: "prompt", promptId: "q", model: [{ value: "gpt-4o" }] },
      ]),
    ).toBe("gpt-4o-mini");
    expect(
      starterJudgeModelOf([
        { experimentType: "tts", model: [{ value: "tts-1" }] },
      ]),
    ).toBeNull();
    expect(starterJudgeModelOf([{ targetType: "http", http: {} }])).toBeNull();
    expect(starterJudgeModelOf([])).toBeNull();
    expect(starterJudgeModelOf(undefined)).toBeNull();
  });

  it("FED-J2: creates a judge that reads the request and the reply, with keys create-v2 accepts", async () => {
    axios.post.mockResolvedValueOnce({
      data: {
        status: true,
        result: { id: "tpl-1", name: STARTER_JUDGE.name, version: "v1" },
      },
    });
    await expect(ensureStarterJudge("gpt-4o-mini")).resolves.toBe("tpl-1");
    expect(axios.post).toHaveBeenCalledTimes(1);
    const [url, body] = axios.post.mock.calls[0];
    expect(url).toBe(endpoints.develop.eval.createEvalTemplateV2);
    expect(body).toEqual({
      name: "answers_the_request",
      eval_type: "llm",
      instructions: STARTER_JUDGE.instructions,
      description: STARTER_JUDGE.description,
      output_type: "pass_fail",
      model: "gpt-4o-mini",
    });
    expect(body.instructions).toContain("{{input}}");
    expect(body.instructions).toContain("{{output}}");
    // create-v2 accepts only lowercase letters, digits, - and _ in a name.
    expect(body.name).toMatch(/^[a-z0-9]+(?:_[a-z0-9]+)*$/);
    // create-v2 refuses an unknown top-level key (reject_unknown_fields).
    expect(strictRouteOf("post", CREATE_V2)).toMatchObject({
      definition: "EvalTemplateCreateV2Request",
    });
    expect(strictViolations("post", CREATE_V2, body)).toEqual([]);
  });

  it("FED-J3: a judge a first experiment already made is used again, not made twice", async () => {
    axios.post
      .mockRejectedValueOnce(
        refusal("An evaluation with this name already exists."),
      )
      .mockResolvedValueOnce({
        data: {
          status: true,
          result: {
            items: [
              { id: "tpl-other", name: "answers_the_request_copy" },
              { id: "tpl-old", name: "answers_the_request" },
            ],
            total: 2,
            page: 0,
            page_size: 25,
          },
        },
      });
    await expect(ensureStarterJudge("gpt-4o")).resolves.toBe("tpl-old");
    expect(axios.post).toHaveBeenCalledTimes(2);
    expect(axios.post.mock.calls[1]).toEqual([
      endpoints.develop.eval.listEvalTemplates,
      {
        page: 0,
        page_size: 25,
        search: "answers_the_request",
        owner_filter: "user",
      },
    ]);
  });

  it("FED-J4: any other refusal reaches the step as it is, with no second request", async () => {
    const error = refusal("Instructions are required.");
    axios.post.mockRejectedValueOnce(error);
    await expect(ensureStarterJudge("gpt-4o")).rejects.toBe(error);
    expect(axios.post).toHaveBeenCalledTimes(1);

    // Same name, but not the caller's to use: said, never a silent pick.
    axios.post
      .mockRejectedValueOnce(
        refusal("An evaluation with this name already exists."),
      )
      .mockResolvedValueOnce({
        data: {
          status: true,
          result: { items: [], total: 0, page: 0, page_size: 25 },
        },
      });
    await expect(ensureStarterJudge("gpt-4o")).rejects.toThrow(
      /answers_the_request already exists/,
    );
  });

  it("FED-J5: the card maps input to the prompt each run sent and output to its reply", () => {
    expect(STARTER_JUDGE_MAPPING).toEqual({
      input: "prompt_chain",
      output: "output",
    });
    expect(
      starterEvalConfig({ templateId: "tpl-1", model: "gpt-4o-mini" }),
    ).toEqual({
      templateId: "tpl-1",
      name: "answers_the_request",
      description: STARTER_JUDGE.description,
      mapping: { input: "prompt_chain", output: "output" },
      requiredKeys: ["input", "output"],
      model: "gpt-4o-mini",
      evalType: "llm",
      config: {},
      evalTemplate: {
        output_type: "Pass/Fail",
        requiredKeys: ["input", "output"],
      },
    });
  });
});
