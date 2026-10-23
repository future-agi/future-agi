package cache

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"reflect"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"sync"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/cache"
)

// In-process stand-ins for the vector databases the semantic cache can use.
//
// Each fake stores what the backend's Set sends and evaluates the filter the
// backend's Search sends, the way the real service would. A fake that returned
// a canned hit could not tell a backend that scopes its query from one that
// does not, which is the only thing these tests are here to tell apart.
// Anything the fake does not understand is a 400, so a backend that starts
// sending a new filter shape fails loudly instead of matching everything.

type fakePoint struct {
	id      string
	vector  []float32
	payload map[string]any
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}

// payloadEqual compares a stored payload value with a filter operand after
// both have been through JSON, so 3 and 3.0 are the same and "3" is not.
func payloadEqual(stored, want any) bool {
	a, _ := json.Marshal(stored)
	b, _ := json.Marshal(want)
	var x, y any
	_ = json.Unmarshal(a, &x)
	_ = json.Unmarshal(b, &y)
	return reflect.DeepEqual(x, y)
}

func asFloat(v any) (float64, bool) {
	switch n := v.(type) {
	case float64:
		return n, true
	case int64:
		return float64(n), true
	case int:
		return float64(n), true
	case json.Number:
		f, err := n.Float64()
		return f, err == nil
	}
	return 0, false
}

// --- Qdrant ---------------------------------------------------------------

type fakeQdrant struct {
	mu     sync.Mutex
	points map[string]*fakePoint
	// lastMust is the filter of the last search; indexes are the payload
	// fields an index was requested for.
	lastMust []map[string]json.RawMessage
	indexes  []string
	srv      *httptest.Server
}

var qdrantUUID = regexp.MustCompile(`^(?:[0-9a-fA-F]{32}|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})$`)

func newFakeQdrant(t *testing.T) *fakeQdrant {
	f := &fakeQdrant{points: map[string]*fakePoint{}}
	f.srv = httptest.NewServer(http.HandlerFunc(f.serve))
	t.Cleanup(f.srv.Close)
	return f
}

func (f *fakeQdrant) serve(w http.ResponseWriter, r *http.Request) {
	f.mu.Lock()
	defer f.mu.Unlock()
	switch {
	case r.Method == http.MethodGet && strings.Count(r.URL.Path, "/") == 2:
		writeJSON(w, 200, map[string]any{"result": map[string]any{"points_count": len(f.points)}})
	case r.Method == http.MethodPut && strings.HasSuffix(r.URL.Path, "/points"):
		var body struct {
			Points []struct {
				ID      any            `json:"id"`
				Vector  []float32      `json:"vector"`
				Payload map[string]any `json:"payload"`
			} `json:"points"`
		}
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			writeJSON(w, 400, map[string]any{"status": map[string]any{"error": err.Error()}})
			return
		}
		for _, p := range body.Points {
			// Qdrant accepts an unsigned integer or a UUID as a point id and
			// rejects anything else, so a cache key that stops being
			// UUID-shaped breaks this backend.
			id, ok := p.ID.(string)
			if !ok || !qdrantUUID.MatchString(id) {
				writeJSON(w, 400, map[string]any{"status": map[string]any{"error": fmt.Sprintf("bad point id %v", p.ID)}})
				return
			}
			f.points[strings.ToLower(strings.ReplaceAll(id, "-", ""))] = &fakePoint{id: id, vector: p.Vector, payload: p.Payload}
		}
		writeJSON(w, 200, map[string]any{"status": "ok"})
	case r.Method == http.MethodPost && strings.HasSuffix(r.URL.Path, "/points/search"):
		var body struct {
			Vector         []float32 `json:"vector"`
			Limit          int       `json:"limit"`
			ScoreThreshold float64   `json:"score_threshold"`
			Filter         struct {
				Must []map[string]json.RawMessage `json:"must"`
			} `json:"filter"`
		}
		dec := json.NewDecoder(r.Body)
		dec.UseNumber()
		if err := dec.Decode(&body); err != nil {
			writeJSON(w, 400, map[string]any{"status": map[string]any{"error": err.Error()}})
			return
		}
		f.lastMust = body.Filter.Must
		var best *fakePoint
		var bestScore float64
		for _, p := range f.points {
			ok, err := qdrantMatches(p.payload, body.Filter.Must)
			if err != nil {
				writeJSON(w, 400, map[string]any{"status": map[string]any{"error": err.Error()}})
				return
			}
			if !ok {
				continue
			}
			s := cache.CosineSimilarity(body.Vector, p.vector)
			if s >= body.ScoreThreshold && s > bestScore {
				best, bestScore = p, s
			}
		}
		res := []map[string]any{}
		if best != nil {
			res = append(res, map[string]any{"id": best.id, "score": bestScore, "payload": best.payload})
		}
		writeJSON(w, 200, map[string]any{"result": res})
	case r.Method == http.MethodPut && strings.HasSuffix(r.URL.Path, "/index"):
		var body struct {
			FieldName   string `json:"field_name"`
			FieldSchema string `json:"field_schema"`
		}
		_ = json.NewDecoder(r.Body).Decode(&body)
		f.indexes = append(f.indexes, body.FieldName+":"+body.FieldSchema)
		writeJSON(w, 200, map[string]any{"result": map[string]any{"status": "acknowledged"}})
	case r.Method == http.MethodPut:
		writeJSON(w, 200, map[string]any{"result": true})
	default:
		writeJSON(w, 404, map[string]any{"status": map[string]any{"error": "not found: " + r.Method + " " + r.URL.Path}})
	}
}

