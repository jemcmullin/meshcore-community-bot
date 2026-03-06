.PHONY: build up down logs pull

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

# Update submodule to latest (intentional upgrade, not automatic)
pull: 
	git submodule update --remote --merge
	git add meshcore-bot
	git diff --cached --stat
	@echo "Review the diff above, then: git commit -m 'chore: update meshcore-bot submodule'"
