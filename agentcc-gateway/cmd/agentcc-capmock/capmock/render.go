package capmock

import (
	"encoding/json"
	"fmt"
	"net/http"
	"strconv"
	"strings"
	"time"
)

// call is one data-plane request to a fake, after its directives.
type call struct {
	w    http.ResponseWriter
	r    *http.Request
	fake *fake
	body []byte
	b    behavior
	id   string
}

// json decodes the request body as an object ({} when it is not one).
func (c *call) json() map[string]any {
	var v map[string]any
	if json.Unmarshal(c.body, &v) != nil || v == nil {
		return map[string]any{}
	}
	return v
}

// query is the search query of the request: its body's query or the q
// parameter.
func (c *call) query() string {
	if q, ok := c.json()["query"].(string); ok {
		return q
	}
	return c.r.URL.Query().Get("q")
}

// render answers status with the filled fixture.
func (m *Mock) render(c *call, status int, fixture string, vars map[string]string) {
	doc, err := m.fill(c, fixture, vars)
	if err != nil {
		writeJSON(c.w, 500, vendorError(500, err.Error()))
		return
	}
	m.respond(c, status, doc)
}

// fill decodes the fixture after filling its {{name}} placeholders with
// JSON-escaped values; id, now, query, base and fake are always set, and
// vars override them.
func (m *Mock) fill(c *call, fixture string, vars map[string]string) (map[string]any, error) {
	raw, err := fixtures.ReadFile("fixtures/" + fixture)
	if err != nil {
		return nil, fmt.Errorf("capmock: missing fixture %s", fixture)
	}
	all := map[string]string{"id": c.id, "now": m.now().UTC().Format(time.RFC3339), "query": c.query(),
		"base": m.baseURL(), "fake": c.fake.name}
	for k, v := range vars {
		all[k] = v
	}
	pairs := make([]string, 0, 2*len(all))
	for k, v := range all {
		esc, _ := json.Marshal(v)
		pairs = append(pairs, "{{"+k+"}}", string(esc[1:len(esc)-1]))
	}
	var doc map[string]any
	if err := json.Unmarshal([]byte(strings.NewReplacer(pairs...).Replace(string(raw))), &doc); err != nil {
		return nil, fmt.Errorf("capmock: fixture %s is not a JSON object after filling", fixture)
	}
	return doc, nil
}

// respond writes doc, with a report_cost amount at the fake's cost path.
func (m *Mock) respond(c *call, status int, doc any) {
	if c.b.reportCost != nil {
		setPath(doc, c.fake.costPath, *c.b.reportCost)
	}
	writeJSON(c.w, status, doc)
}

// setPath sets a dotted path of object keys in doc, creating objects on the
// way.
func setPath(doc any, path string, v any) {
	obj, ok := doc.(map[string]any)
	if !ok {
		return
	}
	keys := strings.Split(path, ".")
	for _, k := range keys[:len(keys)-1] {
		next, ok := obj[k].(map[string]any)
		if !ok {
			next = map[string]any{}
			obj[k] = next
		}
		obj = next
	}
	obj[keys[len(keys)-1]] = v
}

// file is a URL a fake handed out (an upload, a generated image).
type file struct {
	content     []byte
	contentType string
	expires     time.Time // zero: never
}

// mintFile stores content under id and returns its URL, which expires
// after expiry when that is set (the url_expiry directive).
func (m *Mock) mintFile(id string, content []byte, contentType string, expiry time.Duration) string {
	f := file{content: content, contentType: contentType}
	url := m.baseURL() + "/__files/" + id
	if expiry > 0 {
		f.expires = m.now().Add(expiry)
		url += "?expires=" + strconv.FormatInt(f.expires.Unix(), 10)
	}
	m.mu.Lock()
	m.files[id] = f
	m.mu.Unlock()
	return url
}

func (m *Mock) handleFile(w http.ResponseWriter, r *http.Request) {
	m.mu.Lock()
	f, ok := m.files[r.PathValue("id")]
	m.mu.Unlock()
	switch {
	case !ok:
		writeJSON(w, 404, vendorError(404, "no such file"))
	case !f.expires.IsZero() && m.now().After(f.expires):
		writeJSON(w, 403, vendorError(403, "the signed URL has expired"))
	default:
		w.Header().Set("Content-Type", f.contentType)
		_, _ = w.Write(f.content)
	}
}
