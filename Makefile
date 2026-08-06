.PHONY: \
	test \
	test-backend \
	test-wb \
	ps \
	health \
	go-parser-health \
	playwright-health \
	logs-backend \
	logs-fetcher \
	logs-scanner \
	logs-outbox \
	logs-notifications

test: test-backend test-wb

test-backend:
	cd backend && python manage.py test \
		app.api.v1.catalog.tests \
		app.api.v1.common.tests \
		app.api.v1.notifications.tests \
		app.api.v1.fetcher.tests \
		app.api.v1.monitoring.tests \
		app.api.v1.orders.tests \
		app.api.v1.payments.tests

test-wb:
	cd go_fetcher/internal && python -m unittest \
		wb_browser_fetcher.app.test_product_parsing

ps:
	docker compose ps

health:
	curl -X GET "http://127.0.0.1:8000/api/v1/system/health/"

go-parser-health:
	curl -X GET "http://127.0.0.1:8000/api/v1/system/parser-health/"

playwright-health:
	curl -X GET "http://127.0.0.1:8095/api/v1/health"

logs-backend:
	docker compose logs -f backend

logs-fetcher:
	docker compose logs -f go_fetcher

logs-scanner:
	docker compose logs -f monitoring_scanner

logs-outbox:
	docker compose logs -f outbox_worker

logs-notifications:
	docker compose logs -f notification_consumer