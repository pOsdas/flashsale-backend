package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"log"
	"log/slog"
	"net/url"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/joho/godotenv"

	"go_fetcher/internal/config"
	"go_fetcher/internal/cookies"
	"go_fetcher/internal/httpserver"
	"go_fetcher/internal/models"
	"go_fetcher/internal/parsers/ozon"
	"go_fetcher/internal/parsers/wildberries"
	"go_fetcher/internal/pipeline"
	"go_fetcher/internal/sender"
)

const (
	sourceWildberries = "wildberries"
	sourceOzon        = "ozon"

	cliSourceWB   = "wb"
	cliSourceOzon = "ozon"

	commandProduct    = "product"
	commandSearch     = "search"
	commandCategory   = "category"
	commandCategories = "categories"

	commandServe = "serve"

	defaultLimit      = 100
	defaultHTTPServer = "0.0.0.0:8090"
)

func main() {
	logger := newLogger()

	if err := godotenv.Load(); err != nil {
		logger.Warn("no .env file found")
	}

	cfg, err := config.Load()
	if err != nil {
		logger.Error("failed to load config", slog.String("error", err.Error()))
		os.Exit(1)
	}

	if len(os.Args) >= 2 && os.Args[1] == commandServe {
		runHTTPServer(logger, cfg)
		return
	}

	runCLI(logger, cfg)
}

func runCLI(
	logger *slog.Logger,
	cfg *config.Config,
) {
	if len(os.Args) < 3 {
		printUsageAndExit()
	}

	source := os.Args[1]
	command := os.Args[2]

	ctx, cancel := context.WithTimeout(context.Background(), cfg.Timeout)
	defer cancel()

	djangoSender := sender.NewDjangoSender(
		cfg.DjangoURL,
		cfg.FetcherAPIKey,
	)

	importPipeline := pipeline.NewImportPipeline(
		djangoSender,
	)

	startedAt := time.Now()

	logger.Info(
		"fetcher command started",
		slog.String("source", source),
		slog.String("command", command),
	)

	switch source {
	case cliSourceWB:
		runWBCommand(ctx, logger, cfg, command, importPipeline)

	case cliSourceOzon:
		runOzonCommand(ctx, logger, cfg, command, importPipeline)

	default:
		logger.Error("unsupported source", slog.String("source", source))
		printUsageAndExit()
	}

	logger.Info(
		"fetcher command finished",
		slog.String("source", source),
		slog.String("command", command),
		slog.Duration("duration", time.Since(startedAt)),
	)
}

func runHTTPServer(
	logger *slog.Logger,
	cfg *config.Config,
) {
	ctx, stop := signal.NotifyContext(
		context.Background(),
		os.Interrupt,
		syscall.SIGTERM,
	)
	defer stop()

	wbCookieProvider := cookies.NewFileCookieProvider(
		"secrets/wb_cookie.txt",
		30*time.Second,
	)
	ozonCookieProvider := cookies.NewFileCookieProvider(
		"secrets/ozon_cookie.txt",
		30*time.Second,
	)

	wbParser := wildberries.NewParser(
		wildberries.ParserConfig{
			CookieProvider:        wbCookieProvider,
			Timeout:               cfg.Timeout,
			RequestDelay:          cfg.WBRequestDelay,
			MaxRetries:            cfg.WBMaxRetries,
			RetryBaseDelay:        cfg.WBRetryBaseDelay,
			BrowserFetcherURL:     cfg.WBBrowserFetcherURL,
			BrowserFetcherEnabled: cfg.WBBrowserFetcherEnabled,
			BrowserFetcherTimeout: time.Duration(cfg.WBBrowserFetcherTimeoutSeconds) * time.Second,
		},
		logger,
	)

	ozonParser := ozon.NewParser(
		ozon.ParserConfig{
			CookieProvider:        ozonCookieProvider,
			Timeout:               cfg.Timeout,
			HTTPParserTimeout:     time.Duration(cfg.OzonHTTPParserTimeoutSeconds) * time.Second,
			RequestDelay:          cfg.OzonRequestDelay,
			MaxRetries:            cfg.OzonMaxRetries,
			RetryBaseDelay:        cfg.OzonRetryBaseDelay,
			BrowserFetcherURL:     cfg.OzonBrowserFetcherURL,
			BrowserFetcherEnabled: cfg.OzonBrowserFetcherEnabled,
			BrowserFetcherTimeout: time.Duration(cfg.OzonBrowserFetcherTimeoutSeconds) * time.Second,
		},
		logger,
	)

	server := httpserver.NewServer(
		defaultHTTPServer,
		cfg.FetcherAPIKey,
		logger,
		func(ctx context.Context, req httpserver.FetchProductRequest) (*httpserver.ProductDTO, error) {
			productInput, err := buildWBProductInput(req.URL)
			if err != nil {
				return nil, err
			}

			products, err := wbParser.ParseProduct(ctx, productInput)
			if err != nil {
				return nil, err
			}

			if len(products) == 0 {
				return nil, errors.New("wb product was not found")
			}

			return productToDTO(products[0]), nil
		},
		func(ctx context.Context, req httpserver.FetchProductRequest) (*httpserver.ProductDTO, error) {
			productInput := strings.TrimSpace(req.URL)

			products, err := ozonParser.ParseProduct(ctx, productInput)
			if err != nil {
				return nil, err
			}

			if len(products) == 0 {
				return nil, errors.New("ozon product was not found")
			}

			return productToDTO(products[0]), nil
		},
		wbParser,
		ozonParser,
	)

	if err := server.Run(ctx); err != nil {
		logger.Error("http server failed", slog.String("error", err.Error()))
		os.Exit(1)
	}
}

