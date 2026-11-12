package servertools

import (
	"encoding/json"
	"fmt"
	"math"
	"path"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// Fee units (catalog/servertools/fees.json).
const (
	UnitRequest          = "request"           // one search, fetch or file search call
	UnitQuery            = "query"             // one Gemini search query
	UnitGroundedPrompt   = "grounded_prompt"   // one Gemini prompt answered with grounding
	UnitContainerHour    = "container_hour"    // Anthropic code execution, per container
	UnitContainerSession = "container_session" // an OpenAI container session, billed by the minute
	UnitGBDay            = "gb_day"            // storage: recorded, never billed
)

// Fee tools that are not request tool families: OpenAI's preview search,
// file search and its storage, containers, and Gemini grounding.
const (
	WebSearchPreview  Tool = "web_search_preview"
	FileSearch        Tool = "file_search"
	FileSearchStorage Tool = "file_search_storage"
	Container         Tool = "container"
	GoogleSearch      Tool = "google_search"
	GoogleMaps        Tool = "google_maps"
)

// Fee is one row of the fee table: what provider charges per unit of tool
// for the models Models names.
type Fee struct {
	Provider   string  `json:"provider"`
	Tool       Tool    `json:"tool"`
	Unit       string  `json:"unit"`
	USDPerUnit float64 `json:"usd_per_unit"`
	// Models are globs (path.Match) on the lower-cased model id after its
	// last '/'; none matches every model.
	Models []string `json:"models,omitempty"`
	// MemoryGB is the container size a container_session row prices.
	MemoryGB int `json:"memory_gb,omitempty"`
	// SessionMinutes is the session one container_session price covers.
	SessionMinutes int `json:"session_minutes,omitempty"`
	// MinMinutes is the least a container is billed for.
	MinMinutes int `json:"min_minutes,omitempty"`
	// InputTokensPerUnit adds that many input tokens per unit, priced at
	// the model's input rate.
	InputTokensPerUnit int `json:"input_tokens_per_unit,omitempty"`
	// FreeWith lists request tool versions, "<tool>>=<version>", that make
	// the fee 0.
	FreeWith []string `json:"free_with,omitempty"`
	// Billed false records the fee for estimates only.
	Billed *bool  `json:"billed,omitempty"`
	Note   string `json:"note,omitempty"`
	Source string `json:"source"`

	freeWith []freeRule
}

// IsBilled reports whether the fee is charged (storage is not).
func (f *Fee) IsBilled() bool { return f.Billed == nil || *f.Billed }

// USDPerMinute is a container fee's price per minute.
func (f *Fee) USDPerMinute() float64 {
	switch f.Unit {
	case UnitContainerHour:
		return f.USDPerUnit / 60
	case UnitContainerSession:
		return f.USDPerUnit / float64(f.SessionMinutes)
	}
	return 0
}

// freeRule is one parsed FreeWith entry.
type freeRule struct {
	tool    Tool
	version string
}

// FeeTable is the parsed fee table, as one request sees it: rows whose
// FreeWith the request meets are waived (ForRequest). The zero FeeTable
// prices nothing.
type FeeTable struct {
	fees []Fee
	req  Requested
}

// feeFile is fees.json.
type feeFile struct {
	Version int   `json:"version"`
	Fees    []Fee `json:"fees"`
}

var knownUnits = map[string]bool{
	UnitRequest: true, UnitQuery: true, UnitGroundedPrompt: true,
	UnitContainerHour: true, UnitContainerSession: true, UnitGBDay: true,
}

// ParseFees parses a fee table (catalog/servertools/fees.json). Every row
// needs a provider, a tool, a known unit, a non-negative finite price and a
// source; a container session row also its memory_gb and session_minutes,
// and every glob and free_with entry must parse. Storage (gb_day) is never
// billed, so its row must say billed false.
func ParseFees(data []byte) (FeeTable, error) {
	var f feeFile
	if err := json.Unmarshal(data, &f); err != nil {
		return FeeTable{}, fmt.Errorf("servertools: fee table: %w", err)
	}
	if f.Version != 1 {
		return FeeTable{}, fmt.Errorf("servertools: fee table version %d, want 1", f.Version)
	}
	for i := range f.Fees {
		if err := f.Fees[i].validate(); err != nil {
			return FeeTable{}, fmt.Errorf("servertools: fee %d (%s %s): %w", i, f.Fees[i].Provider, f.Fees[i].Tool, err)
		}
	}
	return FeeTable{fees: f.Fees}, nil
}

func (f *Fee) validate() error {
	switch {
	case f.Provider == "" || f.Tool == "":
		return fmt.Errorf("provider and tool are required")
	case !knownUnits[f.Unit]:
		return fmt.Errorf("unknown unit %q", f.Unit)
	case f.USDPerUnit < 0 || math.IsNaN(f.USDPerUnit) || math.IsInf(f.USDPerUnit, 0):
		return fmt.Errorf("usd_per_unit %v is not a price", f.USDPerUnit)
	case f.Source == "":
		return fmt.Errorf("source is required")
	case f.Unit == UnitContainerSession && (f.MemoryGB <= 0 || f.SessionMinutes <= 0):
		return fmt.Errorf("a container session needs memory_gb and session_minutes")
	case f.Unit == UnitGBDay && f.IsBilled():
		return fmt.Errorf("storage accrues on the store's account and is never billed per request: set billed false")
	case f.MinMinutes < 0 || f.InputTokensPerUnit < 0:
		return fmt.Errorf("min_minutes and input_tokens_per_unit cannot be negative")
	}
	for _, g := range f.Models {
		if _, err := path.Match(g, ""); err != nil {
			return fmt.Errorf("model glob %q: %w", g, err)
		}
	}
	for _, s := range f.FreeWith {
		tool, version, ok := strings.Cut(s, ">=")
		t, v, parsed := ParseTool(tool + "_" + version)
		if !ok || !parsed || t != Tool(tool) {
			return fmt.Errorf("free_with %q is not <tool>>=<yyyymmdd>", s)
		}
		f.freeWith = append(f.freeWith, freeRule{tool: t, version: v})
	}
	return nil
}

// Len is the number of rows.
func (t FeeTable) Len() int { return len(t.fees) }

// ForRequest is t as req sees it: a fee whose free_with the request's own
// tools meet (Anthropic code execution alongside web_search_20260209 or
// later) is waived. It allocates nothing.
func (t FeeTable) ForRequest(req *models.ChatCompletionRequest) FeeTable {
	t.req = Detect(req)
	return t
}

// Lookup is the row that prices tool for provider's model: the first row of
// provider and tool whose globs match model and, for a container, whose
// memory_gb is memoryGB. ok is false when no row prices it.
func (t FeeTable) Lookup(provider string, tool Tool, model string, memoryGB int) (*Fee, bool) {
	bare := bareModel(model)
	for i := range t.fees {
		f := &t.fees[i]
		if f.Provider != provider || f.Tool != tool || (f.MemoryGB != 0 && f.MemoryGB != memoryGB) {
			continue
		}
		if matchesModel(f.Models, bare) {
			return f, true
		}
	}
	return nil, false
}

// Waived reports whether the request t was built for meets f's free_with.
func (t FeeTable) Waived(f *Fee) bool {
	for _, r := range f.freeWith {
		if v := t.req.Version(r.tool); v != "" && v >= r.version {
			return true
		}
	}
	return false
}

// SmallestContainerGB is the smallest container size the table prices for
// provider: the size a container of unknown size is estimated at.
func (t FeeTable) SmallestContainerGB(provider string) int {
	least := 0
	for i := range t.fees {
		f := &t.fees[i]
		if f.Provider == provider && f.Tool == Container && f.MemoryGB > 0 && (least == 0 || f.MemoryGB < least) {
			least = f.MemoryGB
		}
	}
	return least
}

// bareModel is model's id after its last '/', lower case.
func bareModel(model string) string {
	return strings.ToLower(model[strings.LastIndexByte(model, '/')+1:])
}

func matchesModel(globs []string, bare string) bool {
	if len(globs) == 0 {
		return true
	}
	for _, g := range globs {
		if ok, _ := path.Match(g, bare); ok {
			return true
		}
	}
	return false
}
