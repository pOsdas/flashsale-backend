package ozon

import (
	"context"
	"errors"
	"fmt"
	"go_fetcher/internal/cookies"
	"go_fetcher/internal/models"
	"log/slog"
	"net/http"
	"strings"
	"time"
)

const (
	defaultCurrency = "RUB"

	ozonPageAPIURL = "https://www.ozon.ru/api/entrypoint-api.bx/page/json/v2"
	ozonUserAgent  = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36 Edg/125.0.0.0"

	defaultOzonHTTPParserTimeout     = 12 * time.Second
	defaultOzonBrowserFetcherTimeout = 35 * time.Second
)

type Parser struct {
	client                *http.Client
	browserClient         *BrowserClient
	logger                *slog.Logger
	cookie                string
	cookieProvider        cookies.Provider
	requestDelay          time.Duration
	maxRetries            int
	retryBaseDelay        time.Duration
	httpParserTimeout     time.Duration
	browserFetcherTimeout time.Duration
}

type ParserConfig struct {
	Cookie                string
	CookieProvider        cookies.Provider
	Timeout               time.Duration
	HTTPParserTimeout     time.Duration
	RequestDelay          time.Duration
	MaxRetries            int
	RetryBaseDelay        time.Duration
	BrowserFetcherURL     string
	BrowserFetcherEnabled bool
	BrowserFetcherTimeout time.Duration
}

func NewParser(cfg ParserConfig, logger *slog.Logger) *Parser {
	if logger == nil {
		logger = slog.Default()
	}

	if cfg.Timeout <= 0 {
		cfg.Timeout = 15 * time.Second
	}

	if cfg.HTTPParserTimeout <= 0 {
		cfg.HTTPParserTimeout = defaultOzonHTTPParserTimeout
	}

	if cfg.RequestDelay < 0 {
		cfg.RequestDelay = 0
	}

	if cfg.MaxRetries < 0 {
		cfg.MaxRetries = 0
	}

	if cfg.RetryBaseDelay <= 0 {
		cfg.RetryBaseDelay = 1 * time.Second
	}

	if cfg.BrowserFetcherTimeout <= 0 {
		cfg.BrowserFetcherTimeout = defaultOzonBrowserFetcherTimeout
	}

	return &Parser{
		client: &http.Client{
			Timeout:   cfg.HTTPParserTimeout,
			Transport: newOzonHTTPTransport(),
		},
		browserClient:         NewBrowserClient(cfg.BrowserFetcherEnabled, cfg.BrowserFetcherURL, cfg.BrowserFetcherTimeout, logger),
		logger:                logger,
		cookie:                cfg.Cookie,
		cookieProvider:        cfg.CookieProvider,
		requestDelay:          cfg.RequestDelay,
		maxRetries:            cfg.MaxRetries,
		retryBaseDelay:        cfg.RetryBaseDelay,
		httpParserTimeout:     cfg.HTTPParserTimeout,
		browserFetcherTimeout: cfg.BrowserFetcherTimeout,
	}
}

func (p *Parser) ParseProduct(ctx context.Context, productInput string) ([]models.Product, error) {
	httpStartedAt := time.Now()
	httpCtx, cancelHTTP := p.newHTTPParserContext(ctx)
	products, err := p.parseProductHTTP(httpCtx, productInput)
	cancelHTTP()
	p.observeHTTPParserResult(
		ctx,
		"product",
		httpStartedAt,
		err,
		products,
	)

	if err == nil && isValidOzonProductList(products) {
		return products, nil
	}

	if !p.shouldUseBrowserFallback(productInput, err, products) {
		return products, err
	}

	if parentErr := ctx.Err(); parentErr != nil {
		skipReason := classifyParentContextReason(parentErr)
		p.observeBrowserFallbackSkipped("product", skipReason)
		p.logBrowserFallbackSkipped(ctx, "product", productInput, 1, ozonBrowserProductPath, err, products, parentErr)
		return products, fallbackSkippedError("parse Ozon product", err, parentErr)
	}

	fallbackReason := classifyFallbackReason(ctx, err, products)
	fallbackStartedAt := p.observeBrowserFallbackStarted(
		"product",
		fallbackReason,
	)
	p.logBrowserFallbackStart(ctx, "product", productInput, 1, ozonBrowserProductPath, err, products)

	browserCtx, cancelBrowser := p.newBrowserFallbackContext(ctx)
	defer cancelBrowser()

	product, browserErr := p.browserClient.ParseProduct(browserCtx, productInput)
	if browserErr != nil {
		failureReason := classifyBrowserFallbackFailure(browserErr)
		p.observeBrowserFallbackFinished(
			"product",
			"failed",
			failureReason,
			fallbackStartedAt,
		)
		p.logBrowserFallbackFailure(ctx, "product", productInput, ozonBrowserProductPath, browserErr)

		if err != nil {
			return nil, fmt.Errorf("parse Ozon product with HTTP failed: %w; browser fallback failed: %w", err, browserErr)
		}

		return products, fmt.Errorf("parse Ozon product with browser fallback: %w", browserErr)
	}

	p.observeBrowserFallbackFinished(
		"product",
		"success",
		fallbackReason,
		fallbackStartedAt,
	)

	return []models.Product{product}, nil
}