// --- WB ---

func runWBCommand(
	ctx context.Context,
	logger *slog.Logger,
	cfg *config.Config,
	command string,
	importPipeline *pipeline.ImportPipeline,
) {
	wbCookieProvider := cookies.NewFileCookieProvider(
		"secrets/wb_cookie.txt",
		30*time.Second,
	)

	wbParser := wildberries.NewParser(
		wildberries.ParserConfig{
			CookieProvider:        wbCookieProvider,
			Timeout:               cfg.Timeout,
			RequestDelay:          cfg.WBRequestDelay,
			MaxRetries:            cfg.WBMaxRetries,
			RetryBaseDelay:        cfg.WBRetryBaseDelay,
			BrowserFetcherURL:     cfg.WBBrowserFetcherURL,
			BrowserFetcherEnabled: cfg.WBBrowserFetcherEnabled,
			BrowserFetcherTimeout: time.Duration(cfg.WBBrowserFetcherTimeoutSeconds) * time.Second,
		},
		logger,
	)

	switch command {
	case commandProduct:
		runWBProductCommand(ctx, logger, wbParser, importPipeline)

	case commandSearch:
		runWBSearchCommand(ctx, logger, wbParser, importPipeline)

	case commandCategory:
		runWBCategoryCommand(ctx, logger, wbParser, importPipeline)

	default:
		logger.Error("unsupported wb command", slog.String("command", command))
		printUsageAndExit()
	}
}

func runWBProductCommand(
	ctx context.Context,
	logger *slog.Logger,
	wbParser *wildberries.Parser,
	importPipeline *pipeline.ImportPipeline,
) {
	productFlags := flag.NewFlagSet("wb product", flag.ExitOnError)

	if err := productFlags.Parse(os.Args[3:]); err != nil {
		logger.Error("failed to parse wb product flags", slog.String("error", err.Error()))
		os.Exit(1)
	}

	if productFlags.NArg() != 1 {
		logger.Error("invalid wb product command arguments")
		log.Fatal("usage: go run ./cmd/fetcher wb product <nmID>")
	}

	nmID := productFlags.Arg(0)

	logger.Info(
		"wb product command parsed",
		slog.String("nm_id", nmID),
	)

	products, err := wbParser.ParseProduct(ctx, nmID)
	if err != nil {
		logger.Error("failed to parse WB product", slog.String("error", err.Error()))
		os.Exit(1)
	}

	response, err := importPipeline.ImportProducts(
		ctx,
		sourceWildberries,
		products,
	)
	if err != nil {
		logger.Error("failed to import WB product", slog.String("error", err.Error()))
		os.Exit(1)
	}

	printImportResponse(response)
}

