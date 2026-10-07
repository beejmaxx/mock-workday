.PHONY: test up down

test:
	uv run --frozen pytest

up:
	docker compose up --build -d

down:
	docker compose down --volumes --remove-orphans

.PHONY: aws-plan aws-up aws-smoke aws-down aws-leftovers
aws-plan aws-up aws-smoke aws-down aws-leftovers:
	bash infra/scripts/aws.sh $(patsubst aws-%,%,$@)
