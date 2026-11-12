// CloudFront viewer-request function for the DEV frontend only.
// Preserve query strings and real asset/API errors; rewrite only app routes.
function handler(event) {
  var request = event.request;
  var uri = request.uri || "";
  var page = /^\/(auth|dashboard|shared)(\/|$)/.test(uri);
  var file = /\/[^/]*\.[^/]+$/.test(uri);
  if ((request.method === "GET" || request.method === "HEAD") && page && !file) {
    request.uri = "/index.html";
  }
  return request;
}