func runWBSearchCommand(
	ctx context.Context,
	logger *slog.Logger,
	wbParser *wildberries.Parser,
	importPipeline *pipeline.ImportPipeline,
) {
	searchFlags := flag.NewFlagSet("wb search", flag.ExitOnError)

	limit := searchFlags.Int("limit", defaultLimit, "maximum number of products to import")

	if err := searchFlags.Parse(os.Args[3:]); err != nil {
		logger.Error("failed to parse wb search flags", slog.String("error", err.Error()))
		os.Exit(1)
	}

	if searchFlags.NArg() != 1 {
		logger.Error("invalid wb search command arguments")
		log.Fatal(`usage: go run ./cmd/fetcher wb search --limit=100 "iphone"`)
	}

	query := searchFlags.Arg(0)

	logger.Info(
		"wb search command parsed",
		slog.String("query", query),
		slog.Int("limit", *limit),
	)

	products, err := wbParser.SearchProducts(ctx, query, *limit)
	if err != nil {
		logger.Error("failed to parse WB search products", slog.String("error", err.Error()))
		os.Exit(1)
	}

	response, err := importPipeline.ImportProducts(
		ctx,
		sourceWildberries,
		products,
	)
	if err != nil {
		logger.Error("failed to import WB search products", slog.String("error", err.Error()))
		os.Exit(1)
	}

	printImportResponse(response)
}

func runWBCategoryCommand(
	ctx context.Context,
	logger *slog.Logger,
	wbParser *wildberries.Parser,
	importPipeline *pipeline.ImportPipeline,
) {
	categoryFlags := flag.NewFlagSet("wb category", flag.ExitOnError)

	limit := categoryFlags.Int("limit", defaultLimit, "maximum number of products to import")

	if err := categoryFlags.Parse(os.Args[3:]); err != nil {
		logger.Error("failed to parse wb category flags", slog.String("error", err.Error()))
		os.Exit(1)
	}

	if categoryFlags.NArg() != 1 {
		logger.Error("invalid wb category command arguments")
		log.Fatal(`usage: go run ./cmd/fetcher wb category --limit=100 "кошельки и кредитницы"`)
	}

	categoryName := categoryFlags.Arg(0)

	logger.Info(
		"wb category command parsed",
		slog.String("category", categoryName),
		slog.Int("limit", *limit),
	)

	products, err := wbParser.CategoryProducts(ctx, categoryName, *limit)
	if err != nil {
		logger.Error("failed to parse WB category products", slog.String("error", err.Error()))
		os.Exit(1)
	}

	response, err := importPipeline.ImportProducts(
		ctx,
		sourceWildberries,
		products,
	)
	if err != nil {
		logger.Error("failed to import WB category products", slog.String("error", err.Error()))
		os.Exit(1)
	}

	printImportResponse(response)
}

// --- Ozon ---

func runOzonCommand(
	ctx context.Context,
	logger *slog.Logger,
	cfg *config.Config,
	command string,
	importPipeline *pipeline.ImportPipeline,
) {
	ozonCookieProvider := cookies.NewFileCookieProvider(
		"secrets/ozon_cookie.txt",
		30*time.Second,
	)

	ozonParser := ozon.NewParser(
		ozon.ParserConfig{
			CookieProvider:        ozonCookieProvider,
			Timeout:               cfg.Timeout,
			HTTPParserTimeout:     time.Duration(cfg.OzonHTTPParserTimeoutSeconds) * time.Second,
			RequestDelay:          cfg.OzonRequestDelay,
			MaxRetries:            cfg.OzonMaxRetries,
			RetryBaseDelay:        cfg.OzonRetryBaseDelay,
			BrowserFetcherURL:     cfg.OzonBrowserFetcherURL,
			BrowserFetcherEnabled: cfg.OzonBrowserFetcherEnabled,
			BrowserFetcherTimeout: time.Duration(cfg.OzonBrowserFetcherTimeoutSeconds) * time.Second,
		},
		logger,
	)

	switch command {
	case commandProduct:
		runOzonProductCommand(ctx, logger, ozonParser, importPipeline)

	case commandSearch:
		runOzonSearchCommand(ctx, logger, ozonParser, importPipeline)

	case commandCategory:
		runOzonCategoryCommand(ctx, logger, ozonParser, importPipeline)

	case commandCategories:
		runOzonCategoriesCommand(ctx, logger, ozonParser)

	default:
		logger.Error("unsupported ozon command", slog.String("command", command))
		printUsageAndExit()
	}
}

