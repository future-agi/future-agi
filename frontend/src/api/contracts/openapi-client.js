import axios from "src/utils/axios";

import { apiPath } from "./api-surface";

export function contractedRequest({
  template,
  method = "get",
  pathParams = {},
  params,
  data,
  config = {},
}) {
  const url = apiPath(template, pathParams);
  return axios.request({
    ...config,
    url,
    method,
    ...(params !== undefined ? { params } : {}),
    ...(data !== undefined ? { data } : {}),
  });
}
