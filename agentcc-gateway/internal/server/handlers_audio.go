package server

import (
	"context"
	"encoding/base64"
	"encoding/binary"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"strconv"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers"
)

// CreateSpeech handles POST /v1/audio/speech.
func (h *Handlers) CreateSpeech(w http.ResponseWriter, r *http.Request) {
	rc := models.AcquireRequestContext()
	defer rc.Release()

	rc.RequestID = models.GetRequestID(r.Context())
	rc.TraceID = w.Header().Get("x-agentcc-trace-id")
	rc.EndpointType = "speech"
	rc.RequestHeaders = cloneRequestHeaders(r)
	body, err := io.ReadAll(io.LimitReader(r.Body, h.maxBodySize+1))
	if err != nil {
		models.WriteError(w, models.ErrBadRequest("read_error", "Failed to read request body"))
		return
	}
	if int64(len(body)) > h.maxBodySize {
		models.WriteError(w, &models.APIError{
			Status:  http.StatusRequestEntityTooLarge,
			Type:    models.ErrTypeInvalidRequest,
			Code:    "request_too_large",
			Message: fmt.Sprintf("Request body exceeds maximum size of %d bytes", h.maxBodySize),
		})
		return
	}

	var req models.SpeechRequest
	if err := json.Unmarshal(body, &req); err != nil {
		models.WriteError(w, models.ErrBadRequest("invalid_json", "Invalid JSON in request body: "+err.Error()))
		return
	}
	if req.Model == "" {
		models.WriteError(w, models.ErrBadRequest("missing_model", "model is required"))
		return
	}
	if req.Input == "" {
		models.WriteError(w, models.ErrBadRequest("missing_input", "input is required"))
		return
	}
	if req.Voice == "" {
		models.WriteError(w, models.ErrBadRequest("missing_voice", "voice is required"))
		return
	}

	rc.Model = req.Model
	rc.SpeechRequest = &req

	setAuthMetadataFromRequest(rc, r)

	// Resolve timeout and apply.
	timeout := h.resolveTimeout(rc, r)
	ctx, cancel := context.WithTimeout(r.Context(), timeout)
	defer cancel()
	rc.Metadata["timeout_ms"] = fmt.Sprintf("%d", timeout.Milliseconds())

	// Resolve per-org config.
	h.peekKeyOrgID(rc)
	orgID, orgCfg := h.resolveOrgConfig(rc)
	if orgID != "" {
		rc.Metadata["org_id"] = orgID
	}
	h.applyOrgModelMapOverrides(orgCfg, rc)

	// Resolve provider.
	provider, err := h.resolveProvider(ctx, rc, req.Model)
	if err != nil {
		if orgCfg != nil && orgID != "" && h.orgProviderCache != nil {
			if orgP, providerID := h.resolveOrgProvider(orgID, orgCfg, req.Model); orgP != nil {
				provider = orgP
				rc.Provider = providerID
				rc.Metadata["org_provider"] = "true"
				err = nil
			}
		}
		if err != nil {
			models.WriteErrorFromError(w, err)
			return
		}
	} else if shouldApplyOrgProviderOverride(rc) {
		provider = h.applyOrgProviderOverride(orgID, orgCfg, rc.Provider, provider)
	}

	// Type-assert to SpeechProvider.
	sp, ok := provider.(providers.SpeechProvider)
	if !ok {
		models.WriteError(w, &models.APIError{
			Status:  http.StatusNotImplemented,
			Type:    models.ErrTypeServer,
			Code:    "not_supported",
			Message: fmt.Sprintf("Provider %q does not support text-to-speech", rc.Provider),
		})
		return
	}

	var audioData []byte
	var contentType string
	providerCall := func(ctx context.Context, rc *models.RequestContext) error {
		audioStream, ct, err := sp.CreateSpeech(ctx, rc.SpeechRequest)
		if err != nil {
			return err
		}
		defer audioStream.Close()

		data, err := io.ReadAll(audioStream)
		if err != nil {
			return models.ErrInternal(fmt.Sprintf("reading speech response: %v", err))
		}

		audioData = data
		contentType = ct
		rc.ResolvedModel = rc.Model
		rc.SetMetadata("input_characters", strconv.Itoa(len(rc.SpeechRequest.Input)))
		rc.SetMetadata("response_content_type", ct)
		rc.SetMetadata("response_size_bytes", strconv.Itoa(len(data)))
		return nil
	}

	if err := h.engine.Process(ctx, rc, providerCall); err != nil {
		models.WriteErrorFromError(w, err)
		return
	}

	h.setAgentccHeaders(w, rc)
	w.Header().Set("Content-Type", contentType)
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write(audioData)
}

