package trajectory

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"
	"testing"
	"time"
	"unicode/utf8"

	"github.com/alicebob/miniredis/v2"
	"github.com/redis/go-redis/v9"
)

func TestHashItemIsExactBytes(t *testing.T) {
	a := `{ "content": "Hello, world!", "role": "user" }`
	b := `{"role":"user","content":"Hello, world!"}`
	ha, hb := HashItem(a), HashItem(b)
	if len(ha) != 32 || strings.ToLower(ha) != ha {
		t.Fatalf("hash %q is not 32 lowercase hex chars (FixedString(32))", ha)
	}
	// Key order and whitespace are part of the item: a reader concatenates
	// stored bytes, so different serialisations must never share a key.
	if ha == hb {
		t.Fatalf("different item bytes produced identical hash %s", ha)
	}
	if HashItem(a) != ha {
		t.Fatalf("hash is not deterministic")
	}
	// sha256("") prefix: pins the algorithm and the 16-byte truncation.
	if got := HashItem(""); got != "e3b0c44298fc1c149afbf4c8996fb924" {
		t.Fatalf("HashItem(\"\") = %s", got)
	}
}

func TestTruncateUTF8(t *testing.T) {
	// 4-byte emoji repeated 20 times = 80 bytes.
	s := strings.Repeat("🚀", 20)
	for _, limit := range []int{1, 3, 4, 5, 7, 15, 80, 100} {
		got := TruncateUTF8(s, limit)
		if !utf8.ValidString(got) {
			t.Errorf("limit=%d produced invalid UTF-8: %x", limit, []byte(got))
		}
		if len(got) > limit {
			t.Errorf("limit=%d len(got)=%d > limit", limit, len(got))
		}
	}
	if got := TruncateUTF8("hello", 0); got != "" {
		t.Errorf("limit=0 got %q, want empty", got)
	}
}

