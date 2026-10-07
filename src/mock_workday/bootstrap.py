from .config import APP_URL, OWNER_URL
from .db import Database, one
from .seed import seed


def main():
    db = Database(APP_URL, OWNER_URL)
    try:
        with db.owner.connect() as conn:
            installed = (
                one(conn, "SELECT to_regclass('public.tenants') AS name")["name"]
                is not None
            )
        if not installed:
            db.install()
            seed(db)
    finally:
        db.close()


if __name__ == "__main__":
    main()
