package observedcatalog

import (
	"context"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"reflect"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/twmb/franz-go/pkg/kgo"
)

func TestClickHouseQuorumUsesCurrentTableTopology(t *testing.T) {
	for _, tc := range []struct {
		name, body, quorum string
	}{
		{"plain", `{"engine":"AggregatingMergeTree","replicas":0}`, "auto"},
		{"plain-null-join", `{"engine":"AggregatingMergeTree","replicas":null}`, "auto"},
		{"single", `{"engine":"ReplicatedAggregatingMergeTree","replicas":1}`, "1"},
		{"two", `{"engine":"ReplicatedAggregatingMergeTree","replicas":2}`, "auto"},
		{"multiple", `{"engine":"ReplicatedAggregatingMergeTree","replicas":3}`, "auto"},
		{"empty", "", ""},
		{"missing-engine", `{"replicas":1}`, ""},
		{"missing-count", `{"engine":"ReplicatedAggregatingMergeTree"}`, ""},
		{"null-count", `{"engine":"ReplicatedAggregatingMergeTree","replicas":null}`, ""},
		{"zero-count", `{"engine":"ReplicatedAggregatingMergeTree","replicas":0}`, ""},
		{"negative-count", `{"engine":"ReplicatedAggregatingMergeTree","replicas":-1}`, ""},
		{"fractional-count", `{"engine":"ReplicatedAggregatingMergeTree","replicas":1.5}`, ""},
		{"string-count", `{"engine":"ReplicatedAggregatingMergeTree","replicas":"1"}`, ""},
		{"wrong-engine", `{"engine":"Distributed","replicas":1}`, ""},
		{"duplicate-rows", "{\"engine\":\"AggregatingMergeTree\"}\n{\"engine\":\"AggregatingMergeTree\"}", ""},
		{"truncated", `{"engine":"ReplicatedAggregatingMergeTree","replicas":1`, ""},
		{"oversized", strings.Repeat(" ", 4097), ""},
	} {
		t.Run(tc.name, func(t *testing.T) {
			var tables, quorums []string
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				q := r.URL.Query()
				if user, password, ok := r.BasicAuth(); !ok || user != "writer" || password != "secret" {
					t.Error("metadata/write credentials changed")
				}
				if strings.HasPrefix(q.Get("query"), "SELECT ") {
					if q.Get("readonly") != "1" || q.Get("param_catalog") != "catalog" || q.Get("max_result_rows") != "2" || q.Get("max_result_bytes") != "4096" || q.Get("wait_end_of_query") != "1" {
						t.Error("unbounded or unscoped topology query")
					}
					if strings.Contains(q.Get("query"), "system.tables") {
						tables = append(tables, q.Get("param_table"))
					} else if strings.HasPrefix(tc.name, "plain") {
						t.Error("plain tables must not read system.replicas")
					}
					fmt.Fprint(w, tc.body)
					return
				}
				quorums = append(quorums, q.Get("insert_quorum"))
			}))
			defer server.Close()
			sink, err := NewClickHouseSink(ClickHouseConfig{URL: server.URL, Database: "catalog", Username: "writer", Password: "secret"})
			if err != nil {
				t.Fatal(err)
			}
			err = sink.Insert(context.Background(), testBatch(t))
			if tc.quorum == "" {
				if err == nil || len(quorums) != 0 {
					t.Fatal("unknown topology allowed writes", err, quorums)
				}
			} else if err != nil || !reflect.DeepEqual(quorums, []string{tc.quorum, tc.quorum}) || !reflect.DeepEqual(tables, []string{KeyTable, ValueTable}) {
				t.Fatal("incorrect table-specific quorum", err, tables, quorums)
			}
		})
	}
}

func TestClickHouseTopologyAndInsertShareDeadline(t *testing.T) {
	for _, callerTimeout := range []time.Duration{time.Second, 150 * time.Millisecond} {
		t.Run(callerTimeout.String(), func(t *testing.T) {
			remaining := make(chan int, 1)
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if strings.HasPrefix(r.URL.Query().Get("query"), "SELECT ") {
					time.Sleep(40 * time.Millisecond)
					fmt.Fprint(w, `{"engine":"AggregatingMergeTree"}`)
					return
				}
				budget, _ := strconv.Atoi(r.URL.Query().Get("insert_quorum_timeout"))
				remaining <- budget
				io.Copy(io.Discard, r.Body)
				<-r.Context().Done()
			}))
			defer server.Close()
			sink, _ := NewClickHouseSink(ClickHouseConfig{URL: server.URL, Database: "catalog", Timeout: 250 * time.Millisecond})
			ctx, cancel := context.WithTimeout(context.Background(), callerTimeout)
			defer cancel()
			start := time.Now()
			err := sink.Insert(ctx, testBatch(t))
			if err == nil || time.Since(start) > min(callerTimeout, 250*time.Millisecond)+100*time.Millisecond {
				t.Fatal("table/caller deadline exceeded", err)
			}
			select {
			case budget := <-remaining:
				if budget <= 0 || budget >= int(min(callerTimeout, 250*time.Millisecond).Milliseconds()/2) {
					t.Fatal("quorum deadline ignored time spent reading metadata", budget)
				}
			case <-time.After(time.Second):
				t.Fatal("INSERT not reached")
			}
		})
	}
}