func TestExtractSpanFormats(t *testing.T) {
	projectID := "11111111-1111-4111-8111-111111111111"
	sessionID := "22222222-2222-4222-8222-222222222222"
	traceID := "33333333-3333-4333-8333-333333333333"

	t.Run("OpenAIChatObjectWithToolsAndChoices", func(t *testing.T) {
		input := `{
			"model": "gpt-4o",
			"temperature": 0.2,
			"tools": [
				{"type": "function", "function": {"name": "read_file", "description": "Read a file"}}
			],
			"messages": [
				{"role": "system", "content": "You are a helpful coding assistant."},
				{"role": "user", "content": "Fix the bug in main.go"}
			]
		}`
		output := `{
			"id": "chatcmpl-123",
			"choices": [
				{"index": 0, "message": {"role": "assistant", "content": "Let me inspect main.go first."}}
			]
		}`
		ext := ExtractSpan(projectID, sessionID, traceID, input, output, 64)
		if !ext.Applied {
			t.Fatalf("expected Applied=true")
		}
		if ext.ItemScopeID != sessionID {
			t.Fatalf("ItemScopeID = %q, want session %q", ext.ItemScopeID, sessionID)
		}
		if len(ext.InputHashes) != 2 {
			t.Fatalf("InputHashes len = %d, want 2", len(ext.InputHashes))
		}
		if !strings.HasPrefix(ext.InputHashes[0], ProjectHashPrefix) {
			t.Errorf("system message hash %q should have project prefix %q", ext.InputHashes[0], ProjectHashPrefix)
		}
		if strings.HasPrefix(ext.InputHashes[1], ProjectHashPrefix) {
			t.Errorf("user message hash %q should NOT have project prefix", ext.InputHashes[1])
		}
		if len(ext.ToolDefHashes) != 1 || !strings.HasPrefix(ext.ToolDefHashes[0], ProjectHashPrefix) {
			t.Fatalf("ToolDefHashes = %v, want 1 project-prefixed hash", ext.ToolDefHashes)
		}
		if len(ext.OutputHashes) != 1 {
			t.Fatalf("OutputHashes = %v, want 1", ext.OutputHashes)
		}
		if len(ext.InputPreview) > 64 || len(ext.OutputPreview) > 64 {
			t.Fatalf("previews exceed 64B: in=%d out=%d", len(ext.InputPreview), len(ext.OutputPreview))
		}
		env := mustParseEnvelope(t, ext)
		if env.Input == nil || env.Input.Refs != "tmm" || env.Input.SystemHash != "" {
			t.Fatalf("unexpected input template: %+v", env.Input)
		}
		if !strings.Contains(env.Input.Gaps[0], `"model": "gpt-4o"`) {
			t.Errorf("first gap lost the wrapper text verbatim: %q", env.Input.Gaps[0])
		}
		if env.Output == nil || env.Output.Refs != "m" {
			t.Fatalf("unexpected output template: %+v", env.Output)
		}
		requireRoundTrip(t, ext, input, output)
	})

	t.Run("AnthropicSystemAndMessages", func(t *testing.T) {
		input := `{"model":"claude-3-7-sonnet","system":"Follow coding standards.","messages":[{"role":"user","content":"Hello"}]}`
		ext := ExtractSpan(projectID, "", traceID, input, `{"content":"Hi!"}`, 128)
		if !ext.Applied {
			t.Fatalf("expected Applied=true")
		}
		if ext.ItemScopeID != traceID {
			t.Fatalf("when session empty, ItemScopeID = %q, want traceID %q", ext.ItemScopeID, traceID)
		}
		if len(ext.InputHashes) != 1 {
			t.Fatalf("expected [user] = 1 message hash, got %v", ext.InputHashes)
		}
		env := mustParseEnvelope(t, ext)
		if env.Input == nil || env.Input.Refs != "sm" {
			t.Fatalf("unexpected Anthropic template: %+v", env.Input)
		}
		if !strings.HasPrefix(env.Input.SystemHash, ProjectHashPrefix) {
			t.Errorf("top-level system hash %q should be project-scoped", env.Input.SystemHash)
		}
		requireRoundTrip(t, ext, input, `{"content":"Hi!"}`)
	})

	t.Run("OpenAIResponsesAPI", func(t *testing.T) {
		input := `{"model":"gpt-5","instructions":"Be concise.","input":[{"role":"user","content":"Ping"}]}`
		ext := ExtractSpan(projectID, sessionID, traceID, input, "", 128)
		if !ext.Applied {
			t.Fatalf("expected Applied=true")
		}
		env := mustParseEnvelope(t, ext)
		if env.Input == nil || env.Input.Refs != "sm" || env.Output != nil {
			t.Fatalf("unexpected Responses API envelope: %+v", env)
		}
		requireRoundTrip(t, ext, input, "")
	})

	t.Run("TopLevelMessageArray", func(t *testing.T) {
		input := `[{"role":"developer","content":"Strict mode"},{"role":"user","content":"Hi"}]`
		ext := ExtractSpan(projectID, sessionID, traceID, input, "", 128)
		if !ext.Applied {
			t.Fatalf("expected Applied=true")
		}
		if !strings.HasPrefix(ext.InputHashes[0], ProjectHashPrefix) {
			t.Errorf("developer role should be project-scoped, got %q", ext.InputHashes[0])
		}
		env := mustParseEnvelope(t, ext)
		if env.Input == nil || env.Input.Refs != "mm" || env.Input.Gaps[0] != "[" || env.Input.Gaps[1] != "," || env.Input.Gaps[2] != "]" {
			t.Fatalf("expected [m,m] template, got %+v", env.Input)
		}
		requireRoundTrip(t, ext, input, "")
	})

	t.Run("SmartPreviewSurfacesLatestUserTurnPastLargeToolsAndSystem", func(t *testing.T) {
		bigSys := strings.Repeat("System instruction rule. ", 100)
		bigTool := map[string]any{"type": "function", "function": map[string]any{"name": "deploy", "description": strings.Repeat("schema ", 200)}}
		inObj := map[string]any{
			"model": "gpt-4o",
			"tools": []any{bigTool},
			"messages": []any{
				map[string]any{"role": "system", "content": bigSys},
				map[string]any{"role": "user", "content": "Old turn 1 question"},
				map[string]any{"role": "assistant", "content": "Old turn 1 answer"},
				map[string]any{"role": "user", "content": "Debug checkout error 502 right now"},
			},
		}
		inBytes, _ := json.Marshal(inObj)
		ext := ExtractSpan(projectID, sessionID, traceID, string(inBytes), "", DefaultPreviewMaxBytes)
		if !ext.Applied {
			t.Fatalf("expected Applied=true")
		}
		if !strings.Contains(ext.InputPreview, "Debug checkout error 502 right now") {
			t.Fatalf("InputPreview should surface latest user message, got: %q", ext.InputPreview)
		}
		if len(ext.InputPreview) > DefaultPreviewMaxBytes {
			t.Fatalf("InputPreview len=%d exceeds %d", len(ext.InputPreview), DefaultPreviewMaxBytes)
		}
	})

	t.Run("NonConversationSpanUntouched", func(t *testing.T) {
		ext := ExtractSpan(projectID, sessionID, traceID, `{"sql":"SELECT 1"}`, `"ok"`, 128)
		if ext.Applied {
			t.Fatalf("non-conversation span should not be extracted")
		}
	})
}

