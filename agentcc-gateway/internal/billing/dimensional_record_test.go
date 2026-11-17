package billing

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

func TestDimensionalRecordFieldsMatchVersionedSchema(t *testing.T) {
	raw, err := os.ReadFile(filepath.Join("..", "..", "..", "api_contracts", "gateway", "examples", "managed-settlement-v2.schema.json"))
	if err != nil {
		t.Fatal(err)
	}
	var schema struct {
		Required   []string                   `json:"required"`
		Properties map[string]json.RawMessage `json:"properties"`
	}
	if err := json.Unmarshal(raw, &schema); err != nil {
		t.Fatal(err)
	}
	rec := Settlement{RequestID: "synthetic-request", OrgID: "synthetic-org", Dimensional: &DimensionalReceipt{}}
	fields := rec.FieldMap()
	if len(fields) != len(schema.Required) || len(fields) != len(schema.Properties) {
		t.Fatal("v2 field count mismatch")
	}
	for _, key := range schema.Required {
		if _, exists := fields[key]; !exists {
			t.Fatalf("missing field %s", key)
		}
	}
	var version struct {
		Const string `json:"const"`
	}
	_ = json.Unmarshal(schema.Properties["v"], &version)
	if fields["v"] != version.Const {
		t.Fatal("wrong schema version")
	}
}

func TestDimensionalRecordSelectsV2WithoutChangingV1(t *testing.T) {
	rec := Settlement{RequestID: "synthetic-request", OrgID: "synthetic-org"}
	old := rec.FieldMap()
	if old["v"] != "1" || old["dimensions"] != "" {
		t.Fatalf("v1 changed: %+v", old)
	}
	n := int64(2)
	rec.Dimensional = &DimensionalReceipt{Contract: "synthetic-flat-v1", Model: "synthetic/media", Endpoint: "mock", Snapshot: "snapshot-1", State: "measured", Lines: []DimensionalLine{{Unit: "output_image", RateUSD: "0.039", Cap: 2, Quantity: &n}}}
	got := rec.FieldMap()
	if got["v"] != "2" || got["dimensions"] == "" {
		t.Fatalf("v2 not selected: %+v", got)
	}
	for k, v := range old {
		if k != "v" && got[k] != v {
			t.Fatalf("v1 field %s changed", k)
		}
	}
	var receipt DimensionalReceipt
	if err := json.Unmarshal([]byte(got["dimensions"]), &receipt); err != nil {
		t.Fatal(err)
	}
	if receipt.Lines[0].Quantity == nil || *receipt.Lines[0].Quantity != 2 {
		t.Fatalf("lost count %+v", receipt)
	}
	rec.Dimensional.Lines[0].Quantity = nil
	rec.Dimensional.Lines[0].Estimated = true
	rec.Dimensional.State = "estimated_bound"
	var raw map[string]json.RawMessage
	_ = json.Unmarshal([]byte(rec.FieldMap()["dimensions"]), &raw)
	var lines []map[string]any
	_ = json.Unmarshal(raw["lines"], &lines)
	if _, present := lines[0]["quantity"]; present {
		t.Fatal("unknown quantity serialized as measured")
	}
}
