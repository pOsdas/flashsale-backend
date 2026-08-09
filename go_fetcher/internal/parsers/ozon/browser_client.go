package ozon

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"go_fetcher/internal/models"
	"go_fetcher/internal/parsers/browsergateway"
	"io"
	"log/slog"
	"net/http"
	"net/url"
	"strings"
	"time"
)

const (
	ozonBrowserProductPath  = "/api/v1/product"
	ozonBrowserSearchPath   = "/api/v1/search"
	ozonBrowserCategoryPath = "/api/v1/category"
)

type BrowserClient struct {
	client   *http.Client
	endpoint string
	timeout  time.Duration
	logger   *slog.Logger
}

func NewBrowserClient(enabled bool, rawURL string, timeout time.Duration, logger *slog.Logger) *BrowserClient {
	if !enabled {
		return nil
	}

	rawURL = strings.TrimSpace(rawURL)
	if rawURL == "" {
		return nil
	}

	if timeout <= 0 {
		timeout = defaultOzonBrowserFetcherTimeout
	}

	if logger == nil {
		logger = slog.Default()
	}

	return &BrowserClient{
		client: &http.Client{
			Timeout: timeout,
		},
		endpoint: normalizeBrowserFetcherEndpoint(rawURL),
		timeout:  timeout,
		logger:   logger,
	}
}

func (c *BrowserClient) Enabled() bool {
	return c != nil && strings.TrimSpace(c.endpoint) != ""
}

func (c *BrowserClient) ParseProduct(ctx context.Context, productInput string) (models.Product, error) {
	if !c.Enabled() {
		return models.Product{}, fmt.Errorf("ozon browser fetcher is not configured")
	}

	var productResponse ozonBrowserProductEnvelope
	if err := c.doPost(ctx, ozonBrowserProductPath, ozonBrowserProductRequest{
		URL:            strings.TrimSpace(productInput),
		TimeoutSeconds: int(c.timeout.Seconds()),
	}, &productResponse); err != nil {
		return models.Product{}, err
	}

	if strings.EqualFold(strings.TrimSpace(productResponse.Status), "error") {
		return models.Product{}, fmt.Errorf("browser fetcher error: %s", strings.TrimSpace(productResponse.Error))
	}

	product := productResponse.Product.toProduct(productInput)
	if !isValidOzonProduct(product) {
		return models.Product{}, fmt.Errorf("browser fetcher returned invalid Ozon product")
	}

	c.logger.Info(
		"ozon product parsed by browser fallback",
		slog.String("sku", product.SKU),
		slog.String("title", product.Title),
		slog.Int("price_cents", product.PriceCents),
		slog.Int("available", product.Available),
	)

	return product, nil
}

func (c *BrowserClient) SearchProducts(ctx context.Context, query string, limit int) ([]models.Product, error) {
	if !c.Enabled() {
		return nil, fmt.Errorf("ozon browser fetcher is not configured")
	}

	var productResponses []ozonBrowserProductResponse
	if err := c.doPost(ctx, ozonBrowserSearchPath, ozonBrowserSearchRequest{
		Query:          strings.TrimSpace(query),
		Limit:          limit,
		TimeoutSeconds: int(c.timeout.Seconds()),
	}, &productResponses); err != nil {
		return nil, err
	}

	products := browserProductResponsesToProducts(productResponses, "")
	products = deduplicateProductsBySKU(products)

	if !isValidOzonProductList(products) {
		return nil, fmt.Errorf("browser fetcher returned invalid Ozon search products")
	}

	c.logger.Info(
		"ozon search parsed by browser fallback",
		slog.String("query", query),
		slog.Int("products_found", len(products)),
	)

	return products, nil
}

func (c *BrowserClient) CategoryProducts(ctx context.Context, categoryInput string, limit int) ([]models.Product, error) {
	if !c.Enabled() {
		return nil, fmt.Errorf("ozon browser fetcher is not configured")
	}

	var productResponses []ozonBrowserProductResponse
	if err := c.doPost(ctx, ozonBrowserCategoryPath, ozonBrowserCategoryRequest{
		URL:            strings.TrimSpace(categoryInput),
		Limit:          limit,
		TimeoutSeconds: int(c.timeout.Seconds()),
	}, &productResponses); err != nil {
		return nil, err
	}

	products := browserProductResponsesToProducts(productResponses, "")
	products = deduplicateProductsBySKU(products)

	if !isValidOzonProductList(products) {
		return nil, fmt.Errorf("browser fetcher returned invalid Ozon category products")
	}

	c.logger.Info(
		"ozon category parsed by browser fallback",
		slog.String("category_input", categoryInput),
		slog.Int("products_found", len(products)),
	)

	return products, nil
}