func TestExtractSpan50TurnAgentLoopCompression(t *testing.T) {
	projectID := "11111111-1111-4111-8111-111111111111"
	systemPrompt := "You are an autonomous software engineering agent. " + strings.Repeat("Rule: always write tests and verify builds cleanly. ", 40)

	tools := make([]map[string]any, 15)
	for i := range tools {
		tools[i] = map[string]any{
			"type": "function",
			"function": map[string]any{
				"name":        fmt.Sprintf("tool_%02d", i),
				"description": strings.Repeat(fmt.Sprintf("Tool %d documentation details. ", i), 15),
				"parameters":  map[string]any{"type": "object", "properties": map[string]any{"arg": map[string]any{"type": "string"}}},
			},
		}
	}

	seen := NewSeenStore(nil, 100_000, time.Hour, time.Hour)
	ctx := context.Background()

	var totalRawBytes int64
	var totalNewItemBytes int64
	var totalReusedItems int
	var totalNewItems int

	msgs := []map[string]any{
		{"role": "system", "content": systemPrompt},
	}

	for turn := 1; turn <= 50; turn++ {
		msgs = append(msgs, map[string]any{
			"role":    "user",
			"content": fmt.Sprintf("Turn %02d user prompt: %s", turn, strings.Repeat("context ", 30)),
		})
		inObj := map[string]any{"model": "claude-sonnet-4-6", "tools": tools, "messages": msgs}
		inBytes, _ := json.Marshal(inObj)
		outMsg := map[string]any{
			"role":    "assistant",
			"content": fmt.Sprintf("Turn %02d assistant reply: %s", turn, strings.Repeat("reasoning ", 30)),
		}
		outObj := map[string]any{"choices": []any{map[string]any{"index": 0, "message": outMsg}}}
		outBytes, _ := json.Marshal(outObj)

		totalRawBytes += int64(len(inBytes) + len(outBytes))

		ext := ExtractSpan(projectID, "sess-1", "trace-1", string(inBytes), string(outBytes), DefaultPreviewMaxBytes)
		if !ext.Applied {
			t.Fatalf("turn %d: expected Applied=true", turn)
		}
		newItems, reused, _ := seen.FilterNew(ctx, ext.Items)
		totalNewItems += len(newItems)
		totalReusedItems += reused
		for _, it := range newItems {
			totalNewItemBytes += int64(len(it.Content))
		}
		msgs = append(msgs, outMsg)
	}

	ratio := float64(totalRawBytes) / float64(totalNewItemBytes)
	t.Logf("50-turn agent loop: raw=%d B, unique_items=%d B, ratio=%.1fx, new_items=%d, reused_items=%d",
		totalRawBytes, totalNewItemBytes, ratio, totalNewItems, totalReusedItems)
	if ratio < 15.0 {
		t.Fatalf("expected >= 15x item compression on 50-turn agent loop, got %.2fx", ratio)
	}

	// Second trace in the SAME project should reuse system prompt + all 15 tools via __project__!
	inObj2 := map[string]any{
		"model": "claude-sonnet-4-6",
		"tools": tools,
		"messages": []map[string]any{
			{"role": "system", "content": systemPrompt},
			{"role": "user", "content": "Brand new session task"},
		},
	}
	inBytes2, _ := json.Marshal(inObj2)
	ext2 := ExtractSpan(projectID, "sess-2", "trace-2", string(inBytes2), "", DefaultPreviewMaxBytes)
	new2, reused2, _ := seen.FilterNew(ctx, ext2.Items)
	if len(new2) != 1 {
		t.Fatalf("second trace: want exactly 1 new item (new user msg), got %d (reused=%d)", len(new2), reused2)
	}
	if reused2 != 16 {
		t.Fatalf("second trace: want 16 reused project items (1 system + 15 tools), got %d", reused2)
	}
}

