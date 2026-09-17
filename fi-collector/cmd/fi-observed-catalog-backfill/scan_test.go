package main

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func testRow(version string, value string) map[string]any {
	return map[string]any{"observation_type": "span", "service_name": "service", "trace_id": "trace", "id": "span", "_version": version, "attrs_string": map[string]any{"key": value}, "is_deleted": json.Number("0")}
}

func TestLatestSourceVersionsAndTombstones(t *testing.T) {
	key, _ := rowKey(testRow("1", "old"))
	newer := testRow("2", "new")
	for _, rows := range [][]map[string]any{{testRow("1", "old"), newer}, {newer, testRow("1", "old"), newer}} {
		got, err := latestRows(rows, []physicalKey{key})
		if err != nil || len(got) != 1 || got[0]["_version"] != "2" {
			t.Fatalf("latest: %v %v", got, err)
		}
	}
	deleted := testRow("3", "new")
	deleted["is_deleted"] = json.Number("1")
	got, err := latestRows([]map[string]any{newer, deleted}, []physicalKey{key})
	if err != nil || got[0]["is_deleted"] != json.Number("1") {
		t.Fatal("tombstone must reach caller before filtering", err)
	}
}

func TestSourcePageRejectsAmbiguousOrMissingData(t *testing.T) {
	key, _ := rowKey(testRow("1", "old"))
	for name, rows := range map[string][]map[string]any{
		"conflicting_version": {testRow("2", "one"), testRow("2", "two")},
		"missing_identity":    {},
		"invalid_version":     {testRow("bad", "one")},
	} {
		t.Run(name, func(t *testing.T) {
			if _, err := latestRows(rows, []physicalKey{key}); err == nil {
				t.Fatal("accepted invalid page")
			}
		})
	}
	other := testRow("1", "one")
	other["service_name"] = "another service"
	if _, err := latestRows([]map[string]any{other}, []physicalKey{key}); err == nil {
		t.Fatal("accepted wrong physical identity")
	}
}

func TestLatestSourceIgnoresObsoleteConflictsInAnyOrder(t *testing.T) {
	key, _ := rowKey(testRow("1", "old"))
	for _, tombstone := range []json.Number{"0", "1"} {
		latest := testRow("2", "new")
		latest["is_deleted"] = tombstone
		for _, rows := range [][]map[string]any{
			{testRow("1", "one"), testRow("1", "two"), latest},
			{latest, testRow("1", "one"), testRow("1", "two")},
			{testRow("1", "one"), latest, testRow("1", "two")},
		} {
			selected, err := latestRows(rows, []physicalKey{key})
			if err != nil || len(selected) != 1 || selected[0]["_version"] != "2" || selected[0]["is_deleted"] != tombstone {
				t.Fatalf("obsolete conflict affected latest state: %v %v", selected, err)
			}
		}
	}
}

func TestSourceClientIsSelectOnlyAndDoesNotLeakServerData(t *testing.T) {
	calls := 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls++
		if r.URL.Query().Get("readonly") != "1" || r.URL.Query().Get("result_overflow_mode") != "throw" {
			t.Error("unbounded/non-readonly query")
		}
		w.WriteHeader(http.StatusForbidden)
		fmt.Fprint(w, "private source value")
	}))
	defer server.Close()
	reader, err := newSourceReader(server.URL, "source", "readonly", "test")
	if err != nil {
		t.Fatal(err)
	}
	for _, sql := range []string{"INSERT INTO spans VALUES (1)", "SELECT 1; DROP TABLE spans"} {
		if _, err := reader.selectRows(context.Background(), sql, nil); err == nil {
			t.Fatal("accepted a write")
		}
	}
	if calls != 0 {
		t.Fatal("write attempted")
	}
	_, err = reader.selectRows(context.Background(), "SELECT 1", nil)
	if err == nil || strings.Contains(err.Error(), "private") {
		t.Fatal("raw server error exposed", err)
	}
	if _, err := reader.selectRows(context.Background(), "SELECT 1", url.Values{"readonly": {"0"}}); err == nil {
		t.Fatal("settings override accepted")
	}
}

func TestSourceClientDoesNotAcceptPartialJSON(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { fmt.Fprint(w, "{\"id\":\"one\"}\n{\"id\":") }))
	defer server.Close()
	reader, _ := newSourceReader(server.URL, "source", "readonly", "test")
	if _, err := reader.selectRows(context.Background(), "SELECT 1", nil); err == nil {
		t.Fatal("partial result accepted")
	}
}

func TestSourceClientRejectsLateExceptionHeaderOnHTTP200(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("X-ClickHouse-Exception-Code", "159")
		w.WriteHeader(http.StatusOK)
	}))
	defer server.Close()
	reader, _ := newSourceReader(server.URL, "source", "readonly", "test")
	if _, err := reader.selectRows(context.Background(), "SELECT 1", nil); err == nil {
		t.Fatal("late exception was mistaken for an empty page")
	}
}

func TestCheckpointResumeOwnershipAndBinding(t *testing.T) {
	path := filepath.Join(t.TempDir(), "progress.json")
	lock, err := lockCheckpoint(path)
	if err != nil {
		t.Fatal(err)
	}
	defer lock.Close()
	if other, err := lockCheckpoint(path); err == nil {
		other.Close()
		t.Fatal("concurrent ownership accepted")
	}
	start := time.Date(2026, 1, 1, 12, 20, 0, 0, time.UTC)
	value, err := loadCheckpoint(path, "source-and-destination", start)
	if err != nil || value.Hour.Minute() != 0 {
		t.Fatal("invalid initial checkpoint", err)
	}
	value.Pages = 4
	if err := saveCheckpoint(path, value); err != nil {
		t.Fatal(err)
	}
	resumed, err := loadCheckpoint(path, value.Binding, start)
	if err != nil || resumed.Pages != 4 {
		t.Fatal("resume failed", err)
	}
	if _, err := loadCheckpoint(path, "different-scope", start); err == nil {
		t.Fatal("wrong binding accepted")
	}
	info, _ := os.Stat(path)
	if info.Mode().Perm() != 0600 {
		t.Fatal("checkpoint permissions too broad")
	}
	alias := filepath.Join(t.TempDir(), "alias")
	if err := os.Symlink(path, alias); err != nil {
		t.Fatal(err)
	}
	if _, err := loadCheckpoint(alias, value.Binding, start); err == nil {
		t.Fatal("checkpoint symlink accepted")
	}
}

func TestPhysicalKeyDoesNotJoinWithAmbiguousDelimiter(t *testing.T) {
	one := physicalKey{"a\x00b", "c", "trace", "span"}
	two := physicalKey{"a", "b\x00c", "trace", "span"}
	if one == two || one.after(two) == two.after(one) {
		t.Fatal("physical identities collided")
	}
}