func qdrantMatches(payload map[string]any, must []map[string]json.RawMessage) (bool, error) {
	for _, cond := range must {
		var key string
		if err := json.Unmarshal(cond["key"], &key); err != nil || key == "" {
			return false, fmt.Errorf("condition without key: %v", cond)
		}
		switch {
		case cond["match"] != nil:
			var m struct {
				Value any `json:"value"`
			}
			if err := json.Unmarshal(cond["match"], &m); err != nil {
				return false, err
			}
			if v, ok := payload[key]; !ok || !payloadEqual(v, m.Value) {
				return false, nil
			}
		case cond["range"] != nil:
			var rg struct {
				Gt *float64 `json:"gt"`
			}
			if err := json.Unmarshal(cond["range"], &rg); err != nil || rg.Gt == nil {
				return false, fmt.Errorf("unsupported range %s", cond["range"])
			}
			v, ok := asFloat(payload[key])
			if !ok || !(v > *rg.Gt) {
				return false, nil
			}
		default:
			return false, fmt.Errorf("unsupported condition %v", cond)
		}
	}
	return true, nil
}

// --- Pinecone -------------------------------------------------------------

type fakePinecone struct {
	mu      sync.Mutex
	vectors map[string]*fakePoint
	// lastFilter is the metadata filter of the last query.
	lastFilter map[string]any
	srv        *httptest.Server
}

func newFakePinecone(t *testing.T) *fakePinecone {
	f := &fakePinecone{vectors: map[string]*fakePoint{}}
	f.srv = httptest.NewServer(http.HandlerFunc(f.serve))
	t.Cleanup(f.srv.Close)
	return f
}

func (f *fakePinecone) serve(w http.ResponseWriter, r *http.Request) {
	f.mu.Lock()
	defer f.mu.Unlock()
	switch {
	case r.URL.Path == "/vectors/upsert":
		var body struct {
			Vectors []struct {
				ID       string         `json:"id"`
				Values   []float32      `json:"values"`
				Metadata map[string]any `json:"metadata"`
			} `json:"vectors"`
		}
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			writeJSON(w, 400, map[string]any{"message": err.Error()})
			return
		}
		for _, v := range body.Vectors {
			f.vectors[v.ID] = &fakePoint{id: v.ID, vector: v.Values, payload: v.Metadata}
		}
		writeJSON(w, 200, map[string]any{"upsertedCount": len(body.Vectors)})
	case r.URL.Path == "/query":
		var body struct {
			Vector []float32      `json:"vector"`
			TopK   int            `json:"topK"`
			Filter map[string]any `json:"filter"`
		}
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			writeJSON(w, 400, map[string]any{"message": err.Error()})
			return
		}
		f.lastFilter = body.Filter
		type match struct {
			p     *fakePoint
			score float64
		}
		var ms []match
		for _, p := range f.vectors {
			ok, err := pineconeMatches(p.payload, body.Filter)
			if err != nil {
				writeJSON(w, 400, map[string]any{"message": err.Error()})
				return
			}
			if ok {
				ms = append(ms, match{p, cache.CosineSimilarity(body.Vector, p.vector)})
			}
		}
		sort.Slice(ms, func(i, j int) bool { return ms[i].score > ms[j].score })
		if body.TopK > 0 && len(ms) > body.TopK {
			ms = ms[:body.TopK]
		}
		out := []map[string]any{}
		for _, m := range ms {
			out = append(out, map[string]any{"id": m.p.id, "score": m.score, "metadata": m.p.payload})
		}
		writeJSON(w, 200, map[string]any{"matches": out})
	case r.URL.Path == "/describe_index_stats":
		writeJSON(w, 200, map[string]any{"totalVectorCount": len(f.vectors)})
	default:
		writeJSON(w, 404, map[string]any{"message": "not found: " + r.URL.Path})
	}
}

