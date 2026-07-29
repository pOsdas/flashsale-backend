from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from drf_spectacular.utils import extend_schema, OpenApiResponse

from app.api.v1.fetcher.exceptions import (
    FetcherBatchAlreadyProcessedError,
    FetcherCurrencyNotSupportedError,
    FetcherError,
    FetcherImportInProgressError,
    FetcherStockUpdateError,
    FetcherUpsertError,
)
from app.api.v1.fetcher.permissions import (
    HasFetcherApiKey,
)
from app.api.v1.fetcher.serializers import (
    FetcherImportSerializer,
    FetchProductRequestSerializer,
    FetchProductResponseSerializer,
)
from app.api.v1.fetcher.services.fetch_product_service import FetchProductService
from app.api.v1.monitoring.services.fetcher_client import (
    MonitoringFetcherTemporarilyUnavailableError,
)
from app.api.v1.fetcher.services.fetcher_import_service import FetcherImportService
from app.core.logging import get_logger


logger = get_logger(__name__)


@extend_schema(
    tags=["Fetcher"],
    summary="Import products from external fetcher (Go -> Django backend)",
    description=(
        "Receives product data from go_fetcher service and performs upsert "
        "into catalog and stock. Supports idempotency via batch_id."
    ),
    request=FetcherImportSerializer,
    responses={
        200: OpenApiResponse(
            description="Import successful or already processed",
            response={
                "type": "object",
                "properties": {
                    "success": {"type": "boolean"},
                    "status": {"type": "string"},
                    "source": {"type": "string"},
                    "batch_id": {"type": "string"},
                    "created": {"type": "integer"},
                    "updated": {"type": "integer"},
                },
            },
        ),
        400: OpenApiResponse(description="Invalid payload or unsupported currency"),
        409: OpenApiResponse(description="Import already in progress"),
        500: OpenApiResponse(description="Internal server error"),
    },
)
@api_view(["POST"])
@permission_classes([HasFetcherApiKey])
def import_fetcher_items(request):
    serializer = FetcherImportSerializer(data=request.data)

    if not serializer.is_valid():
        return Response(
            {
                "success": False,
                "error": "Invalid import payload.",
                "details": serializer.errors,
            },
            status=status.HTTP_400_BAD_REQUEST
        )

    source = serializer.validated_data["source"]
    batch_id = serializer.validated_data["batch_id"]
    items = serializer.validated_data["items"]

    logger.info(
        "Fetcher import request received",
        extra={
            "source": source,
            "batch_id": batch_id,
            "items_count": len(items),
        },
    )

    service = FetcherImportService(
        source=source,
        batch_id=batch_id,
        items=items,
    )
    try:
        result = service.execute()
    except FetcherBatchAlreadyProcessedError:
        logger.info(
            "Fetcher import batch already processed",
            extra={
                "source": source,
                "batch_id": batch_id,
            },
        )

        return Response(
            {
                "success": True,
                "status": "already_processed",
                "source": source,
                "batch_id": batch_id,
                "created": 0,
                "updated": 0,
            },
            status=status.HTTP_200_OK,
        )

    except FetcherImportInProgressError as e:
        logger.warning(
            "Fetcher import already in progress",
            extra={
                "source": source,
                "batch_id": batch_id,
            },
        )

        return Response(
            {
                "success": False,
                "error": str(e),
            },
            status=status.HTTP_409_CONFLICT,
        )

    except FetcherCurrencyNotSupportedError as e:
        logger.warning(
            "Fetcher import rejected because of unsupported currency",
            extra={
                "source": source,
                "batch_id": batch_id,
                "error": str(e),
            },
        )

        return Response(
            {
                "success": False,
                "error": str(e),
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    except (FetcherUpsertError, FetcherStockUpdateError) as e:
        logger.error(
            "Fetcher import failed during catalog update",
            extra={
                "source": source,
                "batch_id": batch_id,
                "error": str(e),
            },
            exc_info=True,
        )

        return Response(
            {
                "success": False,
                "error": "Catalog import failed.",
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    except FetcherError as e:
        logger.error(
            "Fetcher import failed",
            extra={
                "source": source,
                "batch_id": batch_id,
                "error": str(e),
            },
            exc_info=True,
        )

        return Response(
            {
                "success": False,
                "error": "Fetcher import failed."
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    logger.info(
        "Fetcher import completed",
        extra={
            "source": source,
            "batch_id": batch_id,
            "created_count": result["created"],
            "updated_count": result["updated"],
        },
    )

    return Response(
        {
            "success": True,
            "status": "imported",
            "source": source,
            "batch_id": batch_id,
            "created": result["created"],
            "updated": result["updated"],
        },
        status=status.HTTP_200_OK,
    )


@extend_schema(
    tags=["Fetcher"],
    summary="Fetch product through go_fetcher and save monitoring snapshot (Django backend -> Go)",
    description=(
        "Sends marketplace, url, role and check_interval_minutes to go_fetcher service and receives product snapshot. "
        "Add snapshot in db."
    ),
    request=FetchProductRequestSerializer,
    responses={
        200: FetchProductResponseSerializer,
        400: OpenApiResponse(description="Invalid payload"),
        500: OpenApiResponse(description="Fetch product failed"),
        503: OpenApiResponse(description="Marketplace parser is temporarily unavailable"),
    },
)
@api_view(["POST"])
@permission_classes([IsAuthenticated])
def fetch_product(request):
    serializer = FetchProductRequestSerializer(data=request.data)

    if not serializer.is_valid():
        return Response(
            {
                "success": False,
                "error": "Invalid fetch product payload.",
                "details": serializer.errors,
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    service = FetchProductService(
        user=request.user,
        marketplace=serializer.validated_data["marketplace"],
        url=serializer.validated_data["url"],
        role=serializer.validated_data["role"],
        check_interval_minutes=serializer.validated_data["check_interval_minutes"],
    )

    try:
        result = service.execute()
    except MonitoringFetcherTemporarilyUnavailableError as e:
        logger.warning(
            "Fetch product postponed because marketplace parser is temporarily unavailable",
            extra={
                "marketplace": serializer.validated_data["marketplace"],
                "url": serializer.validated_data["url"],
                "error": str(e),
                "retry_after_seconds": e.retry_after_seconds,
            },
        )

        response = Response(
            {
                "success": False,
                "error": "Marketplace parser is temporarily unavailable.",
                "retry_after_seconds": e.retry_after_seconds,
            },
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
        response["Retry-After"] = str(e.retry_after_seconds)
        return response

    except Exception as e:
        logger.error(
            "Fetch product failed",
            extra={
                "marketplace": serializer.validated_data["marketplace"],
                "url": serializer.validated_data["url"],
                "error": str(e),
            },
            exc_info=True,
        )

        return Response(
            {
                "success": False,
                "error": "Fetch product failed.",
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    return Response(
        {
            "success": True,
            **result,
        },
        status=status.HTTP_200_OK,
    )
