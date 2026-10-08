.PHONY: setup up down logs eval test demo gmail-auth

setup:  ## create .env with random secrets (then add GEMINI_API_KEY and the Google OAuth values)
	@test -f .env || (cp .env.example .env && \
	  sed -i.bak "s|^AUTH_SECRET=.*|AUTH_SECRET=$$(openssl rand -base64 32)|; s|^APP_JWT_SECRET=.*|APP_JWT_SECRET=$$(openssl rand -base64 32)|" .env && \
	  rm -f .env.bak && echo "Created .env - now add GEMINI_API_KEY, GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET")
	@mkdir -p data/inbox secrets

up:
	docker compose up --build -d
	@echo "Review UI: http://localhost:3002   API: http://localhost:8002/docs"

down:
	docker compose down

logs:
	docker compose logs -f api

demo:  ## drop the three demo emails into the inbox folder
	@for f in 01-quote-request 02-angry-complaint 03-order-status; do cp data/samples/$$f.json data/inbox/$$f-$$(date +%s).json; done

eval:  ## classification + extraction accuracy on the 30-email labelled set -> evals/report.md
	docker compose run --rm --no-deps api python -m evals.run

test:
	docker compose run --rm --no-deps api python -m pytest -q

gmail-auth:  ## one-time Gmail OAuth consent on your machine (needs secrets/gmail_credentials.json)
	pip install google-auth-oauthlib pydantic-settings && python -m inboxpilot.sources.gmail --authorize
