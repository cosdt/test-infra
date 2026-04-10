RESULT_DIR := result
WEBHOOK_DIR := webhook
RESULT_FUNCTION_NAME ?= cross_repo_ci_result
WEBHOOK_FUNCTION_NAME ?= cross_repo_ci_webhook
AWS_REGION ?= us-east-1

test:
	python3 -m pytest tests -v

deploy:
	@status=0; \
	$(MAKE) -C $(RESULT_DIR) deploy AWS_REGION=$(AWS_REGION) FUNCTION_NAME=$(RESULT_FUNCTION_NAME) || status=$$?; \
	$(MAKE) -C $(WEBHOOK_DIR) deploy AWS_REGION=$(AWS_REGION) FUNCTION_NAME=$(WEBHOOK_FUNCTION_NAME) || status=$$?; \
	exit $$status

clean:
	$(MAKE) -C $(RESULT_DIR) clean
	$(MAKE) -C $(WEBHOOK_DIR) clean
	rm -rf .pytest_cache __pycache__ result/__pycache__ webhook/__pycache__ utils/__pycache__ tests/__pycache__

.PHONY: test deploy clean