// CreateTranslation handles POST /v1/audio/translations.
// Translates audio in any language to English text.
func (h *Handlers) CreateTranslation(w http.ResponseWriter, r *http.Request) {
	rc := models.AcquireRequestContext()
	defer rc.Release()

	rc.RequestID = models.GetRequestID(r.Context())
	rc.TraceID = w.Header().Get("x-agentcc-trace-id")
	rc.EndpointType = "translation"
	rc.RequestHeaders = cloneRequestHeaders(r)
	const maxAudioSize = 25 << 20
	if err := r.ParseMultipartForm(maxAudioSize); err != nil {
		models.WriteError(w, models.ErrBadRequest("invalid_form", "Failed to parse multipart form: "+err.Error()))
		return
	}

	file, header, err := r.FormFile("file")
	if err != nil {
		models.WriteError(w, models.ErrBadRequest("missing_file", "file is required"))
		return
	}
	defer file.Close()

	fileData, err := io.ReadAll(io.LimitReader(file, maxAudioSize+1))
	if err != nil {
		models.WriteError(w, models.ErrBadRequest("read_error", "Failed to read audio file"))
		return
	}
	if int64(len(fileData)) > maxAudioSize {
		models.WriteError(w, &models.APIError{
			Status:  http.StatusRequestEntityTooLarge,
			Type:    models.ErrTypeInvalidRequest,
			Code:    "file_too_large",
			Message: "Audio file exceeds maximum size of 25MB",
		})
		return
	}

	model := r.FormValue("model")
	if model == "" {
		models.WriteError(w, models.ErrBadRequest("missing_model", "model is required"))
		return
	}

	req := models.TranslationRequest{
		Model:          model,
		FileData:       fileData,
		FileName:       header.Filename,
		Prompt:         r.FormValue("prompt"),
		ResponseFormat: r.FormValue("response_format"),
	}

	rc.Model = req.Model
	rc.TranslationReq = &req

	setAuthMetadataFromRequest(rc, r)

	// Resolve timeout and apply.
	timeout := h.resolveTimeout(rc, r)
	ctx, cancel := context.WithTimeout(r.Context(), timeout)
	defer cancel()
	rc.Metadata["timeout_ms"] = fmt.Sprintf("%d", timeout.Milliseconds())

	// Resolve per-org config.
	h.peekKeyOrgID(rc)
	orgID, orgCfg := h.resolveOrgConfig(rc)
	if orgID != "" {
		rc.Metadata["org_id"] = orgID
	}
	h.applyOrgModelMapOverrides(orgCfg, rc)

	// Resolve provider.
	provider, err := h.resolveProvider(ctx, rc, req.Model)
	if err != nil {
		if orgCfg != nil && orgID != "" && h.orgProviderCache != nil {
			if orgP, providerID := h.resolveOrgProvider(orgID, orgCfg, req.Model); orgP != nil {
				provider = orgP
				rc.Provider = providerID
				rc.Metadata["org_provider"] = "true"
				err = nil
			}
		}
		if err != nil {
			models.WriteErrorFromError(w, err)
			return
		}
	} else if shouldApplyOrgProviderOverride(rc) {
		provider = h.applyOrgProviderOverride(orgID, orgCfg, rc.Provider, provider)
	}

	// Type-assert to TranslationProvider.
	tp, ok := provider.(providers.TranslationProvider)
	if !ok {
		models.WriteError(w, &models.APIError{
			Status:  http.StatusNotImplemented,
			Type:    models.ErrTypeServer,
			Code:    "not_supported",
			Message: fmt.Sprintf("Provider %q does not support audio translation", rc.Provider),
		})
		return
	}

	providerCall := func(ctx context.Context, rc *models.RequestContext) error {
		resp, err := tp.CreateTranslation(ctx, rc.TranslationReq)
		if err != nil {
			return err
		}
		rc.TranslationResp = resp
		rc.ResolvedModel = rc.Model
		if secs, ok := wavDurationSeconds(rc.TranslationReq.FileData); ok {
			rc.SetMetadata("audio_seconds", formatAudioSeconds(secs))
		}
		return nil
	}

	if err := h.engine.Process(ctx, rc, providerCall); err != nil {
		models.WriteErrorFromError(w, err)
		return
	}

	if rc.TranslationResp == nil {
		models.WriteError(w, models.ErrInternal("no response from provider"))
		return
	}

	h.setAgentccHeaders(w, rc)
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(rc.TranslationResp)
}

