"""OTLP route source contracts and the pure rendered Gateway API checker.

No Helm, values files, services or database are needed. Source contracts do not
replace the release gate's actual Helm renders for shared/separate OTLP hosts.
"""

import ast
import copy
import re
import unittest
from pathlib import Path

DEPLOY = Path(__file__).resolve().parents[1]
CHART = DEPLOY / "helm/futureagi"
TRACE_PATHS = ("/v1/traces", "/tracer/v1/traces", "/api/public/otel/v1/traces")


def template_source(path):
    text = re.sub(r"\{\{-?\s*/\*.*?\*/\s*-?\}\}", "", path.read_text(), flags=re.S)
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


class OTLPPublicRouteTests(unittest.TestCase):
    def test_ingress_langfuse_exact_collector_route(self):
        text = template_source(CHART / "templates/ingress.yaml")
        declaration = re.search(r"(?m)^\{\{- \$otlpPaths := list (.*?) -\}\}$", text)
        self.assertIsNotNone(declaration)
        self.assertEqual(re.findall(r'"([^"]+)"', declaration[1]), list(TRACE_PATHS))
        self.assertIn('"component" "fi-collector") -}}', text)
        blocks = re.findall(r"\{\{- range \$otlpPaths \}\}(.*?)\{\{- end \}\}", text, re.S)
        self.assertEqual(len(blocks), 2, "shared-host and separate-host branches")
        for block in blocks:
            with self.subTest(block=block):
                self.assertEqual(
                    "\n".join(line.strip() for line in block.strip().splitlines()),
                    "- path: {{ . }}\npathType: Exact\nbackend:\nservice:\n"
                    "name: {{ $collector }}\nport:\nname: otlp-http",
                )
        self.assertIn("if and $i.otlp.enabled (eq $otlpHost $i.api.host)", text)
        self.assertIn("if and $i.otlp.enabled (ne $otlpHost $i.api.host)", text)

    def test_gateway_api_langfuse_exact_collector_route(self):
        text = template_source(CHART / "templates/gateway-api.yaml")
        declaration = re.search(r"(?m)^\{\{- \$otlpMatches := list (.*?) -\}\}$", text)
        self.assertIsNotNone(declaration)
        self.assertEqual(
            re.findall(r'\(dict "path" \(dict "type" "([^"]+)" "value" "([^"]+)"\)\)', declaration[1]),
            [("Exact", path) for path in TRACE_PATHS],
        )
        self.assertIn('"component" "fi-collector") -}}', text)
        self.assertIn(
            '{{- $otlpHttp := dict "name" $collector "port" (int .Values.fiCollector.service.httpPort) -}}',
            text,
        )
        self.assertEqual(
            len(re.findall(
                r"- matches: \{\{- toYaml \$otlpMatches \| nindent 8 \}\}\s+"
                r"backendRefs:\s+- \{\{ toJson \$otlpHttp \}\}", text,
            )),
            2,
        )
        self.assertIn("if and $g.otlp.enabled (eq $otlpHost $apiHost)", text)
        self.assertIn("if and $g.otlp.enabled (ne $otlpHost $apiHost)", text)

    def test_dev_proxy_langfuse_exact_collector_route(self):
        text = template_source(DEPLOY / "dev-api-proxy/default.conf.template")
        locations = re.findall(r"location ~ (\S+) \{(.*?)\n    \}", text, re.S)
        collector = [(route, body) for route, body in locations if "proxy_pass http://dev_fi_collector;" in body]
        self.assertEqual(len(collector), 1)
        pattern, body = collector[0]
        route = re.compile(pattern)
        for path in TRACE_PATHS:
            with self.subTest(path=path):
                self.assertIsNotNone(route.search(path))
                for rejected in (path + "/", path + "/extra", "/extra" + path):
                    self.assertIsNone(route.search(rejected))
        for path in ("/api/public/health", "/api/public/traces", "/api/public/otel/v1/logs", "/v1/metrics"):
            with self.subTest(path=path):
                self.assertIsNone(route.search(path))
        for directive in (
            "client_max_body_size ${DEV_API_PROXY_OTLP_MAX_BODY_SIZE};",
            "proxy_request_buffering off;",
            "proxy_set_header Authorization $http_authorization;",
            "proxy_set_header Content-Type $http_content_type;",
            "proxy_set_header Content-Encoding $http_content_encoding;",
            "proxy_connect_timeout ${DEV_API_PROXY_CONNECT_TIMEOUT};",
            "proxy_read_timeout ${DEV_API_PROXY_READ_TIMEOUT};",
            "proxy_send_timeout ${DEV_API_PROXY_SEND_TIMEOUT};",
        ):
            self.assertIn(directive, body)
        self.assertNotIn("proxy_pass_request_headers off;", text)
        self.assertNotRegex(body, r"(?i)proxy_set_header\s+x-project-name\s")
        self.assertIn("server fi-collector:4318 resolve;", text)


