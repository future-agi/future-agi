package main

import (
	"bytes"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"math"
	"net/http"
	"os"
	"sort"
	"strings"
	"sync"
	"sync/atomic"
	"time"
)

type request struct {
	Model    string    `json:"model"`
	Messages []message `json:"messages"`
	Stream   bool      `json:"stream,omitempty"`
}

type message struct {
	Role    string `json:"role"`
	Content string `json:"content"`
}

type result struct {
	Model      string
	StatusCode int
	Latency    time.Duration
	Error      string
	Provider   string
	Cost       string
	TTFB       time.Duration // only for streaming
}

var prompts = []string{
	"What is 2+2?",
	"Say hello in French.",
	"Name three colors.",
	"What is the capital of Japan?",
	"Count to five.",
	"What day comes after Monday?",
	"Name a planet.",
	"What is water made of?",
}

func main() {
	baseURL := flag.String("url", "http://localhost:8080", "Gateway base URL")
	concurrency := flag.Int("c", 10, "Concurrent workers")
	totalRequests := flag.Int("n", 50, "Total requests")
	models := flag.String("models", "gpt-4o-mini,llama-3.1-8b-instant", "Comma-separated model list")
	stream := flag.Bool("stream", false, "Use streaming requests")
	warmup := flag.Int("warmup", 2, "Warmup requests (not counted)")
	flag.Parse()

	modelList := strings.Split(*models, ",")
	for i := range modelList {
		modelList[i] = strings.TrimSpace(modelList[i])
	}

	client := &http.Client{
		Timeout: 120 * time.Second,
		Transport: &http.Transport{
			MaxIdleConns:        *concurrency * 2,
			MaxIdleConnsPerHost: *concurrency * 2,
			IdleConnTimeout:     90 * time.Second,
		},
	}

	fmt.Println("=== Agentcc Gateway Load Test ===")
	fmt.Printf("URL:         %s\n", *baseURL)
	fmt.Printf("Concurrency: %d\n", *concurrency)
	fmt.Printf("Requests:    %d\n", *totalRequests)
	fmt.Printf("Models:      %s\n", strings.Join(modelList, ", "))
	fmt.Printf("Streaming:   %v\n", *stream)
	fmt.Println()

	// Check gateway health.
	healthResp, err := client.Get(*baseURL + "/healthz")
	if err != nil {
		fmt.Fprintf(os.Stderr, "Gateway unreachable at %s: %v\n", *baseURL, err)
		os.Exit(1)
	}
	healthResp.Body.Close()
	if healthResp.StatusCode != 200 {
		fmt.Fprintf(os.Stderr, "Gateway not healthy: %d\n", healthResp.StatusCode)
		os.Exit(1)
	}
	fmt.Println("Gateway health: OK")

	// Check /v1/models.
	modelsResp, err := client.Get(*baseURL + "/v1/models")
	if err == nil {
		defer modelsResp.Body.Close()
		var modelsData struct {
			Data []struct {
				ID   string `json:"id"`
				Mode string `json:"mode,omitempty"`
			} `json:"data"`
		}
		if json.NewDecoder(modelsResp.Body).Decode(&modelsData) == nil {
			fmt.Printf("Available models: %d\n", len(modelsData.Data))
			for _, m := range modelsData.Data {
				for _, target := range modelList {
					if m.ID == target {
						fmt.Printf("  -> %s (mode: %s)\n", m.ID, m.Mode)
					}
				}
			}
		}
	}
	fmt.Println()

	// Warmup.
	if *warmup > 0 {
		fmt.Printf("Warming up (%d requests)...\n", *warmup)
		for i := 0; i < *warmup; i++ {
			model := modelList[i%len(modelList)]
			r := doRequest(client, *baseURL, model, prompts[i%len(prompts)], *stream)
			if r.Error != "" {
				fmt.Printf("  Warmup %d (%s): ERROR: %s\n", i+1, model, r.Error)
			} else {
				fmt.Printf("  Warmup %d (%s): %dms, provider=%s\n", i+1, model, r.Latency.Milliseconds(), r.Provider)
			}
		}
		fmt.Println()
	}

	// Run load test.
	fmt.Printf("Starting load test: %d requests, %d concurrency...\n\n", *totalRequests, *concurrency)

	var (
		results   = make([]result, *totalRequests)
		completed atomic.Int64
		wg        sync.WaitGroup
		sem       = make(chan struct{}, *concurrency)
	)

	start := time.Now()

	for i := 0; i < *totalRequests; i++ {
		wg.Add(1)
		sem <- struct{}{}
		go func(idx int) {
			defer wg.Done()
			defer func() { <-sem }()

			model := modelList[idx%len(modelList)]
			prompt := prompts[idx%len(prompts)]
			results[idx] = doRequest(client, *baseURL, model, prompt, *stream)

			done := completed.Add(1)
			if done%10 == 0 || done == int64(*totalRequests) {
				fmt.Printf("  Progress: %d/%d\n", done, *totalRequests)
			}
		}(i)
	}

	wg.Wait()
	elapsed := time.Since(start)

	fmt.Println()
	printReport(results, elapsed, modelList)
}