// CreateTranscription handles POST /v1/audio/transcriptions.
func (h *Handlers) CreateTranscription(w http.ResponseWriter, r *http.Request) {
	rc := models.AcquireRequestContext()
	defer rc.Release()

	rc.RequestID = models.GetRequestID(r.Context())
	rc.TraceID = w.Header().Get("x-agentcc-trace-id")
	rc.EndpointType = "transcription"
	rc.RequestHeaders = cloneRequestHeaders(r)
	const maxAudioSize = 25 << 20
	if err := r.ParseMultipartForm(maxAudioSize); err != nil {
		models.WriteError(w, models.ErrBadRequest("invalid_form", "Failed to parse multipart form: "+err.Error()))
		return
	}

	file, header, err := r.FormFile("file")
	if err != nil {
		models.WriteError(w, models.ErrBadRequest("missing_file", "file is required"))
		return
	}
	defer file.Close()

	fileData, err := io.ReadAll(io.LimitReader(file, maxAudioSize+1))
	if err != nil {
		models.WriteError(w, models.ErrBadRequest("read_error", "Failed to read audio file"))
		return
	}
	if int64(len(fileData)) > maxAudioSize {
		models.WriteError(w, &models.APIError{
			Status:  http.StatusRequestEntityTooLarge,
			Type:    models.ErrTypeInvalidRequest,
			Code:    "file_too_large",
			Message: "Audio file exceeds maximum size of 25MB",
		})
		return
	}

	model := r.FormValue("model")
	if model == "" {
		models.WriteError(w, models.ErrBadRequest("missing_model", "model is required"))
		return
	}

	req := models.TranscriptionRequest{
		Model:          model,
		FileData:       fileData,
		FileName:       header.Filename,
		Language:       r.FormValue("language"),
		ResponseFormat: r.FormValue("response_format"),
	}

	rc.Model = req.Model
	rc.TranscriptionReq = &req

	setAuthMetadataFromRequest(rc, r)

	// Resolve timeout and apply.
	timeout := h.resolveTimeout(rc, r)
	ctx, cancel := context.WithTimeout(r.Context(), timeout)
	defer cancel()
	rc.Metadata["timeout_ms"] = fmt.Sprintf("%d", timeout.Milliseconds())

	// Resolve per-org config.
	h.peekKeyOrgID(rc)
	orgID, orgCfg := h.resolveOrgConfig(rc)
	if orgID != "" {
		rc.Metadata["org_id"] = orgID
	}
	h.applyOrgModelMapOverrides(orgCfg, rc)

	// Resolve provider.
	provider, err := h.resolveProvider(ctx, rc, req.Model)
	if err != nil {
		if orgCfg != nil && orgID != "" && h.orgProviderCache != nil {
			if orgP, providerID := h.resolveOrgProvider(orgID, orgCfg, req.Model); orgP != nil {
				provider = orgP
				rc.Provider = providerID
				rc.Metadata["org_provider"] = "true"
				err = nil
			}
		}
		if err != nil {
			models.WriteErrorFromError(w, err)
			return
		}
	} else if shouldApplyOrgProviderOverride(rc) {
		provider = h.applyOrgProviderOverride(orgID, orgCfg, rc.Provider, provider)
	}

	// Type-assert to TranscriptionProvider.
	tp, ok := provider.(providers.TranscriptionProvider)
	if !ok {
		models.WriteError(w, &models.APIError{
			Status:  http.StatusNotImplemented,
			Type:    models.ErrTypeServer,
			Code:    "not_supported",
			Message: fmt.Sprintf("Provider %q does not support transcription", rc.Provider),
		})
		return
	}

	providerCall := func(ctx context.Context, rc *models.RequestContext) error {
		resp, err := tp.CreateTranscription(ctx, rc.TranscriptionReq)
		if err != nil {
			return err
		}
		rc.TranscriptionResp = resp
		rc.ResolvedModel = rc.Model
		if secs, ok := wavDurationSeconds(rc.TranscriptionReq.FileData); ok {
			rc.SetMetadata("audio_seconds", formatAudioSeconds(secs))
		}
		return nil
	}

	if err := h.engine.Process(ctx, rc, providerCall); err != nil {
		models.WriteErrorFromError(w, err)
		return
	}

	if rc.TranscriptionResp == nil {
		models.WriteError(w, models.ErrInternal("no response from provider"))
		return
	}

	h.setAgentccHeaders(w, rc)
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(rc.TranscriptionResp)
}