func TestSeenStoreTwoPodsSharedRedis(t *testing.T) {
	mr := miniredis.RunT(t)
	rdb := redis.NewClient(&redis.Options{Addr: mr.Addr()})
	defer rdb.Close()

	podA := NewSeenStore(rdb, 1000, time.Hour, 24*time.Hour)
	podB := NewSeenStore(rdb, 1000, time.Hour, 24*time.Hour)
	ctx := context.Background()
	tok1, tok2 := SeenCommitterToken("trace-turn-1"), SeenCommitterToken("trace-turn-2")

	pid := "11111111-1111-4111-8111-111111111111"
	turn1In := `{"messages":[{"role":"system","content":"You are helpful."},{"role":"user","content":"Step 1"}]}`
	turn2In := `{"messages":[{"role":"system","content":"You are helpful."},{"role":"user","content":"Step 1"},{"role":"assistant","content":"Done 1"},{"role":"user","content":"Step 2"}]}`

	ext1 := ExtractSpan(pid, "sess-shared", "trace-shared", turn1In, "", 128)
	resA := podA.Check(ctx, ext1.Items, tok1)
	if resA.Count(SeenNew) != 2 || len(resA.Write()) != 2 {
		t.Fatalf("podA turn1: got new=%d write=%d, want 2/2", resA.Count(SeenNew), len(resA.Write()))
	}
	podA.CommitChecked(ctx, resA, SeenStamp{Committer: tok1})
	newA := resA.Write()

	// Turn 2 lands on Pod B (completely cold local LRU!). Redis must prevent double-writing & double-billing.
	ext2 := ExtractSpan(pid, "sess-shared", "trace-shared", turn2In, "", 128)
	resB := podB.Check(ctx, ext2.Items, tok2)
	if resB.Count(SeenNew) != 2 || resB.Count(SeenReuseOther) != 2 || resB.CreditedItemBytes() == 0 || len(resB.Write()) != 2 {
		t.Fatalf("podB turn2: got new=%d reuse_other=%d credited=%d write=%d, want 2/2/>0/2",
			resB.Count(SeenNew), resB.Count(SeenReuseOther), resB.CreditedItemBytes(), len(resB.Write()))
	}
	podB.CommitChecked(ctx, resB, SeenStamp{Committer: tok2})

	// Verify Forget evicts from BOTH local LRU and shared Redis (for ClickHouse fallback).
	podA.Forget(ctx, newA)
	// A fresh Pod C treats the forgotten items as new again.
	podC := NewSeenStore(rdb, 1000, time.Hour, 24*time.Hour)
	if resC := podC.Check(ctx, ext1.Items, tok2); resC.Count(SeenNew) != 2 {
		t.Fatalf("after Forget, podC got classes new=%d of %d, want 2", resC.Count(SeenNew), len(resC.Decisions))
	}

	// Fail open when Redis is unavailable: every item is written again and,
	// because the store epoch cannot be read, nothing is reused, not even what
	// this pod committed itself while Redis was down.
	mr.Close()
	podD := NewSeenStore(rdb, 1000, time.Hour, 24*time.Hour)
	resD1 := podD.Check(ctx, ext1.Items, tok1)
	if resD1.Count(SeenNew) != 2 || !resD1.RedisFailed || !resD1.EpochUnknown {
		t.Fatalf("redis down first call: new=%d redisFailed=%v epochUnknown=%v, want 2/true/true",
			resD1.Count(SeenNew), resD1.RedisFailed, resD1.EpochUnknown)
	}
	podD.CommitChecked(ctx, resD1, SeenStamp{Committer: tok1})
	podD.nowFn = func() time.Time { return time.Now().Add(2 * time.Second) } // past the epoch retry
	if resD2 := podD.Check(ctx, ext1.Items, tok2); resD2.Count(SeenNew) != 2 || len(resD2.Write()) != 2 {
		t.Fatalf("redis down second call: new=%d write=%d, want 2/2 (no reuse without the epoch)",
			resD2.Count(SeenNew), len(resD2.Write()))
	}
}