func (p *Parser) parseProductHTTP(ctx context.Context, productInput string) ([]models.Product, error) {
	productInput = strings.TrimSpace(productInput)
	if productInput == "" {
		return nil, fmt.Errorf("productInput is empty")
	}

	requestURL, productPath, err := buildProductRequestURL(productInput)
	if err != nil {
		return nil, err
	}

	productID := extractProductID(productPath)
	if productID == "" {
		return nil, fmt.Errorf("failed to extract product id from %q", productInput)
	}

	var response ozonPageResponse

	if err := p.doJSONRequest(ctx, requestURL, &response); err != nil {
		return nil, fmt.Errorf("parse Ozon product %s: %w", productID, err)
	}

	// debugOzonWidgetStates(response.WidgetStates)

	products := extractProductsFromWidgetStates(response.WidgetStates)
	products = deduplicateProductsBySKU(products)

	if len(products) == 0 {
		return nil, fmt.Errorf("no products extracted from Ozon widgetStates")
	}

	product := findProductBySKU(products, productID)
	if product == nil {
		product = &products[0]
	}

	p.logger.Info(
		"ozon product parsed",
		slog.String("product_id", productID),
		slog.String("sku", product.SKU),
		slog.String("title", product.Title),
		slog.Int("price_cents", product.PriceCents),
		slog.Int("available", product.Available),
	)

	return []models.Product{*product}, nil
}

func (p *Parser) shouldUseBrowserFallback(productInput string, err error, products []models.Product) bool {
	if p == nil || p.browserClient == nil || !p.browserClient.Enabled() {
		return false
	}

	if strings.TrimSpace(productInput) == "" {
		return false
	}

	return err != nil || !isValidOzonProductList(products)
}

func isValidOzonProductList(products []models.Product) bool {
	if len(products) == 0 {
		return false
	}

	for _, product := range products {
		if !isValidOzonProduct(product) {
			return false
		}
	}

	return true
}

func isValidOzonProduct(product models.Product) bool {
	return strings.TrimSpace(product.SKU) != "" &&
		strings.TrimSpace(product.Title) != "" &&
		product.PriceCents > 0 &&
		strings.TrimSpace(product.Currency) != ""
}

