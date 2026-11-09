package inclusive

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestInclusiveHTTPAdapters(t *testing.T) {
	for _, tc := range []string{"exact", "missing", "wrong_identity", "refund_conflict", "bad_ack", "redirect"} {
		t.Run(tc, func(t *testing.T) {
			var calls int
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				calls++
				if r.Header.Get("Authorization") != "Bearer synthetic" {
					t.Error("missing authorization")
				}
				if tc == "redirect" {
					http.Redirect(w, r, "/should-not-follow", http.StatusTemporaryRedirect)
					return
				}
				if r.Method == http.MethodGet {
					id := r.URL.Query().Get("id")
					if tc == "wrong_identity" {
						id = "different"
					}
					cost := "0.0000095"
					if tc == "missing" {
						cost = "null"
					}
					fmt.Fprintf(w, `{"data":{"id":%q,"model":"google/gemini-3.5-flash","total_cost":%s}}`, id, cost)
					return
				}
				var body map[string]string
				_ = json.NewDecoder(r.Body).Decode(&body)
				if body["org_id"] != "org" || body["request_id"] != "request" || body["amount_usd"] != "0.000089" {
					t.Errorf("wrong refund %+v", body)
				}
				if tc == "refund_conflict" {
					w.WriteHeader(409)
					return
				}
				if tc == "bad_ack" {
					fmt.Fprint(w, `{"status":true}`)
					return
				}
				fmt.Fprint(w, `{"status":true,"result":{"entry":{"request_id":"request","kind":"refund","amount_usd":0.000089}}}`)
			}))
			defer srv.Close()
			a := HTTPAdapters{GenerationURL: srv.URL, RefundURL: srv.URL, GenerationToken: func() string { return "synthetic" }, RefundToken: func() string { return "synthetic" }}
			if tc == "refund_conflict" || tc == "bad_ack" {
				if e := a.Refund(context.Background(), "org", "request", 89, "correction"); e == nil {
					t.Fatal("bad refund ack accepted")
				}
				return
			}
			g, e := a.Generation(context.Background(), "generation")
			if tc != "exact" {
				if e == nil {
					t.Fatal("bad generation accepted")
				}
				if calls != 1 {
					t.Fatal("redirect followed")
				}
				return
			}
			if e != nil || g.CostUSD != "0.0000095" {
				t.Fatalf("exact decimal lost %+v %v", g, e)
			}
			if e = a.Refund(context.Background(), "org", "request", 89, "correction"); e != nil {
				t.Fatal(e)
			}
		})
	}
}
