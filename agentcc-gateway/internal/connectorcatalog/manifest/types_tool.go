package manifest

import "encoding/json"

// Tool sources and postures.
const (
	ToolsUpstreamMCP = "upstream_mcp"
	ToolsOperations  = "operations"
	ToolsPartner     = "partner"

	PostureAuto = "auto"
	PostureAsk  = "ask"
	PostureDeny = "deny"

	// GeneratedToolsFile is the one file name tools.generated may name.
	GeneratedToolsFile = "tools.generated.json"
)

// Tools describes a provider's tools.
type Tools struct {
	Source         string    `json:"source" schema:"ref=toolsSource"`
	DefaultPosture string    `json:"defaultPosture" schema:"ref=posture"`
	Snapshot       *Snapshot `json:"snapshot,omitempty"`
	Items          []Tool    `json:"items,omitempty" schema:"len=0..128"`
	Generated      string    `json:"generated,omitempty" schema:"const=tools.generated.json"`
}

// Snapshot is a probe of the live tools/list (the probe's canonical form).
type Snapshot struct {
	Count     int    `json:"count" schema:"range=0..1000"`
	ToolsHash string `json:"toolsHash" schema:"ref=toolsHash"`
	Era       string `json:"era" schema:"ref=era"`
	TakenAt   string `json:"takenAt" schema:"ref=date"`
}

// Tool is one tool of a provider. Scopes is nil when the key is absent and
// empty when the tool needs no scope.
type Tool struct {
	Name             string          `json:"name" schema:"ref=toolName"`
	Title            string          `json:"title,omitempty" schema:"len=1..100"`
	Description      string          `json:"description,omitempty" schema:"len=1..1000"`
	Annotations      Annotations     `json:"annotations"`
	Posture          string          `json:"posture,omitempty" schema:"ref=posture"`
	Scopes           []string        `json:"scopes,omitempty" schema:"ref=scope,len=0..32,unique"`
	Operation        *Operation      `json:"operation,omitempty"`
	InputSchema      json.RawMessage `json:"inputSchema,omitempty" schema:"object"`
	OutputSchema     json.RawMessage `json:"outputSchema,omitempty" schema:"object"`
	Response         *Response       `json:"response,omitempty"`
	Generic          bool            `json:"generic,omitempty"`
	GenericArguments []string        `json:"genericArguments,omitempty" schema:"len=1..4,unique,elen=1..64"`
}

// EffectivePosture is the tool's posture, or the provider default.
func (t *Tool) EffectivePosture(defaultPosture string) string {
	if t.Posture != "" {
		return t.Posture
	}
	return defaultPosture
}

// Annotations are the four MCP tool hints, all explicit.
type Annotations struct {
	ReadOnlyHint    bool `json:"readOnlyHint"`
	DestructiveHint bool `json:"destructiveHint"`
	IdempotentHint  bool `json:"idempotentHint"`
	OpenWorldHint   bool `json:"openWorldHint"`
}

// Operation is the REST call a rest provider's tool makes.
type Operation struct {
	Method string `json:"method" schema:"ref=httpMethod"`
	Path   string `json:"path" schema:"ref=operationPath"`
}

// Response shapes a rest tool's result.
type Response struct {
	Unwrap   string   `json:"unwrap,omitempty" schema:"ref=dottedPath"`
	Fields   []string `json:"fields,omitempty" schema:"ref=dottedPath,len=0..64,unique"`
	MaxBytes int      `json:"maxBytes,omitempty" schema:"range=1..1048576"`
}

// ToolsGenerated is a rest provider's tools.generated.json.
type ToolsGenerated struct {
	Schema string        `json:"schema" schema:"const=connector-manifest/v1#tools-generated"`
	Spec   GeneratedSpec `json:"spec"`
	Tools  []Tool        `json:"tools" schema:"len=1..128"`
}

// GeneratedSpec names the spec the tools were generated from; it must equal
// the provider's upstream.spec url and sha256.
type GeneratedSpec struct {
	URL    string `json:"url" schema:"ref=httpsUrl"`
	SHA256 string `json:"sha256" schema:"ref=sha256Hex"`
}
