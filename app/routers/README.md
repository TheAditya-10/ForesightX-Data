# Data Routers

Routers define the public HTTP interface of the data service.

They should stay small and only:

- bind endpoints
- declare response models
- inject controllers and dependencies
