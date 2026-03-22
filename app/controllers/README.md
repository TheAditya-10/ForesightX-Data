# Data Controllers

Controllers are thin adapters between FastAPI routes and domain services.

They exist so HTTP concerns stay separate from market-data logic:

- convert service exceptions into HTTP exceptions
- keep routers declarative
- avoid pushing transport behavior into services
