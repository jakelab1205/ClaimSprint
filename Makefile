.PHONY: test test-parallel

test:
	docker-compose exec -e ANTHROPIC_API_KEY=test web \
		python manage.py test triage --verbosity 2

test-parallel:
	docker-compose exec -e ANTHROPIC_API_KEY=test web \
		python manage.py test triage --parallel 4 --verbosity 2
