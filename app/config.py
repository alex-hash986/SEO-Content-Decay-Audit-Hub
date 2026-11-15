from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "sqlite:///./data/seo_decay.db"
    app_title: str = "SEO & Content Decay Audit Hub"


settings = Settings()