func doRequest(client *http.Client, baseURL, model, prompt string, stream bool) result {
	req := request{
		Model: model,
		Messages: []message{
			{Role: "user", Content: prompt},
		},
		Stream: stream,
	}

	body, _ := json.Marshal(req)
	httpReq, _ := http.NewRequest("POST", baseURL+"/v1/chat/completions", bytes.NewReader(body))
	httpReq.Header.Set("Content-Type", "application/json")

	start := time.Now()
	resp, err := client.Do(httpReq)
	if err != nil {
		return result{Model: model, Error: err.Error(), Latency: time.Since(start)}
	}
	defer resp.Body.Close()

	r := result{
		Model:      model,
		StatusCode: resp.StatusCode,
		Provider:   resp.Header.Get("x-agentcc-provider"),
		Cost:       resp.Header.Get("x-agentcc-cost"),
	}

	if stream && resp.StatusCode == 200 {
		// For streaming, measure TTFB (first byte) and read all chunks.
		r.TTFB = time.Since(start)
		io.Copy(io.Discard, resp.Body)
		r.Latency = time.Since(start)
	} else {
		io.Copy(io.Discard, resp.Body)
		r.Latency = time.Since(start)
	}

	if resp.StatusCode >= 400 {
		r.Error = fmt.Sprintf("HTTP %d", resp.StatusCode)
	}

	return r
}

func printReport(results []result, elapsed time.Duration, models []string) {
	fmt.Println("=== Load Test Results ===")
	fmt.Printf("Total time: %s\n", elapsed.Round(time.Millisecond))
	fmt.Printf("Throughput: %.1f req/s\n\n", float64(len(results))/elapsed.Seconds())

	// Overall stats.
	var (
		successes int
		errors    int
		latencies []float64
	)

	perModel := make(map[string]*modelStats)

	for _, r := range results {
		ms := perModel[r.Model]
		if ms == nil {
			ms = &modelStats{Model: r.Model}
			perModel[r.Model] = ms
		}

		if r.Error != "" {
			errors++
			ms.Errors++
		} else {
			successes++
			ms.Successes++
			lat := float64(r.Latency.Milliseconds())
			latencies = append(latencies, lat)
			ms.Latencies = append(ms.Latencies, lat)
			ms.Provider = r.Provider
		}
	}

	fmt.Printf("Successes:   %d\n", successes)
	fmt.Printf("Errors:      %d\n", errors)
	if len(results) > 0 {
		fmt.Printf("Error rate:  %.1f%%\n", float64(errors)/float64(len(results))*100)
	}
	fmt.Println()

	if len(latencies) > 0 {
		sort.Float64s(latencies)
		fmt.Println("--- Overall Latency (ms) ---")
		fmt.Printf("  Min:    %.0f\n", latencies[0])
		fmt.Printf("  P50:    %.0f\n", percentile(latencies, 50))
		fmt.Printf("  P95:    %.0f\n", percentile(latencies, 95))
		fmt.Printf("  P99:    %.0f\n", percentile(latencies, 99))
		fmt.Printf("  Max:    %.0f\n", latencies[len(latencies)-1])
		fmt.Printf("  Avg:    %.0f\n", avg(latencies))
		fmt.Println()
	}

	// Per-model breakdown.
	fmt.Println("--- Per-Model Breakdown ---")
	modelNames := make([]string, 0, len(perModel))
	for m := range perModel {
		modelNames = append(modelNames, m)
	}
	sort.Strings(modelNames)

	fmt.Printf("%-35s %6s %6s %8s %8s %8s %8s %s\n",
		"Model", "OK", "Err", "P50", "P95", "Avg", "Max", "Provider")
	fmt.Println(strings.Repeat("-", 100))

	for _, name := range modelNames {
		ms := perModel[name]
		if len(ms.Latencies) > 0 {
			sort.Float64s(ms.Latencies)
			fmt.Printf("%-35s %6d %6d %7.0fms %7.0fms %7.0fms %7.0fms %s\n",
				name, ms.Successes, ms.Errors,
				percentile(ms.Latencies, 50),
				percentile(ms.Latencies, 95),
				avg(ms.Latencies),
				ms.Latencies[len(ms.Latencies)-1],
				ms.Provider)
		} else {
			fmt.Printf("%-35s %6d %6d %8s %8s %8s %8s %s\n",
				name, ms.Successes, ms.Errors, "-", "-", "-", "-", ms.Provider)
		}
	}

	// Print errors if any.
	if errors > 0 {
		fmt.Println()
		fmt.Println("--- Error Summary ---")
		errCounts := make(map[string]int)
		for _, r := range results {
			if r.Error != "" {
				key := fmt.Sprintf("%s: %s", r.Model, r.Error)
				errCounts[key]++
			}
		}
		for errMsg, count := range errCounts {
			fmt.Printf("  [%d] %s\n", count, errMsg)
		}
	}

	fmt.Println()
}

type modelStats struct {
	Model     string
	Successes int
	Errors    int
	Latencies []float64
	Provider  string
}

func percentile(sorted []float64, p float64) float64 {
	if len(sorted) == 0 {
		return 0
	}
	idx := (p / 100) * float64(len(sorted)-1)
	lower := int(math.Floor(idx))
	upper := int(math.Ceil(idx))
	if lower == upper {
		return sorted[lower]
	}
	frac := idx - float64(lower)
	return sorted[lower]*(1-frac) + sorted[upper]*frac
}

func avg(vals []float64) float64 {
	if len(vals) == 0 {
		return 0
	}
	var sum float64
	for _, v := range vals {
		sum += v
	}
	return sum / float64(len(vals))
}