func (p *Parser) SearchProducts(ctx context.Context, query string, limit int) ([]models.Product, error) {
	query = strings.TrimSpace(query)

	if query == "" {
		return nil, fmt.Errorf("search query is empty")
	}

	if limit <= 0 {
		return nil, fmt.Errorf("limit must be greater than zero")
	}

	httpStartedAt := time.Now()
	httpCtx, cancelHTTP := p.newHTTPParserContext(ctx)
	products, err := p.fetchCatalogProducts(httpCtx, CatalogRequest{
		Mode:  "search",
		Input: query,
		Limit: limit,
	})
	cancelHTTP()
	p.observeHTTPParserResult(
		ctx,
		"search",
		httpStartedAt,
		err,
		products,
	)

	if err == nil && isValidOzonProductList(products) {
		return products, nil
	}

	if !p.shouldUseBrowserFallback(query, err, products) {
		return products, err
	}

	if parentErr := ctx.Err(); parentErr != nil {
		skipReason := classifyParentContextReason(parentErr)
		p.observeBrowserFallbackSkipped("search", skipReason)
		p.logBrowserFallbackSkipped(ctx, "search", query, limit, ozonBrowserSearchPath, err, products, parentErr)
		return products, fallbackSkippedError("search Ozon products", err, parentErr)
	}

	fallbackReason := classifyFallbackReason(ctx, err, products)
	fallbackStartedAt := p.observeBrowserFallbackStarted(
		"search",
		fallbackReason,
	)
	p.logBrowserFallbackStart(ctx, "search", query, limit, ozonBrowserSearchPath, err, products)

	browserCtx, cancelBrowser := p.newBrowserFallbackContext(ctx)
	defer cancelBrowser()

	browserProducts, browserErr := p.browserClient.SearchProducts(browserCtx, query, limit)
	if browserErr != nil {
		failureReason := classifyBrowserFallbackFailure(browserErr)
		p.observeBrowserFallbackFinished(
			"search",
			"failed",
			failureReason,
			fallbackStartedAt,
		)
		p.logBrowserFallbackFailure(ctx, "search", query, ozonBrowserSearchPath, browserErr)

		if err != nil {
			return nil, fmt.Errorf("search Ozon products with HTTP failed: %w; browser fallback failed: %w", err, browserErr)
		}

		return products, fmt.Errorf("search Ozon products with browser fallback: %w", browserErr)
	}

	p.observeBrowserFallbackFinished(
		"search",
		"success",
		fallbackReason,
		fallbackStartedAt,
	)

	return browserProducts, nil
}

func (p *Parser) CategoryProducts(ctx context.Context, categoryInput string, limit int) ([]models.Product, error) {
	categoryInput = strings.TrimSpace(categoryInput)

	if categoryInput == "" {
		return nil, fmt.Errorf("category input is empty")
	}

	if limit <= 0 {
		return nil, fmt.Errorf("limit must be greater than zero")
	}

	httpStartedAt := time.Now()
	httpCtx, cancelHTTP := p.newHTTPParserContext(ctx)
	products, err := p.fetchCatalogProducts(httpCtx, CatalogRequest{
		Mode:  "category",
		Input: categoryInput,
		Limit: limit,
	})
	cancelHTTP()
	p.observeHTTPParserResult(
		ctx,
		"category",
		httpStartedAt,
		err,
		products,
	)

	if err == nil && isValidOzonProductList(products) {
		return products, nil
	}

	if !p.shouldUseBrowserFallback(categoryInput, err, products) {
		return products, err
	}

	if parentErr := ctx.Err(); parentErr != nil {
		skipReason := classifyParentContextReason(parentErr)
		p.observeBrowserFallbackSkipped("category", skipReason)
		p.logBrowserFallbackSkipped(ctx, "category", categoryInput, limit, ozonBrowserCategoryPath, err, products, parentErr)
		return products, fallbackSkippedError("parse Ozon category", err, parentErr)
	}

	fallbackReason := classifyFallbackReason(ctx, err, products)
	fallbackStartedAt := p.observeBrowserFallbackStarted(
		"category",
		fallbackReason,
	)
	p.logBrowserFallbackStart(ctx, "category", categoryInput, limit, ozonBrowserCategoryPath, err, products)

	browserCtx, cancelBrowser := p.newBrowserFallbackContext(ctx)
	defer cancelBrowser()

	browserProducts, browserErr := p.browserClient.CategoryProducts(browserCtx, categoryInput, limit)
	if browserErr != nil {
		failureReason := classifyBrowserFallbackFailure(browserErr)
		p.observeBrowserFallbackFinished(
			"category",
			"failed",
			failureReason,
			fallbackStartedAt,
		)
		p.logBrowserFallbackFailure(ctx, "category", categoryInput, ozonBrowserCategoryPath, browserErr)

		if err != nil {
			return nil, fmt.Errorf("parse Ozon category with HTTP failed: %w; browser fallback failed: %w", err, browserErr)
		}

		return products, fmt.Errorf("parse Ozon category with browser fallback: %w", browserErr)
	}

	p.observeBrowserFallbackFinished(
		"category",
		"success",
		fallbackReason,
		fallbackStartedAt,
	)

	return browserProducts, nil
}