func TestClickHouseTopologyFailureDoesNotWriteOrLeak(t *testing.T) {
	for _, mode := range []string{"status", "late-error", "redirect", "cancelled", "deadline"} {
		t.Run(mode, func(t *testing.T) {
			writes := 0
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if !strings.HasPrefix(r.URL.Query().Get("query"), "SELECT ") {
					writes++
					return
				}
				switch mode {
				case "status":
					w.WriteHeader(http.StatusForbidden)
				case "late-error":
					w.Header().Set("X-ClickHouse-Exception-Code", "497")
				case "redirect":
					w.Header().Set("Location", "/credentials-must-not-follow")
					w.WriteHeader(http.StatusTemporaryRedirect)
				case "deadline":
					<-r.Context().Done()
				}
				fmt.Fprint(w, "private-server-details")
			}))
			defer server.Close()
			sink, _ := NewClickHouseSink(ClickHouseConfig{URL: server.URL, Database: "catalog", Timeout: 50 * time.Millisecond})
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			if mode == "cancelled" {
				cancel()
			}
			err := sink.Insert(ctx, testBatch(t))
			if err == nil || strings.Contains(err.Error(), "private-server-details") || writes != 0 {
				t.Fatal("unsafe topology failure", err, writes)
			}
		})
	}
}

func TestClickHouseDoesNotCacheSingleReplicaQuorum(t *testing.T) {
	var quorums []string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if strings.HasPrefix(r.URL.Query().Get("query"), "SELECT ") {
			replicas := 3
			if len(quorums) == 0 {
				replicas = 1
			}
			fmt.Fprintf(w, `{"engine":"ReplicatedAggregatingMergeTree","replicas":%d}`, replicas)
			return
		}
		quorums = append(quorums, r.URL.Query().Get("insert_quorum"))
	}))
	defer server.Close()
	sink, _ := NewClickHouseSink(ClickHouseConfig{URL: server.URL, Database: "catalog"})
	for range 2 {
		if err := sink.Insert(context.Background(), testBatch(t)); err != nil {
			t.Fatal(err)
		}
	}
	if !reflect.DeepEqual(quorums, []string{"1", "auto", "auto", "auto"}) {
		t.Fatal("stale single-replica quorum", quorums)
	}
}

func TestConsumerTopologyFailureAfterKeysReplaysWithoutCommitting(t *testing.T) {
	failValues := true
	keys, values := 0, 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		q := r.URL.Query()
		if strings.HasPrefix(q.Get("query"), "SELECT ") {
			if failValues && q.Get("param_table") == ValueTable {
				w.WriteHeader(http.StatusForbidden)
				return
			}
			fmt.Fprint(w, `{"engine":"ReplicatedAggregatingMergeTree","replicas":1}`)
			return
		}
		if strings.Contains(q.Get("query"), KeyTable) {
			keys++
		} else {
			values++
		}
	}))
	defer server.Close()
	sink, _ := NewClickHouseSink(ClickHouseConfig{URL: server.URL, Database: "catalog"})
	raw, err := Encode(testBatch(t))
	if err != nil {
		t.Fatal(err)
	}
	source := &sourceStub{records: []*kgo.Record{{Topic: DefaultTopic, Partition: 0, Offset: 4, Value: raw}}}
	consumer := Consumer{source: source, cfg: KafkaConfig{Topic: DefaultTopic, Timeout: time.Second}, sink: sink}
	if err := consumer.processOnce(context.Background()); err == nil || source.commits != 0 || keys != 1 || values != 0 {
		t.Fatal("metadata failure skipped or committed the record", err, source.commits, keys, values)
	}
	failValues = false
	if err := consumer.processOnce(context.Background()); err != nil || source.commits != 1 || keys != 2 || values != 1 {
		t.Fatal("partial write did not replay completely", err, source.commits, keys, values)
	}
}
