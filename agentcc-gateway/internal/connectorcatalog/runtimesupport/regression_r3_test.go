package runtimesupport

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"reflect"
	"strings"
	"testing"
	"time"
	"unsafe"
)

func TestSupportContentEncodingValues(t *testing.T) {
	for _, tc := range []struct {
		name   string
		values []string
		status int
	}{
		{"absent", nil, http.StatusOK},
		{"single_empty", []string{""}, http.StatusOK},
		{"gzip", []string{"gzip"}, http.StatusBadRequest},
		{"empty_then_gzip", []string{"", "gzip"}, http.StatusBadRequest},
		{"gzip_then_empty", []string{"gzip", ""}, http.StatusBadRequest},
		{"duplicate_empty", []string{"", ""}, http.StatusBadRequest},
		{"duplicate_gzip", []string{"gzip", "gzip"}, http.StatusBadRequest},
		{"comma_joined", []string{", gzip"}, http.StatusBadRequest},
		{"nonempty_whitespace", []string{" "}, http.StatusBadRequest},
	} {
		t.Run(tc.name, func(t *testing.T) {
			o, req, records := fixture(t)
			data, err := json.Marshal(req)
			if err != nil {
				t.Fatal(err)
			}
			r := httptest.NewRequest(http.MethodPost, Path, strings.NewReader(string(data)))
			r.Header.Set("Authorization", "Bearer "+syntheticToken)
			r.Header.Set("Content-Type", "application/json")
			for _, value := range tc.values {
				r.Header.Add("Content-Encoding", value)
			}
			w := httptest.NewRecorder()
			snapshot(o, records).ServeHTTP(w, r)
			if w.Code != tc.status {
				t.Fatalf("status %d: %s; want %d", w.Code, w.Body, tc.status)
			}
			if tc.status == http.StatusBadRequest {
				if w.Body.String() != "{\"error\":\"invalid_request\"}\n" {
					t.Fatalf("unexpected error body: %s", w.Body)
				}
			} else {
				var body map[string]any
				if err := json.Unmarshal(w.Body.Bytes(), &body); err != nil {
					t.Fatal(err)
				}
				if body["state"] != "available" || len(body) != 14 {
					t.Fatalf("valid control request refused: %s", w.Body)
				}
			}
		})
	}
}

func TestSupportWallClockRollbackDespiteMonotonicProgress(t *testing.T) {
	o, req, records := fixture(t)
	now := time.Now()
	end := now.Add(100 * time.Millisecond)
	rollback := now.Add(-100 * time.Millisecond)
	// Go has no public constructor for independently moving wall and monotonic
	// clocks. In this test only, copy the wall word from another genuine Time
	// into a local value, retaining end's advancing monotonic reading. Never
	// change the host clock. Fail, rather than skip, if the representation or
	// monotonic-clock preconditions change on the execution toolchain.
	endWall := reflect.ValueOf(&end).Elem().FieldByName("wall")
	rollbackWall := reflect.ValueOf(rollback).FieldByName("wall")
	if !endWall.IsValid() || endWall.Kind() != reflect.Uint64 || !endWall.CanAddr() ||
		!rollbackWall.IsValid() || rollbackWall.Kind() != reflect.Uint64 {
		t.Fatal("time.Time wall representation changed; update regression fixture")
	}
	*(*uint64)(unsafe.Pointer(endWall.UnsafeAddr())) = rollbackWall.Uint()
	if !end.After(now) || !end.Round(0).Before(now.Round(0)) {
		t.Fatal("fixture must advance monotonically while wall time moves backward")
	}
	calls := 0
	o.Now = func() time.Time {
		calls++
		if calls == 1 {
			return now
		}
		return end
	}
	w, body := post(t, snapshot(o, records), req, syntheticToken)
	if calls != 2 || w.Code != http.StatusOK || body["state"] != "unavailable" ||
		body["reason_code"] != "runtime_support_lifecycle_unavailable" || len(body) != 8 {
		t.Fatalf("wall rollback accepted: calls=%d status=%d body=%s", calls, w.Code, w.Body)
	}
}

func TestSupportLeaseEndChecks(t *testing.T) {
	for _, tc := range []struct {
		name    string
		elapsed time.Duration
		cancel  bool
		want    string
	}{
		{"within_lease", 100 * time.Millisecond, false, "available"},
		{"wall_rollback", -time.Nanosecond, false, "unavailable"},
		{"exact_expiry", time.Second, false, "unavailable"},
		{"past_expiry", time.Second + time.Millisecond, false, "unavailable"},
		{"canceled_before_reply", 100 * time.Millisecond, true, "unavailable"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			o, req, records := fixture(t)
			start := o.Now()
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			calls := 0
			o.Now = func() time.Time {
				calls++
				if calls == 1 {
					return start
				}
				if tc.cancel {
					cancel()
				}
				return start.Add(tc.elapsed)
			}
			body := snapshot(o, records).answer(ctx, req)
			if calls != 2 || body["state"] != tc.want {
				t.Fatalf("calls=%d body=%v; want %s", calls, body, tc.want)
			}
			if tc.want == "unavailable" && (body["reason_code"] != "runtime_support_lifecycle_unavailable" || len(body) != 8) {
				t.Fatalf("unexpected refusal: %v", body)
			}
		})
	}
}