func runOzonProductCommand(
	ctx context.Context,
	logger *slog.Logger,
	ozonParser *ozon.Parser,
	importPipeline *pipeline.ImportPipeline,
) {
	productFlags := flag.NewFlagSet("ozon product", flag.ExitOnError)

	if err := productFlags.Parse(os.Args[3:]); err != nil {
		logger.Error("failed to parse ozon product flags", slog.String("error", err.Error()))
		os.Exit(1)
	}

	if productFlags.NArg() != 1 {
		logger.Error("invalid ozon product command arguments")
		log.Fatal(`usage: go run ./cmd/fetcher ozon product "/product/sirop-topping-bez-sahara-nizkokaloriynyy-mr-djemius-zero-solenaya-karamel-330g-1919933573/"`)
	}

	productInput := productFlags.Arg(0)

	logger.Info(
		"ozon product command parsed",
		slog.String("product_input", productInput),
	)

	products, err := ozonParser.ParseProduct(ctx, productInput)
	if err != nil {
		logger.Error("failed to parse Ozon product", slog.String("error", err.Error()))
		os.Exit(1)
	}

	response, err := importPipeline.ImportProducts(
		ctx,
		sourceOzon,
		products,
	)
	if err != nil {
		logger.Error("failed to import Ozon product", slog.String("error", err.Error()))
		os.Exit(1)
	}

	printImportResponse(response)
}

func runOzonSearchCommand(
	ctx context.Context,
	logger *slog.Logger,
	ozonParser *ozon.Parser,
	importPipeline *pipeline.ImportPipeline,
) {
	searchFlags := flag.NewFlagSet("ozon search", flag.ExitOnError)

	limit := searchFlags.Int("limit", defaultLimit, "maximum number of products to import")

	if err := searchFlags.Parse(os.Args[3:]); err != nil {
		logger.Error("failed to parse ozon search flags", slog.String("error", err.Error()))
		os.Exit(1)
	}

	if searchFlags.NArg() != 1 {
		logger.Error("invalid ozon search command arguments")
		log.Fatal(`usage: go run ./cmd/fetcher ozon search --limit=100 "iphone"`)
	}

	query := searchFlags.Arg(0)

	logger.Info(
		"ozon search command parsed",
		slog.String("query", query),
		slog.Int("limit", *limit),
	)

	products, err := ozonParser.SearchProducts(ctx, query, *limit)
	if err != nil {
		logger.Error("failed to parse Ozon search products", slog.String("error", err.Error()))
		os.Exit(1)
	}

	response, err := importPipeline.ImportProducts(
		ctx,
		sourceOzon,
		products,
	)
	if err != nil {
		logger.Error("failed to import Ozon search products", slog.String("error", err.Error()))
		os.Exit(1)
	}

	printImportResponse(response)
}

func runOzonCategoryCommand(
	ctx context.Context,
	logger *slog.Logger,
	ozonParser *ozon.Parser,
	importPipeline *pipeline.ImportPipeline,
) {
	categoryFlags := flag.NewFlagSet("ozon category", flag.ExitOnError)

	limit := categoryFlags.Int("limit", defaultLimit, "maximum number of products to import")

	if err := categoryFlags.Parse(os.Args[3:]); err != nil {
		logger.Error("failed to parse ozon category flags", slog.String("error", err.Error()))
		os.Exit(1)
	}

	if categoryFlags.NArg() != 1 {
		logger.Error("invalid ozon category command arguments")
		log.Fatal(`usage: go run ./cmd/fetcher ozon category --limit=100 "/category/svitery-dzhempery-i-kardigany-muzhskie-7554/"`)
	}

	categoryInput := categoryFlags.Arg(0)

	logger.Info(
		"ozon category command parsed",
		slog.String("category_input", categoryInput),
		slog.Int("limit", *limit),
	)

	products, err := ozonParser.CategoryProducts(ctx, categoryInput, *limit)
	if err != nil {
		logger.Error("failed to parse Ozon category products", slog.String("error", err.Error()))
		os.Exit(1)
	}

	response, err := importPipeline.ImportProducts(
		ctx,
		sourceOzon,
		products,
	)
	if err != nil {
		logger.Error("failed to import Ozon category products", slog.String("error", err.Error()))
		os.Exit(1)
	}

	printImportResponse(response)
}

func runOzonCategoriesCommand(
	ctx context.Context,
	logger *slog.Logger,
	ozonParser *ozon.Parser,
) {
	categoriesFlags := flag.NewFlagSet("ozon categories", flag.ExitOnError)

	limit := categoriesFlags.Int("limit", 10, "maximum number of category candidates to show")

	if err := categoriesFlags.Parse(os.Args[3:]); err != nil {
		logger.Error("failed to parse ozon categories flags", slog.String("error", err.Error()))
		os.Exit(1)
	}

	if categoriesFlags.NArg() != 1 {
		logger.Error("invalid ozon categories command arguments")
		log.Fatal(`usage: go run ./cmd/fetcher ozon categories --limit=10 "мужские свитеры"`)
	}

	query := categoriesFlags.Arg(0)

	logger.Info(
		"ozon categories command parsed",
		slog.String("query", query),
		slog.Int("limit", *limit),
	)

	categories, err := ozonParser.ResolveCategories(ctx, query, *limit)
	if err != nil {
		logger.Error("failed to resolve Ozon categories", slog.String("error", err.Error()))
		os.Exit(1)
	}

	printOzonCategories(categories)
}