func formatAudioSeconds(seconds float64) string {
	return strconv.FormatFloat(seconds, 'f', 6, 64)
}

func wavDurationSeconds(data []byte) (float64, bool) {
	if len(data) < 44 || string(data[0:4]) != "RIFF" || string(data[8:12]) != "WAVE" {
		return 0, false
	}

	var sampleRate uint32
	var channels uint16
	var bitsPerSample uint16
	var dataSize uint32

	offset := 12
	for offset+8 <= len(data) {
		chunkID := string(data[offset : offset+4])
		chunkSize := int(binary.LittleEndian.Uint32(data[offset+4 : offset+8]))
		chunkStart := offset + 8
		chunkEnd := chunkStart + chunkSize
		if chunkEnd > len(data) {
			return 0, false
		}

		switch chunkID {
		case "fmt ":
			if chunkSize < 16 {
				return 0, false
			}
			channels = binary.LittleEndian.Uint16(data[chunkStart+2 : chunkStart+4])
			sampleRate = binary.LittleEndian.Uint32(data[chunkStart+4 : chunkStart+8])
			bitsPerSample = binary.LittleEndian.Uint16(data[chunkStart+14 : chunkStart+16])
		case "data":
			dataSize = uint32(chunkSize)
		}

		offset = chunkEnd
		if chunkSize%2 == 1 {
			offset++
		}
	}

	if sampleRate == 0 || channels == 0 || bitsPerSample == 0 || dataSize == 0 {
		return 0, false
	}

	bytesPerSecond := float64(sampleRate) * float64(channels) * float64(bitsPerSample) / 8.0
	if bytesPerSecond <= 0 {
		return 0, false
	}

	return float64(dataSize) / bytesPerSecond, true
}

