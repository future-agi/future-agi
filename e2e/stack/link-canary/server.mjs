// Owned acceptance-test listener. Its evaluator-facing networks are internal;
// 11.23.0.10 is public-numbered only to exercise the real public-IP policy.
// A separate bridge exposes the host-only journal. No outbound requests are made.
import http from "node:http";

const requests = [];
http
  .createServer((req, res) => {
    const url = new URL(req.url, "http://canary");
    if (req.method === "GET" && url.pathname === "/requests") {
      const prefix = url.searchParams.get("prefix");
      res.setHeader("Content-Type", "application/json");
      res.end(
        JSON.stringify(
          requests.filter((row) => !prefix || row.path.includes(prefix)),
        ),
      );
      return;
    }
    requests.push({
      method: req.method,
      host: req.headers.host,
      path: req.url,
    });
    if (url.pathname.startsWith("/redirect/")) {
      res.writeHead(302, {
        Location: `http://private.example/redirect-target${url.pathname}`,
      });
    } else {
      res.writeHead(200);
    }
    res.end();
  })
  .listen(80, "0.0.0.0");
