import type { AxiosResponse } from "axios";

/** The mutator forwards Axios's response, including its non-Fetch headers. */
export type ApiResponseHeaders = AxiosResponse<unknown>["headers"];

/** TResponse is the generated data/status/headers envelope, not just its body. */
export declare function apiMutator<
  TResponse extends {
    data: unknown;
    status: number;
    headers: ApiResponseHeaders;
  },
>(url: string, options: RequestInit): Promise<TResponse>;

export default apiMutator;
