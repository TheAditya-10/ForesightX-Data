from shared import BaseServiceSettings


class DataServiceSettings(BaseServiceSettings):
    service_name: str = "foresightx-data"
    port: int = 8001
    redis_url: str = "redis://redis:6379/0"
    cache_ttl_seconds: int = 60
    news_cache_ttl_seconds: int = 180
    history_cache_ttl_seconds: int = 300
    legacy_data_dir: str = "../ForesightX-pattern/data"