func TestBatchRestoreInlineAndUsageReport(t *testing.T) {
	row := map[string]any{
		"input":              "preview-in",
		"output":             "preview-out",
		"input_item_hashes":  []string{"abc"},
		"output_item_hashes": []string{"def"},
		"tool_def_hashes":    []string{},
		"item_scope_id":      "sess-1",
		"payload_encoding":   EncodingItemsV1,
		"payload_envelope":   "{}",
		"attributes_extra":   map[string]any{"custom": "keep", "input.value": `{"messages":[{"role":"user","content":"full input"}]}`},
	}

	b := NewBatch()
	promoted := CompactPromoted(row["attributes_extra"].(map[string]any), "input.value", false, true)
	b.AddFallback(SpanFallback{
		Row:           row,
		OrigInput:     `{"messages":[{"role":"user","content":"full input"}]}`,
		OrigOutput:    `{"choices":[{"message":{"role":"assistant","content":"full output"}}]}`,
		PromotedInput: promoted,
	})
	b.RestoreInline()

	if row["input"] != `{"messages":[{"role":"user","content":"full input"}]}` {
		t.Fatalf("OrigInput not restored: %v", row["input"])
	}
	if row["output"] != `{"choices":[{"message":{"role":"assistant","content":"full output"}}]}` {
		t.Fatalf("OrigOutput not restored: %v", row["output"])
	}
	of := row["attributes_extra"].(map[string]any)
	if of["input.value"] != `{"messages":[{"role":"user","content":"full input"}]}` || of["custom"] != "keep" || len(of) != 2 {
		t.Fatalf("promoted attribute not restored exactly: %v", of)
	}
	for _, col := range []string{"input_item_hashes", "output_item_hashes", "tool_def_hashes", "item_scope_id", "payload_encoding", "payload_envelope"} {
		if _, exists := row[col]; exists {
			t.Errorf("column %q still present after RestoreInline", col)
		}
	}

	// Verify UsageReport monotonic invariants:
	// 1. Non-deduped batch bills exact RawBytes.
	r0 := UsageReport{RawBytes: 500, DedupApplied: false}
	if r0.StoredTotalBytes() != 500 || r0.DedupSavedBytes() != 0 {
		t.Fatalf("non-deduped report mismatch: stored=%d saved=%d", r0.StoredTotalBytes(), r0.DedupSavedBytes())
	}
	// 2. Turn 1 (100% new items + overhead > orig) never bills MORE than RawBytes.
	r1 := UsageReport{RawBytes: 21000, OrigExtractedBytes: 20000, KeptSpanOverheadBytes: 1200, NewItemBytes: 19500, DedupApplied: true}
	if r1.StoredTotalBytes() != 21000 || r1.DedupSavedBytes() != 0 {
		t.Fatalf("turn 1 report mismatch: stored=%d saved=%d", r1.StoredTotalBytes(), r1.DedupSavedBytes())
	}
	// 3. Turn 10 (mostly reused) bills exact RawBytes - Saved.
	r10 := UsageReport{RawBytes: 101000, OrigExtractedBytes: 98000, KeptSpanOverheadBytes: 2800, NewItemBytes: 2000, DedupApplied: true}
	if r10.DedupSavedBytes() != 93200 || r10.StoredTotalBytes() != 7800 {
		t.Fatalf("turn 10 report mismatch: stored=%d saved=%d", r10.StoredTotalBytes(), r10.DedupSavedBytes())
	}
}

