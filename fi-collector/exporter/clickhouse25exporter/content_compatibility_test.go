package clickhouse25exporter

import (
	"testing"

	"go.opentelemetry.io/collector/pdata/pcommon"
)

func TestOrdinaryContentRetainsEmptyPlaceholderFallback(t *testing.T) {
	for _, direction := range []string{"input", "output"} {
		keys := inputContentKeys
		if direction == "output" {
			keys = outputContentKeys
		}
		for i, alias := range keys[:len(keys)-1] {
			for _, empty := range []any{"", nil} {
				t.Run(direction+"/"+alias+"/"+fmtValue(empty), func(t *testing.T) {
					row := convertOne(t, contentSpan(func(attrs pcommon.Map) {
						if err := attrs.FromRaw(map[string]any{alias: empty, keys[i+1]: "LEGACY_SENTINEL"}); err != nil {
							t.Fatal(err)
						}
					}))
					if row[direction] != "LEGACY_SENTINEL" {
						t.Fatalf("ordinary alias fallback lost: %s=%q", direction, row[direction])
					}
				})
			}
		}
	}
}

func TestModernContentPresenceBlocksLowerPriorityToolContent(t *testing.T) {
	for _, direction := range []string{"input", "output"} {
		for _, value := range []any{"", nil, "__REDACTED__", "[]"} {
			t.Run(direction+"/"+fmtValue(value), func(t *testing.T) {
				row := convertOne(t, contentSpan(func(attrs pcommon.Map) {
					if err := attrs.FromRaw(map[string]any{
						"gen_ai.operation.name": "execute_tool", "gen_ai." + direction + ".messages": value,
						"gen_ai.tool.call.arguments": "PRIVATE_ARGUMENTS", "anthropic.tool_result.content": "PRIVATE_RESULT",
					}); err != nil {
						t.Fatal(err)
					}
				}))
				if row[direction] != fmtValue(value) {
					t.Fatalf("modern content boundary lost: %s=%q", direction, row[direction])
				}
				other, want := "input", "PRIVATE_ARGUMENTS"
				if direction == "input" {
					other, want = "output", "PRIVATE_RESULT"
				}
				if row[other] != want {
					t.Fatalf("opposite direction lost: %s=%q", other, row[other])
				}
			})
		}
	}
}