def gateway_checker():
    # Load only these pure functions, not module-level chart I/O or PyYAML.
    path = CHART / "hack/rendered_checks.py"
    tree = ast.parse(path.read_text(), filename=str(path))
    names = {"check_gateway_api", "component", "containers", "pod_spec", "env_values"}
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    if {node.name for node in functions} != names:
        raise AssertionError("rendered checker functions are missing")
    namespace = {}
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(path), "exec"), namespace)
    return namespace["check_gateway_api"]


def gateway_documents():
    return [
        {
            "kind": "HTTPRoute", "metadata": {"name": "test-api"},
            "spec": {"rules": [
                {
                    "matches": [{"path": {"type": "Exact", "value": path}} for path in TRACE_PATHS],
                    "backendRefs": [{"name": "test-fi-collector", "port": 4318}],
                },
                {"matches": [{"path": {"type": "PathPrefix", "value": "/ws/"}}], "timeouts": {"request": "24h"}},
            ]},
        },
        {
            "kind": "GRPCRoute", "metadata": {"name": "test-otlp-grpc"},
            "spec": {"rules": [{"backendRefs": [{"name": "test-fi-collector", "port": 4317}]}]},
        },
        {
            "kind": "HTTPRoute", "metadata": {"name": "test-llm-gateway"},
            "spec": {"hostnames": ["llm.futureagi.example.com"]},
        },
        {
            "kind": "Deployment",
            "metadata": {"labels": {"app.kubernetes.io/component": "backend"}},
            "spec": {"template": {"spec": {"containers": [{"env": [
                {"name": "AGENTCC_GATEWAY_PUBLIC_URL", "value": "https://llm.futureagi.example.com"},
            ]}]}}},
        },
        {
            "kind": "NetworkPolicy", "metadata": {"name": "test-public"},
            "spec": {"podSelector": {"matchExpressions": [{"values": ["agentcc-gateway"]}]}},
        },
    ]


class RenderedOTLPRouteTests(unittest.TestCase):
    def test_accepts_langfuse_alias_with_canonical_routes(self):
        self.assertEqual(gateway_checker()(gateway_documents()), [])

    def test_requires_alias_exact_match_collector_and_port(self):
        check = gateway_checker()
        fixture = gateway_documents()
        for mutation in ("missing-alias", "prefix", "backend", "port", "extra-path"):
            with self.subTest(mutation=mutation):
                docs = copy.deepcopy(fixture)
                rule = docs[0]["spec"]["rules"][0]
                if mutation == "missing-alias":
                    rule["matches"].pop()
                elif mutation == "prefix":
                    rule["matches"][-1]["path"]["type"] = "PathPrefix"
                elif mutation == "backend":
                    rule["backendRefs"][0]["name"] = "test-backend"
                elif mutation == "port":
                    rule["backendRefs"][0]["port"] = 80
                else:
                    rule["matches"].append({"path": {"type": "Exact", "value": "/api/public/health"}})
                self.assertIn(
                    "gateway-api: OTLP/HTTP is not routed on exact paths to the collector's 4318",
                    check(docs),
                )


if __name__ == "__main__":
    unittest.main()