func TestConfigFromEnv(t *testing.T) {
	env := map[string]string{
		"FI_TRAJECTORY_DEDUP_MODE":     "canary",
		"FI_TRAJECTORY_DEDUP_PROJECTS": " AAAA-1111 , bbbb-2222 ",
		"FI_TRAJECTORY_LRU_SIZE":       "25000",
		"FI_BILL_DEDUPED_BYTES":        "true",
	}
	cfg, err := ConfigFromEnv(Config{}, func(k string) string { return env[k] })
	if err != nil {
		t.Fatalf("ConfigFromEnv: %v", err)
	}
	if cfg.Mode != ModeCanary || cfg.LRUSize != 25000 || !cfg.BillsDedupedBytes() {
		t.Fatalf("unexpected cfg: %+v", cfg)
	}
	if !cfg.Active() || !cfg.EnabledForProject("aaaa-1111") || !cfg.EnabledForProject("BBBB-2222") || cfg.EnabledForProject("cccc-3333") {
		t.Fatalf("canary allowlist mismatch: %+v", cfg.CanaryProjects)
	}

	if _, err := ConfigFromEnv(Config{}, func(k string) string {
		if k == "FI_TRAJECTORY_DEDUP_MODE" {
			return "bogus"
		}
		return ""
	}); err == nil {
		t.Fatalf("expected error for invalid FI_TRAJECTORY_DEDUP_MODE")
	}
}