// --- HTTP helpers ---

func buildWBProductInput(rawProductURL string) (string, error) {
	rawProductURL = strings.TrimSpace(rawProductURL)

	if rawProductURL == "" {
		return "", errors.New("wb product url is required")
	}

	if _, err := strconv.ParseInt(rawProductURL, 10, 64); err == nil {
		return rawProductURL, nil
	}

	parsedURL, err := url.Parse(rawProductURL)
	if err != nil {
		return "", errors.New("invalid wb product url")
	}

	pathParts := strings.Split(parsedURL.Path, "/")

	for index, part := range pathParts {
		if part == "catalog" && index+1 < len(pathParts) {
			nmID := strings.TrimSpace(pathParts[index+1])

			if nmID == "" {
				return "", errors.New("wb nmID was not found in url")
			}

			if _, err := strconv.ParseInt(nmID, 10, 64); err != nil {
				return "", errors.New("invalid wb nmID in url")
			}

			return nmID, nil
		}
	}

	return "", errors.New("wb nmID was not found in url")
}

func productToDTO(product models.Product) *httpserver.ProductDTO {
	return &httpserver.ProductDTO{
		ExternalID:    product.SKU,
		Title:         product.Title,
		SellerName:    product.SellerName,
		Brand:         product.Brand,
		PriceCents:    product.PriceCents,
		OldPriceCents: product.OldPriceCents,
		Currency:      product.Currency,
		IsAvailable:   product.Available > 0,
		Rating:        product.Rating,
		ReviewsCount:  product.ReviewsCount,
		ProductPath:   product.ProductPath,
		URL:           product.URL,
	}
}

// --- Common ---

func printOzonCategories(categories []ozon.OzonCategoryCandidate) {
	fmt.Println("Found Ozon categories:")
	fmt.Println("")

	for index, category := range categories {
		fmt.Printf("%d. %s\n", index+1, category.Title)
		fmt.Printf("   %s\n", category.URL)
		fmt.Println("")
	}
}

func printImportResponse(response pipeline.ImportResponse) {
	fmt.Printf("Django import success: %t\n", response.Success)
	fmt.Printf("Django import status: %s\n", response.Status)
	fmt.Printf("Created: %d\n", response.Created)
	fmt.Printf("Updated: %d\n", response.Updated)
}

func printUsageAndExit() {
	fmt.Println("usage:")

	fmt.Println("")
	fmt.Println("HTTP server:")
	fmt.Println("  go run ./cmd/fetcher serve")

	fmt.Println("")
	fmt.Println("Wildberries:")
	fmt.Println("  go run ./cmd/fetcher wb product <nmID>")
	fmt.Println(`  go run ./cmd/fetcher wb search --limit=100 "iphone"`)
	fmt.Println(`  go run ./cmd/fetcher wb category --limit=100 "кошельки и кредитницы"`)

	fmt.Println("")
	fmt.Println("Ozon:")
	fmt.Println(`  go run ./cmd/fetcher ozon product "/product/sirop-topping-bez-sahara-nizkokaloriynyy-mr-djemius-zero-solenaya-karamel-330g-1919933573/"`)
	fmt.Println(`  go run ./cmd/fetcher ozon search --limit=100 "iphone"`)
	fmt.Println(`  go run ./cmd/fetcher ozon categories --limit=10 "мужские свитеры"`)
	fmt.Println(`  go run ./cmd/fetcher ozon category --limit=100 "/category/svitery-dzhempery-i-kardigany-muzhskie-7554/"`)
	fmt.Println(`  go run ./cmd/fetcher ozon category --limit=100 "https://www.ozon.ru/category/svitery-dzhempery-i-kardigany-muzhskie-7554/"`)

	os.Exit(1)
}

func newLogger() *slog.Logger {
	handler := slog.NewTextHandler(os.Stdout, &slog.HandlerOptions{
		Level: slog.LevelInfo,
	})

	logger := slog.New(handler)
	slog.SetDefault(logger)

	return logger
}