func (p *Parser) newHTTPParserContext(ctx context.Context) (context.Context, context.CancelFunc) {
	return context.WithTimeout(ctx, p.httpParserTimeout)
}

func (p *Parser) newBrowserFallbackContext(ctx context.Context) (context.Context, context.CancelFunc) {
	return context.WithTimeout(ctx, p.browserFetcherTimeout)
}

func (p *Parser) logBrowserFallbackStart(ctx context.Context, mode string, input string, limit int, endpointPath string, err error, products []models.Product) {
	attrs := []slog.Attr{
		slog.String("mode", mode),
		slog.String("input", input),
		slog.Int("limit", limit),
		slog.String("endpoint", p.browserFallbackEndpoint(endpointPath)),
		slog.Int("products_count", len(products)),
		slog.Int("invalid_products_count", countInvalidOzonProducts(products)),
		slog.String("fallback_action", "started"),
		slog.Duration("http_parser_timeout", p.httpParserTimeout),
		slog.Duration("browser_fallback_timeout", p.browserFetcherTimeout),
	}

	attrs = append(attrs, contextDiagnosticAttrs(ctx, "parent")...)

	if err != nil {
		attrs = append(
			attrs,
			slog.String("reason", classifyFallbackReason(ctx, err, products)),
			slog.String("fallback_reason", classifyFallbackReason(ctx, err, products)),
			slog.String("go_parser_error", err.Error()),
		)
	} else {
		attrs = append(
			attrs,
			slog.String("reason", "invalid_or_empty_products"),
			slog.String("fallback_reason", "invalid_or_empty_products"),
		)
	}

	p.logger.LogAttrs(
		ctx,
		slog.LevelWarn,
		"ozon browser fallback started",
		attrs...,
	)
}

func (p *Parser) logBrowserFallbackSkipped(ctx context.Context, mode string, input string, limit int, endpointPath string, parserErr error, products []models.Product, parentErr error) {
	reason := classifyParentContextReason(parentErr)
	attrs := []slog.Attr{
		slog.String("mode", mode),
		slog.String("input", input),
		slog.Int("limit", limit),
		slog.String("endpoint", p.browserFallbackEndpoint(endpointPath)),
		slog.Int("products_count", len(products)),
		slog.Int("invalid_products_count", countInvalidOzonProducts(products)),
		slog.String("fallback_action", "skipped"),
		slog.String("fallback_reason", reason),
		slog.Duration("http_parser_timeout", p.httpParserTimeout),
		slog.Duration("browser_fallback_timeout", p.browserFetcherTimeout),
	}

	attrs = append(attrs, contextDiagnosticAttrs(ctx, "parent")...)

	if parserErr != nil {
		attrs = append(attrs, slog.String("go_parser_error", parserErr.Error()))
	}

	p.logger.LogAttrs(
		ctx,
		slog.LevelWarn,
		"ozon browser fallback skipped",
		attrs...,
	)
}

func (p *Parser) logBrowserFallbackFailure(ctx context.Context, mode string, input string, endpointPath string, err error) {
	attrs := []slog.Attr{
		slog.String("mode", mode),
		slog.String("input", input),
		slog.String("endpoint", p.browserFallbackEndpoint(endpointPath)),
		slog.String("error", err.Error()),
		slog.String("fallback_action", "failed"),
		slog.String("fallback_reason", classifyBrowserFallbackFailure(err)),
		slog.Duration("http_parser_timeout", p.httpParserTimeout),
		slog.Duration("browser_fallback_timeout", p.browserFetcherTimeout),
	}

	attrs = append(attrs, contextDiagnosticAttrs(ctx, "parent")...)

	p.logger.LogAttrs(
		ctx,
		slog.LevelError,
		"ozon browser fallback failed",
		attrs...,
	)
}