func (c *BrowserClient) doPost(ctx context.Context, path string, requestPayload any, responsePayload any) error {
	requestBody, err := json.Marshal(requestPayload)
	if err != nil {
		return fmt.Errorf("encode browser fetcher request: %w", err)
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.endpointFor(path), bytes.NewReader(requestBody))
	if err != nil {
		return fmt.Errorf("create browser fetcher request: %w", err)
	}

	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Accept", "application/json")

	if browsergateway.IsInteractiveRequest(ctx) {
		req.Header.Set(
			browsergateway.RequestModeHeader,
			browsergateway.RequestModeInteractive,
		)
	}

	resp, err := c.client.Do(req)
	if err != nil {
		return fmt.Errorf("execute browser fetcher request: %w", err)
	}
	defer resp.Body.Close()

	responseBody, err := io.ReadAll(resp.Body)
	if err != nil {
		return fmt.Errorf("read browser fetcher response: %w", err)
	}

	if temporaryErr := browsergateway.TemporaryErrorFromHTTPResponse(
		resp.StatusCode,
		responseBody,
		resp.Header.Get("Retry-After"),
	); temporaryErr != nil {
		return temporaryErr
	}

	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		var errorResponse ozonBrowserErrorResponse
		if err := json.Unmarshal(responseBody, &errorResponse); err == nil && strings.TrimSpace(errorResponse.Error) != "" {
			return fmt.Errorf("browser fetcher status %d: %s", resp.StatusCode, errorResponse.Error)
		}

		return fmt.Errorf("browser fetcher status %d: %s", resp.StatusCode, limitString(string(responseBody), 1000))
	}

	if productEnvelope, ok := responsePayload.(*ozonBrowserProductEnvelope); ok {
		if err := decodeBrowserProductResponse(responseBody, productEnvelope); err != nil {
			return fmt.Errorf("decode browser fetcher response: %w, body: %s", err, limitString(string(responseBody), 1000))
		}

		return nil
	}

	if err := json.Unmarshal(responseBody, responsePayload); err != nil {
		return fmt.Errorf("decode browser fetcher response: %w, body: %s", err, limitString(string(responseBody), 1000))
	}

	return nil
}

func decodeBrowserProductResponse(responseBody []byte, responsePayload *ozonBrowserProductEnvelope) error {
	if err := json.Unmarshal(responseBody, responsePayload); err == nil && strings.TrimSpace(responsePayload.Status) != "" {
		return nil
	}

	var productResponse ozonBrowserProductResponse
	if err := json.Unmarshal(responseBody, &productResponse); err != nil {
		return err
	}

	responsePayload.Status = "ok"
	responsePayload.Product = productResponse

	return nil
}

func firstNonEmptyString(values ...string) string {
	for _, value := range values {
		value = strings.TrimSpace(value)
		if value != "" {
			return value
		}
	}

	return ""
}

func (r ozonBrowserProductResponse) toProduct(productInput string) models.Product {
	currency := strings.TrimSpace(r.Currency)
	if currency == "" {
		currency = defaultCurrency
	}

	productPath := strings.TrimSpace(r.ProductPath)
	if productPath == "" {
		productPath = buildBrowserProductPath(productInput)
	}

	productURL := strings.TrimSpace(r.URL)
	if productURL == "" {
		productURL = buildBrowserProductURL(productInput)
	}

	return models.Product{
		SKU:           firstNonEmptyString(r.ExternalID, r.SKU),
		Title:         strings.TrimSpace(r.Title),
		SellerName:    strings.TrimSpace(r.SellerName),
		Brand:         strings.TrimSpace(r.Brand),
		PriceCents:    r.PriceCents,
		OldPriceCents: r.OldPriceCents,
		Currency:      currency,
		Available:     r.Available,
		IsActive:      r.IsActive,
		Rating:        r.Rating,
		ReviewsCount:  r.ReviewsCount,
		ProductPath:   productPath,
		URL:           productURL,
	}
}

func browserProductResponsesToProducts(responses []ozonBrowserProductResponse, productInput string) []models.Product {
	products := make([]models.Product, 0, len(responses))

	for _, response := range responses {
		products = append(products, response.toProduct(productInput))
	}

	return products
}

func (c *BrowserClient) endpointFor(path string) string {
	parsedURL, err := url.Parse(c.endpoint)
	if err != nil {
		return strings.TrimRight(c.endpoint, "/") + path
	}

	parsedURL.Path = path
	parsedURL.RawQuery = ""
	parsedURL.Fragment = ""

	return parsedURL.String()
}

func normalizeBrowserFetcherEndpoint(rawURL string) string {
	parsedURL, err := url.Parse(strings.TrimRight(strings.TrimSpace(rawURL), "/"))
	if err != nil {
		return strings.TrimRight(strings.TrimSpace(rawURL), "/") + ozonBrowserProductPath
	}

	if strings.TrimSpace(parsedURL.Path) == "" {
		parsedURL.Path = ozonBrowserProductPath
	}

	return parsedURL.String()
}

func buildBrowserProductURL(productInput string) string {
	productInput = strings.TrimSpace(productInput)
	if strings.HasPrefix(productInput, "http://") || strings.HasPrefix(productInput, "https://") {
		return productInput
	}

	if strings.HasPrefix(productInput, "/") {
		return "https://www.ozon.ru" + productInput
	}

	return productInput
}

func buildBrowserProductPath(productInput string) string {
	productInput = strings.TrimSpace(productInput)
	if strings.HasPrefix(productInput, "/") {
		return productInput
	}

	parsedURL, err := url.Parse(productInput)
	if err != nil {
		return ""
	}

	return parsedURL.Path
}
