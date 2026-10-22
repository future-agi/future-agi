package capmock

import (
	"net/http"
	"strings"
)

type handler func(m *Mock, c *call)

type route struct {
	pattern string
	h       handler
}

// fake is one vendor stand-in: its routes under /<name>/, where its vendor
// reports a call's cost (report_cost writes there), and, for a fake with
// async jobs, its vendor's failure status word (job_fail_at).
type fake struct {
	name     string
	costPath string
	failWord string
	routes   []route
}

// allFakes is the vendor list: each route is the vendor's
// own path under the fake's prefix, so an adapter's base URL is
// <mock>/<name>.
func allFakes() []*fake {
	return []*fake{
		{name: "exa", costPath: "costDollars.total", routes: []route{
			{"POST /exa/search", static("exa_search.json")},
			{"POST /exa/contents", static("exa_contents.json")},
		}},
		{name: "parallel", routes: []route{
			{"POST /parallel/v1beta/search", static("parallel_search.json")},
			{"POST /parallel/v1beta/extract", static("parallel_extract.json")},
		}},
		{name: "perplexity", routes: []route{{"POST /perplexity/search", static("perplexity_search.json")}}},
		{name: "tavily", routes: []route{
			{"POST /tavily/search", static("tavily_search.json")},
			{"POST /tavily/extract", static("tavily_extract.json")},
		}},
		{name: "brave", routes: []route{{"GET /brave/res/v1/web/search", static("brave_search.json")}}},
		{name: "searxng", routes: []route{{"GET /searxng/search", searxng}}},
		{name: "firecrawl", routes: []route{{"POST /firecrawl/v2/scrape", static("firecrawl_scrape.json")}}},
		{name: "jina", routes: []route{{"GET /jina/{target...}", jinaReader}}},
		{name: "mistral-ocr", routes: []route{{"POST /mistral-ocr/v1/ocr", static("mistral_ocr.json")}}},
		{name: "azure-di", failWord: "failed", routes: []route{
			{"POST /azure-di/documentintelligence/documentModels/{op}", azureAnalyze},
			{"GET /azure-di/documentintelligence/documentModels/{model}/analyzeResults/{id}", azureResult},
		}},
		{name: "textract", routes: []route{{"POST /textract/", textract}}},
		{name: "deepgram", routes: []route{{"POST /deepgram/v1/listen", static("deepgram_listen.json")}}},
		{name: "assemblyai", failWord: "error", routes: []route{
			{"POST /assemblyai/v2/upload", assemblyUpload},
			{"POST /assemblyai/v2/transcript", assemblyCreate},
			{"GET /assemblyai/v2/transcript/{id}", assemblyPoll},
		}},
		{name: "voyage", routes: []route{{"POST /voyage/v1/embeddings", static("voyage_embeddings.json")}}},
		{name: "cohere", routes: []route{{"POST /cohere/v2/rerank", static("cohere_rerank.json")}}},
		{name: "kernel", routes: []route{
			{"POST /kernel/browsers", kernelCreate},
			{"GET /kernel/browsers/{id}", kernelGet},
			{"DELETE /kernel/browsers/{id}", kernelDelete},
		}},
		{name: "browserbase", routes: []route{
			{"POST /browserbase/v1/sessions", browserbaseCreate},
			{"GET /browserbase/v1/sessions/{id}", browserbaseGet},
			{"POST /browserbase/v1/sessions/{id}", browserbaseUpdate},
		}},
		{name: "sandbox", routes: []route{{"GET /sandbox/v1/usage", static("sandbox_usage.json")}}},
		{name: "fal", failWord: "failed", routes: []route{
			{"POST /fal/{model...}", falSubmit},
			{"GET /fal/{path...}", falGet},
		}},
		{name: "pipedream", routes: []route{{"POST /pipedream/mcp", pipedreamMCP}}},
		{name: "x402", routes: []route{{"GET /x402/weather", x402Seller}}},
		{name: "echo-a", costPath: "usage.cost_usd", routes: []route{{"POST /echo-a/echo", echo}}},
		{name: "echo-b", costPath: "usage.cost_usd", routes: []route{{"POST /echo-b/echo", echo}}},
	}
}

func static(fixture string) handler {
	return func(m *Mock, c *call) { m.render(c, http.StatusOK, fixture, nil) }
}

// searxng answers JSON only for format=json, as an instance does.
func searxng(m *Mock, c *call) {
	if c.r.URL.Query().Get("format") != "json" {
		writeJSON(c.w, http.StatusForbidden, vendorError(403, "format must be json"))
		return
	}
	m.render(c, http.StatusOK, "searxng_search.json", nil)
}

// jinaReader reads the URL after /jina/: JSON with Accept: application/json,
// otherwise Reader's text form.
func jinaReader(m *Mock, c *call) {
	target := c.r.PathValue("target")
	if !strings.HasPrefix(target, "http://") && !strings.HasPrefix(target, "https://") {
		writeJSON(c.w, http.StatusBadRequest, map[string]any{"code": 400, "name": "ParamValidationError", "message": "the path must be a URL"})
		return
	}
	if !strings.Contains(c.r.Header.Get("Accept"), "application/json") {
		c.w.Header().Set("Content-Type", "text/plain; charset=utf-8")
		_, _ = c.w.Write([]byte("Title: Capmock page\n\nURL Source: " + target + "\n\nMarkdown Content:\nExample page text.\n"))
		return
	}
	m.render(c, http.StatusOK, "jina_reader.json", map[string]string{"target": target})
}

// textract serves the JSON 1.1 protocol's DetectDocumentText.
func textract(m *Mock, c *call) {
	if c.r.Header.Get("X-Amz-Target") != "Textract.DetectDocumentText" {
		writeJSON(c.w, http.StatusBadRequest, map[string]string{"__type": "UnknownOperationException"})
		return
	}
	doc, err := m.fill(c, "textract_detect.json", nil)
	if err != nil {
		writeJSON(c.w, 500, vendorError(500, err.Error()))
		return
	}
	c.w.Header().Set("X-Amzn-Requestid", c.id)
	m.respond(c, http.StatusOK, doc)
}

// echo returns the request body with the fake's name; it is the test
// capability the capability core's tests run on.
func echo(m *Mock, c *call) {
	doc, err := m.fill(c, "echo.json", nil)
	if err != nil {
		writeJSON(c.w, 500, vendorError(500, err.Error()))
		return
	}
	doc["echo"] = decodeBody(c.body)
	m.respond(c, http.StatusOK, doc)
}
