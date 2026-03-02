import strawberry

from app.api.v1.catalog.schema import CatalogQuery, CatalogMutation
from app.api.v1.orders.schema import OrdersQuery, OrdersMutation


@strawberry.type
class Query(
    CatalogQuery,
    OrdersQuery,
):
    pass


@strawberry.type
class Mutation(
    CatalogMutation,
    OrdersMutation,
):
    pass


schema = strawberry.Schema(query=Query, mutation=Mutation)

