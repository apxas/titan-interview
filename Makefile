run:
	docker compose up --build

migrate:
	docker compose exec app alembic upgrade head

worker:
	docker compose exec worker celery -A app.workers.tasks worker --loglevel=info

test:
	docker compose exec app pytest tests/ -v

seed:
	docker compose exec app python seed.py
