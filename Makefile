.PHONY: build up down logs pull submodule start

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