// pineconeMatches evaluates Pinecone's metadata filter language for the
// operators the backend uses: field-level $eq and $gt, implicitly ANDed, plus
// an explicit $and.
func pineconeMatches(meta map[string]any, filter map[string]any) (bool, error) {
	for field, cond := range filter {
		if field == "$and" {
			list, ok := cond.([]any)
			if !ok {
				return false, fmt.Errorf("$and wants a list")
			}
			for _, sub := range list {
				m, ok := sub.(map[string]any)
				if !ok {
					return false, fmt.Errorf("$and member is not an object")
				}
				if ok, err := pineconeMatches(meta, m); err != nil || !ok {
					return ok, err
				}
			}
			continue
		}
		ops, ok := cond.(map[string]any)
		if !ok {
			return false, fmt.Errorf("filter on %q is not an operator object", field)
		}
		for op, want := range ops {
			got, present := meta[field]
			switch op {
			case "$eq":
				if !present || !payloadEqual(got, want) {
					return false, nil
				}
			case "$gt":
				g, ok1 := asFloat(got)
				w, ok2 := asFloat(want)
				if !ok1 || !ok2 || !(g > w) {
					return false, nil
				}
			default:
				return false, fmt.Errorf("unsupported operator %q", op)
			}
		}
	}
	return true, nil
}

// --- Weaviate -------------------------------------------------------------

type weaviateOperand struct {
	Path     string
	Operator string
	Text     *string
	Int      *int64
}

type fakeWeaviate struct {
	mu         sync.Mutex
	class      string
	classMade  bool
	properties map[string]map[string]any // name -> property definition
	objects    []*fakePoint
	// lastOperands is the where clause of the last Get query, as parsed.
	lastOperands []weaviateOperand
	srv          *httptest.Server
}

func newFakeWeaviate(t *testing.T, class string) *fakeWeaviate {
	f := &fakeWeaviate{class: class, properties: map[string]map[string]any{}}
	f.srv = httptest.NewServer(http.HandlerFunc(f.serve))
	t.Cleanup(f.srv.Close)
	return f
}

var (
	wvNearVector = regexp.MustCompile(`nearVector:\s*\{vector:\s*(\[[^\]]*\]),\s*certainty:\s*([0-9.]+)\s*\}`)
	wvOperands   = regexp.MustCompile(`operands:\s*\[`)
	wvOperand    = regexp.MustCompile(`^\s*\{\s*path:\s*\["([A-Za-z_]+)"\],\s*operator:\s*([A-Za-z]+),\s*(valueText|valueInt):\s*("(?:[^"\\]|\\.)*"|-?\d+)\s*\}\s*`)
)

func (f *fakeWeaviate) serve(w http.ResponseWriter, r *http.Request) {
	f.mu.Lock()
	defer f.mu.Unlock()
	switch {
	case r.Method == http.MethodGet && r.URL.Path == "/v1/schema/"+f.class:
		if !f.classMade {
			writeJSON(w, 404, map[string]any{})
			return
		}
		props := []map[string]any{}
		for _, p := range f.properties {
			props = append(props, p)
		}
		writeJSON(w, 200, map[string]any{"class": f.class, "properties": props})
	case r.Method == http.MethodPost && r.URL.Path == "/v1/schema":
		var body struct {
			Class      string           `json:"class"`
			Properties []map[string]any `json:"properties"`
		}
		_ = json.NewDecoder(r.Body).Decode(&body)
		f.classMade = true
		for _, p := range body.Properties {
			f.properties[fmt.Sprint(p["name"])] = p
		}
		writeJSON(w, 200, body)
	case r.Method == http.MethodPost && r.URL.Path == "/v1/schema/"+f.class+"/properties":
		var p map[string]any
		_ = json.NewDecoder(r.Body).Decode(&p)
		name := fmt.Sprint(p["name"])
		if _, exists := f.properties[name]; exists {
			writeJSON(w, 422, map[string]any{"error": []map[string]any{{"message": "property already exists"}}})
			return
		}
		f.properties[name] = p
		writeJSON(w, 200, p)
	case r.Method == http.MethodPost && r.URL.Path == "/v1/objects":
		var body struct {
			Class      string         `json:"class"`
			Vector     []float32      `json:"vector"`
			Properties map[string]any `json:"properties"`
		}
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			writeJSON(w, 400, map[string]any{"error": err.Error()})
			return
		}
		f.objects = append(f.objects, &fakePoint{vector: body.Vector, payload: body.Properties})
		writeJSON(w, 200, map[string]any{"id": strconv.Itoa(len(f.objects))})
	case r.Method == http.MethodPost && r.URL.Path == "/v1/graphql":
		var body struct {
			Query string `json:"query"`
		}
		_ = json.NewDecoder(r.Body).Decode(&body)
		if strings.Contains(body.Query, "Aggregate") {
			writeJSON(w, 200, map[string]any{"data": map[string]any{"Aggregate": map[string]any{
				f.class: []map[string]any{{"meta": map[string]any{"count": len(f.objects)}}},
			}}})
			return
		}
		f.search(w, body.Query)
	default:
		writeJSON(w, 404, map[string]any{"error": "not found: " + r.Method + " " + r.URL.Path})
	}
}

