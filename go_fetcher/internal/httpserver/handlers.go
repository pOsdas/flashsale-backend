package httpserver

import (
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"strings"
	"time"

	"go_fetcher/internal/observability"
	"go_fetcher/internal/parsers/browsergateway"
)

func (s *Server) handleFetchProduct(w http.ResponseWriter, r *http.Request) {
	startedAt := time.Now()
	marketplace := "unknown"
	result := "unknown"
	errorType := "none"

	defer func() {
		observeProductFetch(
			marketplace,
			result,
			errorType,
			startedAt,
		)
	}()

	s.logger.Info(
		"GO_FETCHER_HTTP_REQUEST_RECEIVED",
		slog.String("method", r.Method),
		slog.String("path", r.URL.Path),
		slog.String("remote_addr", r.RemoteAddr),
		slog.String("content_type", r.Header.Get("Content-Type")),
		slog.Bool("has_api_key", r.Header.Get("X-Fetcher-Api-Key") != ""),
	)

	if r.Method != http.MethodPost {
		result = "method_not_allowed"
		errorType = "validation_error"
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}

	if s.apiKey != "" {
		requestAPIKey := r.Header.Get("X-Fetcher-Api-Key")
		if requestAPIKey != s.apiKey {
			result = "unauthorized"
			errorType = "validation_error"
			writeError(w, http.StatusUnauthorized, "invalid fetcher api key")
			return
		}
	}

	contentType := r.Header.Get("Content-Type")
	if !strings.HasPrefix(contentType, "application/json") {
		result = "unsupported_media_type"
		errorType = "validation_error"
		writeError(w, http.StatusUnsupportedMediaType, "content type must be application/json")
		return
	}

	var req FetchProductRequest

	decoder := json.NewDecoder(r.Body)
	decoder.DisallowUnknownFields()

	if err := decoder.Decode(&req); err != nil {
		result = "invalid_json"
		errorType = "validation_error"
		writeError(w, http.StatusBadRequest, "invalid json request body")
		return
	}

	req.Marketplace = strings.ToLower(strings.TrimSpace(req.Marketplace))
	marketplace = observability.NormalizeMarketplace(req.Marketplace)
	req.URL = strings.TrimSpace(req.URL)
	req.ExternalID = strings.TrimSpace(req.ExternalID)

	if err := validateFetchProductRequest(req); err != nil {
		result = "validation_error"
		errorType = "validation_error"
		writeError(w, http.StatusBadRequest, err.Error())
		return
	}

	var (
		product *ProductDTO
		err     error
	)

	switch req.Marketplace {
	case "wb":
		if s.wbFetcher == nil {
			result = "not_configured"
			errorType = "parser_error"
			writeError(w, http.StatusInternalServerError, "wb fetcher is not configured")
			return
		}

		product, err = s.wbFetcher(r.Context(), req)

	case "ozon":
		if s.ozonFetcher == nil {
			result = "not_configured"
			errorType = "parser_error"
			writeError(w, http.StatusInternalServerError, "ozon fetcher is not configured")
			return
		}

		product, err = s.ozonFetcher(r.Context(), req)

	default:
		result = "validation_error"
		errorType = "validation_error"
		writeError(w, http.StatusBadRequest, "unsupported marketplace")
		return
	}

	if err != nil {
		classifiedError := classifyParserError(err)
		result = "fetch_error"
		errorType = classifiedError.ErrorType

		var temporaryUnavailable *browsergateway.TemporaryUnavailableError
		if errors.As(err, &temporaryUnavailable) {
			result = "temporarily_unavailable"
			errorType = "temporarily_unavailable"
			if temporaryUnavailable.RetryAfterSeconds > 0 {
				w.Header().Set(
					"Retry-After",
					fmt.Sprintf("%d", temporaryUnavailable.RetryAfterSeconds),
				)
			}
			s.logger.Warn(
				"marketplace gateway is temporarily unavailable",
				"marketplace", req.Marketplace,
				"url", req.URL,
				"error", err,
			)
			writeError(w, http.StatusServiceUnavailable, err.Error())
			return
		}

		s.logger.Error(
			"failed to fetch product",
			"marketplace", req.Marketplace,
			"url", req.URL,
			"error", err,
		)

		writeError(w, http.StatusBadGateway, err.Error())
		return
	}

	if product == nil {
		result = "not_found"
		errorType = "product_not_found"
		writeError(w, http.StatusBadGateway, "product was not found")
		return
	}

	if req.ExternalID != "" {
		product.ExternalID = req.ExternalID
	}

	result = "success"
	errorType = "none"

	observability.ProductsReturnedTotal.WithLabelValues(
		marketplace,
	).Inc()
	observability.LastSuccessfulProductFetchTimestampSeconds.WithLabelValues(
		marketplace,
	).SetToCurrentTime()

	writeJSON(w, http.StatusOK, FetchProductResponse{
		Status:  "success",
		Product: product,
	})
}

func validateFetchProductRequest(req FetchProductRequest) error {
	if req.Marketplace == "" {
		return errors.New("marketplace is required")
	}

	if req.Marketplace != "wb" && req.Marketplace != "ozon" {
		return errors.New("marketplace must be wb or ozon")
	}

	if req.URL == "" {
		return errors.New("url is required")
	}

	if !strings.HasPrefix(req.URL, "http://") && !strings.HasPrefix(req.URL, "https://") {
		return errors.New("url must start with http:// or https://")
	}

	return nil
}

func writeJSON(w http.ResponseWriter, statusCode int, payload FetchProductResponse) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(statusCode)

	_ = json.NewEncoder(w).Encode(payload)
}

func writeError(w http.ResponseWriter, statusCode int, message string) {
	writeJSON(w, statusCode, FetchProductResponse{
		Status: "error",
		Error:  message,
	})
}