func contextDiagnosticAttrs(ctx context.Context, prefix string) []slog.Attr {
	attrs := []slog.Attr{}

	if err := ctx.Err(); err != nil {
		attrs = append(attrs, slog.String(prefix+"_context_error", err.Error()))
	}

	deadline, ok := ctx.Deadline()
	if !ok {
		return attrs
	}

	attrs = append(
		attrs,
		slog.Time(prefix+"_deadline", deadline),
		slog.Duration(prefix+"_time_remaining", time.Until(deadline)),
	)

	return attrs
}

func classifyParentContextReason(err error) string {
	switch {
	case errors.Is(err, context.Canceled):
		return "parent_context_canceled"
	case errors.Is(err, context.DeadlineExceeded):
		return "parent_deadline_exceeded"
	default:
		return "parent_context_done"
	}
}

func classifyFallbackReason(parentCtx context.Context, err error, products []models.Product) string {
	if err == nil {
		if !isValidOzonProductList(products) {
			return "invalid_or_empty_products"
		}

		return "unknown"
	}

	if parentCtx.Err() == nil && errors.Is(err, context.DeadlineExceeded) {
		return "http_parser_local_timeout"
	}

	if errors.Is(err, context.Canceled) {
		return "context_canceled"
	}

	var httpErr *ozonHTTPError
	if errors.As(err, &httpErr) {
		switch httpErr.StatusCode {
		case http.StatusForbidden:
			return "http_status_403"
		case http.StatusTooManyRequests:
			return "http_status_429"
		default:
			return fmt.Sprintf("http_status_%d", httpErr.StatusCode)
		}
	}

	errorText := strings.ToLower(err.Error())
	switch {
	case strings.Contains(errorText, "antibot"):
		return "antibot"
	case strings.Contains(errorText, "decode"):
		return "parse_error"
	case strings.Contains(errorText, "timeout"):
		return "network_timeout"
	default:
		return "go_parser_error"
	}
}

func classifyBrowserFallbackFailure(err error) string {
	switch {
	case errors.Is(err, context.Canceled):
		return "context_canceled"
	case errors.Is(err, context.DeadlineExceeded):
		return "browser_fallback_local_timeout"
	default:
		return "browser_fallback_error"
	}
}

func fallbackSkippedError(operation string, parserErr error, parentErr error) error {
	if parserErr != nil {
		return fmt.Errorf("%s with HTTP failed: %w; browser fallback skipped because parent context is done: %w", operation, parserErr, parentErr)
	}

	return fmt.Errorf("%s returned invalid or empty products; browser fallback skipped because parent context is done: %w", operation, parentErr)
}

func (p *Parser) browserFallbackEndpoint(endpointPath string) string {
	if p == nil || p.browserClient == nil || !p.browserClient.Enabled() {
		return ""
	}

	return p.browserClient.endpointFor(endpointPath)
}

func countInvalidOzonProducts(products []models.Product) int {
	invalidProducts := 0

	for _, product := range products {
		if !isValidOzonProduct(product) {
			invalidProducts++
		}
	}

	return invalidProducts
}

func (p *Parser) currentCookie() string {
	if p == nil {
		return ""
	}

	if p.cookieProvider != nil {
		return strings.TrimSpace(p.cookieProvider.GetCookie())
	}

	return strings.TrimSpace(p.cookie)
}

func (p *Parser) hasCookie() bool {
	return strings.TrimSpace(p.currentCookie()) != ""
}

func (p *Parser) cookieSource() string {
	if p == nil {
		return ""
	}

	if p.cookieProvider != nil {
		return p.cookieProvider.Source()
	}

	if strings.TrimSpace(p.cookie) != "" {
		return "env"
	}

	return "empty"
}

func newOzonHTTPTransport() http.RoundTripper {
	return &http.Transport{
		Proxy: http.ProxyFromEnvironment,

		MaxIdleConns:        100,
		MaxIdleConnsPerHost: 10,
		IdleConnTimeout:     90 * time.Second,

		TLSHandshakeTimeout:   10 * time.Second,
		ResponseHeaderTimeout: 20 * time.Second,
		ExpectContinueTimeout: 1 * time.Second,

		ForceAttemptHTTP2: true,
	}
}