func graphqlError(w http.ResponseWriter, msg string) {
	// Weaviate answers a GraphQL error with 200 and an errors array.
	writeJSON(w, 200, map[string]any{"errors": []map[string]any{{"message": msg}}})
}

func (f *fakeWeaviate) search(w http.ResponseWriter, q string) {
	nv := wvNearVector.FindStringSubmatch(q)
	if nv == nil {
		graphqlError(w, "no nearVector")
		return
	}
	var vec []float32
	if err := json.Unmarshal([]byte(nv[1]), &vec); err != nil {
		graphqlError(w, "bad vector: "+err.Error())
		return
	}
	certainty, _ := strconv.ParseFloat(nv[2], 64)

	loc := wvOperands.FindStringIndex(q)
	if loc == nil {
		graphqlError(w, "no where operands")
		return
	}
	rest := q[loc[1]:]
	var ops []weaviateOperand
	for {
		m := wvOperand.FindStringSubmatchIndex(rest)
		if m == nil {
			break
		}
		path, op, kind, lit := rest[m[2]:m[3]], rest[m[4]:m[5]], rest[m[6]:m[7]], rest[m[8]:m[9]]
		o := weaviateOperand{Path: path, Operator: op}
		if kind == "valueText" {
			if !strings.HasPrefix(lit, `"`) {
				graphqlError(w, "valueText must be a string")
				return
			}
			var s string
			if err := json.Unmarshal([]byte(lit), &s); err != nil {
				graphqlError(w, "bad string literal: "+err.Error())
				return
			}
			o.Text = &s
		} else {
			n, err := strconv.ParseInt(lit, 10, 64)
			if err != nil {
				graphqlError(w, "valueInt must be an int")
				return
			}
			o.Int = &n
		}
		ops = append(ops, o)
		rest = rest[m[1]:]
		if strings.HasPrefix(rest, ",") {
			rest = rest[1:]
			continue
		}
		break
	}
	// The operand list must close right after the last operand; anything else
	// means a value escaped its literal and rewrote the clause.
	if !strings.HasPrefix(strings.TrimSpace(rest), "]") || len(ops) == 0 {
		graphqlError(w, "syntax error in where operands near: "+rest)
		return
	}
	f.lastOperands = ops

	var best *fakePoint
	var bestCert float64
	for _, obj := range f.objects {
		ok := true
		for _, o := range ops {
			v, present := obj.payload[o.Path]
			switch {
			case o.Operator == "Equal" && o.Text != nil:
				s, isStr := v.(string)
				ok = present && isStr && s == *o.Text
			case o.Operator == "GreaterThan" && o.Int != nil:
				n, isNum := asFloat(v)
				ok = present && isNum && n > float64(*o.Int)
			default:
				graphqlError(w, "unsupported operand "+o.Operator)
				return
			}
			if !ok {
				break
			}
		}
		if !ok {
			continue
		}
		// Weaviate certainty for cosine distance is (1 + cos) / 2.
		c := (1 + cache.CosineSimilarity(vec, obj.vector)) / 2
		if c >= certainty && c > bestCert {
			best, bestCert = obj, c
		}
	}
	items := []map[string]any{}
	if best != nil {
		items = append(items, map[string]any{
			"cache_key":     best.payload["cache_key"],
			"response_json": best.payload["response_json"],
			"_additional":   map[string]any{"certainty": bestCert},
		})
	}
	writeJSON(w, 200, map[string]any{"data": map[string]any{"Get": map[string]any{f.class: items}}})
}
