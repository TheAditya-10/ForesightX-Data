# Data App Package

This package contains the runtime code for the data microservice.

Request path:

1. router receives validated request
2. controller maps service errors to HTTP responses
3. service fetches market data and cache entries
4. schemas guarantee structured output