// StreamSpeech handles POST /v1/audio/speech/stream.
// Returns TTS audio as base64-encoded SSE chunks.
func (h *Handlers) StreamSpeech(w http.ResponseWriter, r *http.Request) {
	rc := models.AcquireRequestContext()
	defer rc.Release()

	rc.RequestID = models.GetRequestID(r.Context())
	rc.TraceID = w.Header().Get("x-agentcc-trace-id")
	rc.EndpointType = "speech_stream"
	rc.RequestHeaders = cloneRequestHeaders(r)
	body, err := io.ReadAll(io.LimitReader(r.Body, h.maxBodySize+1))
	if err != nil {
		models.WriteError(w, models.ErrBadRequest("read_error", "Failed to read request body"))
		return
	}
	if int64(len(body)) > h.maxBodySize {
		models.WriteError(w, &models.APIError{
			Status:  http.StatusRequestEntityTooLarge,
			Type:    models.ErrTypeInvalidRequest,
			Code:    "request_too_large",
			Message: fmt.Sprintf("Request body exceeds maximum size of %d bytes", h.maxBodySize),
		})
		return
	}

	var req models.SpeechRequest
	if err := json.Unmarshal(body, &req); err != nil {
		models.WriteError(w, models.ErrBadRequest("invalid_json", "Invalid JSON in request body: "+err.Error()))
		return
	}
	if req.Model == "" {
		models.WriteError(w, models.ErrBadRequest("missing_model", "model is required"))
		return
	}
	if req.Input == "" {
		models.WriteError(w, models.ErrBadRequest("missing_input", "input is required"))
		return
	}
	if req.Voice == "" {
		models.WriteError(w, models.ErrBadRequest("missing_voice", "voice is required"))
		return
	}

	rc.Model = req.Model
	rc.SpeechRequest = &req

	setAuthMetadataFromRequest(rc, r)

	// Resolve timeout and apply.
	timeout := h.resolveTimeout(rc, r)
	ctx, cancel := context.WithTimeout(r.Context(), timeout)
	defer cancel()
	rc.Metadata["timeout_ms"] = fmt.Sprintf("%d", timeout.Milliseconds())

	// Resolve per-org config.
	h.peekKeyOrgID(rc)
	orgID, orgCfg := h.resolveOrgConfig(rc)
	if orgID != "" {
		rc.Metadata["org_id"] = orgID
	}
	h.applyOrgModelMapOverrides(orgCfg, rc)

	// Resolve provider.
	provider, err := h.resolveProvider(ctx, rc, req.Model)
	if err != nil {
		if orgCfg != nil && orgID != "" && h.orgProviderCache != nil {
			if orgP, providerID := h.resolveOrgProvider(orgID, orgCfg, req.Model); orgP != nil {
				provider = orgP
				rc.Provider = providerID
				rc.Metadata["org_provider"] = "true"
				err = nil
			}
		}
		if err != nil {
			models.WriteErrorFromError(w, err)
			return
		}
	} else if shouldApplyOrgProviderOverride(rc) {
		provider = h.applyOrgProviderOverride(orgID, orgCfg, rc.Provider, provider)
	}

	// Type-assert to SpeechProvider.
	sp, ok := provider.(providers.SpeechProvider)
	if !ok {
		models.WriteError(w, &models.APIError{
			Status:  http.StatusNotImplemented,
			Type:    models.ErrTypeServer,
			Code:    "not_supported",
			Message: fmt.Sprintf("Provider %q does not support text-to-speech", rc.Provider),
		})
		return
	}

	// Get the audio stream from the provider.
	audioStream, contentType, err := sp.CreateSpeech(ctx, rc.SpeechRequest)
	if err != nil {
		models.WriteErrorFromError(w, err)
		return
	}
	defer audioStream.Close()

	// Set up SSE streaming.
	flusher, ok := w.(http.Flusher)
	if !ok {
		models.WriteError(w, models.ErrInternal("streaming not supported by server"))
		return
	}

	h.setAgentccHeaders(w, rc)
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Connection", "keep-alive")
	w.WriteHeader(http.StatusOK)
	flusher.Flush()

	// Read audio in chunks and send as base64-encoded SSE events.
	const chunkSize = 16 * 1024 // 16KB chunks
	buf := make([]byte, chunkSize)
	index := 0

	for {
		n, readErr := audioStream.Read(buf)
		if n > 0 {
			chunk := models.AudioStreamChunk{
				Index:       index,
				ContentType: contentType,
				Data:        base64.StdEncoding.EncodeToString(buf[:n]),
			}
			data, marshalErr := json.Marshal(chunk)
			if marshalErr != nil {
				slog.Warn("error marshaling audio chunk", "error", marshalErr)
				return
			}
			if _, writeErr := fmt.Fprintf(w, "data: %s\n\n", data); writeErr != nil {
				slog.Warn("error writing audio stream chunk", "error", writeErr)
				return
			}
			flusher.Flush()
			index++
		}
		if readErr != nil {
			if readErr != io.EOF {
				slog.Warn("error reading audio stream", "error", readErr)
			}
			break
		}
	}

	// Send done event.
	doneChunk := models.AudioStreamChunk{
		Index: index,
		Done:  true,
	}
	doneData, _ := json.Marshal(doneChunk)
	fmt.Fprintf(w, "data: %s\n\n", doneData)
	fmt.Fprint(w, "data: [DONE]\n\n")
	flusher.Flush()
}
