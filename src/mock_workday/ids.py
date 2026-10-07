from uuid import UUID, uuid5

MW_NAMESPACE = UUID("b2dbf256-9eed-4f28-874b-afbd58c9e3d2")


def seed_id(tenant: str, object_type: str, ref: str) -> UUID:
    return uuid5(MW_NAMESPACE, f"{tenant}:{object_type}:{ref}")