func TestSeenStoreTTLExpiryAndProjectInvalidation(t *testing.T) {
	now := time.Date(2026, 11, 22, 0, 0, 0, 0, time.UTC)
	store := NewSeenStore(nil, 100, 1*time.Hour, 24*time.Hour)
	store.nowFn = func() time.Time { return now }

	projItem := Item{ProjectID: "p1", ScopeID: ProjectScopeID, ItemHash: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", Content: "sys"}
	sessItem := Item{ProjectID: "p1", ScopeID: "sess-1", ItemHash: "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", Content: "msg"}
	otherItem := Item{ProjectID: "p2", ScopeID: ProjectScopeID, ItemHash: "cccccccccccccccccccccccccccccccc", Content: "other"}

	newItems, reused, _ := store.FilterNew(context.Background(), []Item{projItem, sessItem, otherItem})
	if len(newItems) != 3 || reused != 0 {
		t.Fatalf("first pass: new=%d reused=%d", len(newItems), reused)
	}

	// Within TTL: all 3 reused.
	now = now.Add(30 * time.Minute)
	newItems, reused, _ = store.FilterNew(context.Background(), []Item{projItem, sessItem, otherItem})
	if len(newItems) != 0 || reused != 3 {
		t.Fatalf("within TTL: new=%d reused=%d", len(newItems), reused)
	}

	// Past sessionTTL (1h) but within projectTTL (24h): only sessItem expires and re-inserts.
	now = now.Add(45 * time.Minute)
	newItems, reused, _ = store.FilterNew(context.Background(), []Item{projItem, sessItem, otherItem})
	if len(newItems) != 1 || newItems[0].ItemHash != sessItem.ItemHash || reused != 2 {
		t.Fatalf("after sessionTTL: new=%+v reused=%d", newItems, reused)
	}

	// Past projectTTL (24h): projItem & otherItem expire and refresh into current partition.
	now = now.Add(24 * time.Hour)
	newItems, reused, _ = store.FilterNew(context.Background(), []Item{projItem, otherItem})
	if len(newItems) != 2 || reused != 0 {
		t.Fatalf("after projectTTL: new=%d reused=%d", len(newItems), reused)
	}

	// Project invalidation evicts ONLY p1 entries, keeping p2 cached.
	evicted := store.InvalidateProject("p1")
	if evicted != 2 {
		t.Fatalf("InvalidateProject(p1) evicted=%d, want 2", evicted)
	}
	newItems, reused, _ = store.FilterNew(context.Background(), []Item{projItem, otherItem})
	if len(newItems) != 1 || newItems[0].ProjectID != "p1" || reused != 1 {
		t.Fatalf("after InvalidateProject: new=%+v reused=%d", newItems, reused)
	}

	// v2 classes (generation R = reuse age / 2 = 30m for sessions): a copy two
	// generations old is a Refresh (written again, credited); four or more
	// generations old it is New (billed).
	ctx := context.Background()
	turn := Item{ProjectID: "p3", ScopeID: "sess-9", ItemHash: "dddddddddddddddddddddddddddddddd", Content: "turn"}
	store.Commit(ctx, []Item{turn}, SeenStamp{Committer: SeenCommitterToken("trace-1")})
	if res := store.Check(ctx, []Item{turn}, SeenCommitterToken("trace-2")); res.Decisions[0].Class != SeenReuseOther {
		t.Fatalf("same generation: class=%s, want reuse_other", res.Decisions[0].Class)
	}
	now = now.Add(time.Hour)
	res := store.Check(ctx, []Item{turn}, SeenCommitterToken("trace-2"))
	if res.Decisions[0].Class != SeenRefresh || !res.Decisions[0].Write || res.CreditedItemBytes() != int64(len(turn.Content)) || res.BilledItemBytes() != 0 {
		t.Fatalf("two generations: class=%s write=%v credited=%d billed=%d, want refresh/true/%d/0",
			res.Decisions[0].Class, res.Decisions[0].Write, res.CreditedItemBytes(), res.BilledItemBytes(), len(turn.Content))
	}
	now = now.Add(time.Hour)
	res = store.Check(ctx, []Item{turn}, SeenCommitterToken("trace-2"))
	if res.Decisions[0].Class != SeenNew || res.BilledItemBytes() != int64(len(turn.Content)) {
		t.Fatalf("four generations: class=%s billed=%d, want new/%d", res.Decisions[0].Class, res.BilledItemBytes(), len(turn.Content))
	}

	// Invalidation is per project and repeatable: p3 keeps its entries when p1
	// is invalidated again, and a recommitted p1 item is counted afresh.
	store.Commit(ctx, []Item{turn, projItem}, SeenStamp{Committer: SeenCommitterToken("trace-1")})
	if n := store.InvalidateProject("p1"); n != 1 {
		t.Fatalf("second InvalidateProject(p1)=%d, want 1 (the recommitted item)", n)
	}
	if res := store.Check(ctx, []Item{turn, projItem}, SeenCommitterToken("trace-2")); res.Decisions[0].Class != SeenReuseOther || res.Decisions[1].Class != SeenNew {
		t.Fatalf("after second invalidation: classes=%s,%s, want reuse_other,new", res.Decisions[0].Class, res.Decisions[1].Class)
	}
}

func TestSeenStoreCheckNewIsReadOnlyUntilMarkCommitted(t *testing.T) {
	mr := miniredis.RunT(t)
	rdb := redis.NewClient(&redis.Options{Addr: mr.Addr()})
	defer rdb.Close()

	podA := NewSeenStore(rdb, 1000, time.Hour, 24*time.Hour)
	podB := NewSeenStore(rdb, 1000, time.Hour, 24*time.Hour)
	ctx := context.Background()
	tokA, tokB := SeenCommitterToken("trace-A"), SeenCommitterToken("trace-B")

	items := []Item{
		{ProjectID: "p1", ScopeID: "sess-1", ItemHash: "11111111111111111111111111111111", Content: "m1"},
		{ProjectID: "p1", ScopeID: ProjectScopeID, ItemHash: "22222222222222222222222222222222", Content: "sys"},
	}
	seenKeys := func() []string {
		var out []string
		for _, k := range mr.Keys() {
			if strings.HasPrefix(k, "traj:seen:") {
				out = append(out, k)
			}
		}
		return out
	}

	// Check on Pod A must NOT mark items seen locally or in Redis.
	resA := podA.Check(ctx, items, tokA)
	if resA.Count(SeenNew) != 2 || len(resA.Write()) != 2 {
		t.Fatalf("podA Check: new=%d write=%d, want 2/2", resA.Count(SeenNew), len(resA.Write()))
	}
	if keys := seenKeys(); len(keys) != 0 {
		t.Fatalf("Check wrote seen keys: %v", keys)
	}
	// Concurrent checks on BOTH Pod A and Pod B (both APIs) still see both items as new.
	resA2 := podA.Check(ctx, items, tokA)
	resB := podB.Check(ctx, items, tokB)
	newB, reusedB, _ := podB.CheckNew(ctx, items)
	if resA2.Count(SeenNew) != 2 || resB.Count(SeenNew) != 2 || len(newB) != 2 || reusedB != 0 {
		t.Fatalf("before commit: podA new=%d podB new=%d podB CheckNew=(%d,%d), want 2 each and (2,0)",
			resA2.Count(SeenNew), resB.Count(SeenNew), len(newB), reusedB)
	}

	// Only AFTER Pod A commits do Pod A and Pod B see the items as reused:
	// credited to another billing key, billed again to Pod A's own.
	podA.CommitChecked(ctx, resA, SeenStamp{Committer: tokA})
	for name, pod := range map[string]*SeenStore{"podA": podA, "podB": podB} {
		if other := pod.Check(ctx, items, tokB); other.Count(SeenReuseOther) != 2 || len(other.Write()) != 0 {
			t.Fatalf("after commit, %s as B: reuse_other=%d write=%d, want 2/0", name, other.Count(SeenReuseOther), len(other.Write()))
		}
		if self := pod.Check(ctx, items, tokA); self.Count(SeenReuseSelf) != 2 || self.BilledItemBytes() != resA.BilledItemBytes() {
			t.Fatalf("after commit, %s as A: reuse_self=%d billed=%d, want 2/%d", name, self.Count(SeenReuseSelf), self.BilledItemBytes(), resA.BilledItemBytes())
		}
		if newItems, reused, _ := pod.CheckNew(ctx, items); len(newItems) != 0 || reused != 2 {
			t.Fatalf("after commit, %s CheckNew=(%d,%d), want (0,2)", name, len(newItems), reused)
		}
	}
}

// TestBatchRestoreInlineRowsOnlyTheGivenRows: RestoreInlineRows reverts the
// compacted rows it is given (matched by identity, not by content) and leaves
// the batch's other rows exactly as they were stored.
func TestBatchRestoreInlineRowsOnlyTheGivenRows(t *testing.T) {
	pointerRow := func() map[string]any {
		return map[string]any{"input": "preview", "payload_encoding": EncodingItemsV1, "input_item_hashes": []string{"abc"}}
	}
	stored, refused, plain := pointerRow(), pointerRow(), map[string]any{"input": "plain"}
	b := NewBatch()
	b.AddFallback(SpanFallback{Row: stored, OrigInput: "full stored"})
	b.AddFallback(SpanFallback{Row: refused, OrigInput: "full refused"})
	b.RestoreInlineRows([]map[string]any{refused, plain})
	if refused["input"] != "full refused" {
		t.Fatalf("refused row not restored: %v", refused)
	}
	if _, ok := refused["payload_encoding"]; ok {
		t.Fatal("refused row keeps its pointer columns")
	}
	if stored["input"] != "preview" || stored["payload_encoding"] != EncodingItemsV1 {
		t.Fatalf("a row not given was changed: %v", stored)
	}
	if len(plain) != 1 || plain["input"] != "plain" {
		t.Fatalf("a row without a fallback was changed: %v", plain)
	}
	b.RestoreInlineRows(nil) // no-op
}
