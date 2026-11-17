package mock

import (
	"encoding/base64"
	"encoding/binary"
	"fmt"
	"math"
	"net/http"
	"time"
)

// maxEmbeddingDims bounds the dimensions a request may ask for.
const maxEmbeddingDims = 4096

// tinyPNG is a 1x1 transparent PNG, the image every generation returns.
const tinyPNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="

// renderEmbeddings answers /v1/embeddings with deterministic vectors, one per
// input, as floats or (encoding_format "base64") little-endian float32s.
func (s *Server) renderEmbeddings(w http.ResponseWriter, req *Request, resp *Response, cost float64) {
	dims := min(intField(req.JSON, "dimensions", 8), maxEmbeddingDims)
	asBase64 := req.JSON["encoding_format"] == "base64"
	data := make([]any, 0, resp.Usage.EmbeddingInputs)
	for i := 0; i < resp.Usage.EmbeddingInputs; i++ {
		vec := mockVector(i, dims)
		item := map[string]any{"object": "embedding", "index": i, "embedding": vec}
		if asBase64 {
			item["embedding"] = float32Base64(vec)
		}
		data = append(data, item)
	}
	body := map[string]any{"object": "list", "data": data, "model": req.Model}
	if !resp.NoUsage {
		usage := map[string]any{"prompt_tokens": resp.Usage.Prompt, "total_tokens": resp.Usage.Prompt}
		if resp.ReportCost {
			usage["cost"] = cost
		}
		body["usage"] = usage
	}
	writeJSON(w, http.StatusOK, body)
}

func mockVector(input, dims int) []float32 {
	vec := make([]float32, dims)
	for j := range vec {
		vec[j] = float32(((input+1)*(j+1))%7+1) / 8
	}
	return vec
}

func float32Base64(vec []float32) string {
	buf := make([]byte, 4*len(vec))
	for i, f := range vec {
		binary.LittleEndian.PutUint32(buf[4*i:], math.Float32bits(f))
	}
	return base64.StdEncoding.EncodeToString(buf)
}

// renderImages answers /v1/images/generations with n images, as b64_json when
// the request asks for it and as URLs otherwise.
func renderImages(w http.ResponseWriter, req *Request, resp *Response) {
	asBase64 := req.JSON["response_format"] == "b64_json"
	data := make([]any, 0, resp.Usage.Images)
	for i := 0; i < resp.Usage.Images; i++ {
		if asBase64 {
			data = append(data, map[string]any{"b64_json": tinyPNG})
		} else {
			data = append(data, map[string]any{"url": fmt.Sprintf("https://mock.invalid/image-%d.png", i)})
		}
	}
	writeJSON(w, http.StatusOK, map[string]any{"created": time.Now().Unix(), "data": data})
}

// renderSpeech answers /v1/audio/speech with a few bytes of fake MP3.
func renderSpeech(w http.ResponseWriter, resp *Response) {
	w.Header().Set("Content-Type", "audio/mpeg")
	w.WriteHeader(http.StatusOK)
	_, _ = fmt.Fprintf(w, "ID3mock-audio:%d-chars", resp.Usage.TextChars)
}
