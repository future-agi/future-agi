package mcp

import (
	"encoding/json"
	"reflect"
	"sync"
	"testing"
	"time"
)

func TestRegistryToolSnapshots(t *testing.T) {
	for _, tc := range []struct {
		name string
		list func(*Registry) []*RegisteredTool
	}{
		{"all", (*Registry).ListTools},
		{"server", func(r *Registry) []*RegisteredTool { return r.ServerTools("s") }},
	} {
		t.Run(tc.name, func(t *testing.T) {
			r := NewRegistry("_")
			r.RegisterServer("s", []Tool{{
				Name: "tool", Description: "original",
				InputSchema:  json.RawMessage(`{"type":"object"}`),
				OutputSchema: json.RawMessage(`{"type":"string"}`),
				Annotations:  &ToolAnnotations{},
			}})
			live, _ := r.ResolveTool("s_tool")
			live.Stats.RecordCall(100*time.Millisecond, true)
			lastCalled := live.Stats.LastCalled.Load()

			tools := tc.list(r)
			if len(tools) != 1 {
				t.Fatalf("got %d tools, want 1", len(tools))
			}
			snapshot := tools[0]
			if snapshot == live || snapshot.Server != live.Server || snapshot.OrigName != live.OrigName || snapshot.Name != live.Name || !reflect.DeepEqual(snapshot.Tool, live.Tool) || !reflect.DeepEqual(snapshot.Upstream, live.Upstream) || snapshot.Title != live.Title {
				t.Fatal("snapshot lost tool metadata")
			}
			if snapshot.Stats.CallCount.Load() != 1 || snapshot.Stats.ErrorCount.Load() != 1 || snapshot.Stats.TotalNanos.Load() != int64(100*time.Millisecond) || snapshot.Stats.LastCalled.Load() != lastCalled {
				t.Fatal("snapshot lost usage counters")
			}

			live.Stats.RecordCall(200*time.Millisecond, false)
			if snapshot.Stats.CallCount.Load() != 1 || snapshot.Stats.TotalNanos.Load() != int64(100*time.Millisecond) || snapshot.Stats.LastCalled.Load() != lastCalled {
				t.Fatal("snapshot changed with live counters")
			}
			snapshot.Name = "local"
			snapshot.Tool.Description = "local"
			snapshot.Stats.RecordCall(time.Second, true)
			if live.Name != "s_tool" || live.Tool.Description != "original" || live.Stats.CallCount.Load() != 2 || live.Stats.ErrorCount.Load() != 1 || live.Stats.TotalNanos.Load() != int64(300*time.Millisecond) {
				t.Fatal("snapshot mutation changed the registry")
			}

			r.RegisterServer("s", []Tool{{Name: "replacement"}})
			if snapshot.OrigName != "tool" || snapshot.Stats.CallCount.Load() != 2 {
				t.Fatal("re-registration changed an existing snapshot")
			}
			fresh := tc.list(r)
			if len(fresh) != 1 || fresh[0].Name != "s_replacement" || fresh[0].Stats.CallCount.Load() != 0 {
				t.Fatal("fresh snapshot did not reflect replacement")
			}
		})
	}
}

func TestRegistryConcurrentRegisterListAndRecord(t *testing.T) {
	const iterations = 200
	r := NewRegistry("_")
	r.RegisterServer("stable", []Tool{{Name: "tool", Description: "original"}})
	live, _ := r.ResolveTool("stable_tool")

	var wg sync.WaitGroup
	start := make(chan struct{})
	run := func(fn func()) {
		wg.Add(1)
		go func() {
			defer wg.Done()
			<-start
			fn()
		}()
	}
	run(func() {
		for i := 0; i < iterations; i++ {
			r.RegisterServer("changing", []Tool{{Name: "one"}, {Name: "two"}})
			r.UnregisterServer("changing")
		}
	})
	for worker := 0; worker < 2; worker++ {
		run(func() {
			for i := 0; i < iterations; i++ {
				live.Stats.RecordCall(time.Millisecond, true)
			}
		})
		run(func() {
			for i := 0; i < iterations; i++ {
				for _, tools := range [][]*RegisteredTool{r.ListTools(), r.ServerTools("stable"), r.ServerTools("changing")} {
					for j := range tools {
						tool := tools[j]
						if tool.Name != tool.Server+"_"+tool.OrigName || tool.Tool.Name != tool.Name {
							t.Error("inconsistent registry metadata")
						}
						// These were value snapshots before the vet fix, not live
						// registry pointers. Mutating them must remain isolated.
						tool.Name = "local"
						tool.Tool.Description = "local"
						tool.Stats.RecordCall(time.Second, true)
					}
				}
				_ = r.ListToolsJSON()
				_ = r.ListMCPTools()
			}
		})
	}
	close(start)
	wg.Wait()

	if live.Name != "stable_tool" || live.Tool.Description != "original" {
		t.Fatal("listing exposed mutable registry metadata")
	}
	if live.Stats.CallCount.Load() != 2*iterations || live.Stats.ErrorCount.Load() != 2*iterations || live.Stats.TotalNanos.Load() != 2*iterations*int64(time.Millisecond) {
		t.Fatal("listing changed the live counters")
	}
	if r.ToolCount() != 1 || r.ServerTools("changing") != nil {
		t.Fatal("unregistered tools remain in the registry")
	}
}
