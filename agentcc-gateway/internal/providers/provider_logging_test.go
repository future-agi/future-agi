package providers_test

import (
	"bytes"
	"context"
	"encoding/binary"
	"encoding/json"
	"fmt"
	"hash/crc32"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/providers/azure"
	"github.com/futureagi/agentcc-gateway/internal/providers/bedrock"
	"github.com/futureagi/agentcc-gateway/internal/providers/gemini"
	"github.com/futureagi/agentcc-gateway/internal/providers/openai"
)

func bedrockExceptionFrame(payload []byte) []byte {
	var headers bytes.Buffer
	for _, header := range [][2]string{{":message-type", "exception"}, {":exception-type", "validationException"}} {
		headers.WriteByte(byte(len(header[0])))
		headers.WriteString(header[0])
		headers.WriteByte(7) // AWS event-stream string header.
		binary.Write(&headers, binary.BigEndian, uint16(len(header[1])))
		headers.WriteString(header[1])
	}
	var frame bytes.Buffer
	binary.Write(&frame, binary.BigEndian, uint32(16+headers.Len()+len(payload)))
	binary.Write(&frame, binary.BigEndian, uint32(headers.Len()))
	binary.Write(&frame, binary.BigEndian, crc32.ChecksumIEEE(frame.Bytes()))
	frame.Write(headers.Bytes())
	frame.Write(payload)
	binary.Write(&frame, binary.BigEndian, crc32.ChecksumIEEE(frame.Bytes()))
	return frame.Bytes()
}

func TestProviderStreamErrorLogsExcludeUpstreamPayload(t *testing.T) {
	const private = "PRIVATE_UPSTREAM_SENTINEL"
	const number = "987654321012345678909876543210123456789"
	for _, name := range []string{"openai", "azure", "gemini", "bedrock"} {
		t.Run(name, func(t *testing.T) {
			var logs bytes.Buffer
			previous := slog.Default()
			slog.SetDefault(slog.New(slog.NewJSONHandler(&logs, nil)))
			defer slog.SetDefault(previous)
			upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if name == "bedrock" {
					w.Header().Set("Content-Type", "application/vnd.amazon.eventstream")
					w.Write(bedrockExceptionFrame([]byte(`{"message":"` + private + `"}`)))
					return
				}
				w.Header().Set("Content-Type", "text/event-stream")
				fmt.Fprintf(w, "data: {\"id\":%s,\"private\":%q,\"candidates\":[{\"finishReason\":%s}]}\n\ndata: [DONE]\n\n", number, private, number)
			}))
			defer upstream.Close()
			cfg := config.ProviderConfig{BaseURL: upstream.URL, APIKey: "synthetic-test-key"}
			var provider providers.Provider
			var err error
			switch name {
			case "openai":
				provider, err = openai.New(name, cfg)
			case "azure":
				provider, err = azure.New(name, cfg)
			case "gemini":
				provider, err = gemini.New(name, cfg)
			case "bedrock":
				cfg.AWSAccessKeyID, cfg.AWSSecretAccessKey = "synthetic-access-key", "synthetic-secret-key"
				provider, err = bedrock.New(name, cfg)
			}
			if err != nil {
				t.Fatal(err)
			}
			defer provider.Close()
			chunks, errs := provider.StreamChatCompletion(context.Background(), &models.ChatCompletionRequest{Model: "test-model", Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"prompt"`)}}})
			for chunks != nil || errs != nil {
				select {
				case _, ok := <-chunks:
					if !ok {
						chunks = nil
					}
				case _, ok := <-errs:
					if !ok {
						errs = nil
					}
				}
			}
			if !strings.Contains(logs.String(), `"provider":"`+name+`"`) || !strings.Contains(logs.String(), `"status":200`) {
				t.Fatalf("stream failure was not logged with identifiers: %s", logs.String())
			}
			if strings.Contains(logs.String(), private) || strings.Contains(logs.String(), number) || strings.Contains(logs.String(), `"error":`) || strings.Contains(logs.String(), `"data":`) {
				t.Fatalf("upstream payload escaped through provider logs: %s", logs.String())
			}
		})
	}
}
