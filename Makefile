.PHONY: test up down

test:
	uv run --frozen pytest

up:
	docker compose up --build -d

down:
	docker compose down --volumes --remove-orphans
