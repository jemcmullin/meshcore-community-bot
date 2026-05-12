.PHONY: build up down logs pull submodule submodule-dev build-dev start restart restart-dev redeploy redeploy-dev

# Ensure submodule is initialized before any docker operation
submodule:
	git submodule update --init

build: submodule
	docker compose build

up: submodule
	docker compose up -d

down:
	docker compose down

logs:
	docker compose logs -f

start: up logs

pull:
	git pull

restart: build down start

redeploy: pull restart

submodule-dev:
	cd meshcore-bot && git fetch && git checkout dev && git pull --ff-only origin dev

build-dev: submodule-dev
	docker compose build

restart-dev: build-dev down start

redeploy-dev: pull restart-dev