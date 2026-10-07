import os

from sqlalchemy import URL


def database_url(username, password):
    return URL.create(
        "postgresql+psycopg",
        username=username,
        password=password,
        host=os.getenv("MW_DB_HOST", "db"),
        port=int(os.getenv("MW_DB_PORT", "5432")),
        database=os.getenv("MW_DB_NAME", "mock_workday"),
    )


# Defaults are synthetic credentials for the disposable Compose database only.
APP_URL = database_url("mw_app", os.getenv("MW_DB_APP_PASSWORD", "mw-app-lab"))
OWNER_URL = database_url("mw_owner", os.getenv("MW_DB_OWNER_PASSWORD", "mw-owner-lab"))
ADMIN_URL = (
    database_url(os.environ["MW_DB_ADMIN_USER"], os.environ["MW_DB_ADMIN_PASSWORD"])
    if "MW_DB_ADMIN_PASSWORD" in os.environ
    else None
)
