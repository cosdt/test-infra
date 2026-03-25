SHARED := config.py utils.py redis_helper.py secrets_manager_helper.py
PIP_FLAGS := --platform manylinux2014_x86_64 --only-binary=:all: --implementation cp --python-version 3.10
AWS_REGION ?= us-east-1
FUNCTION_NAME ?= cross_repo_ci_webhook

deployment.zip: clean
	mkdir -p ./deployment
	cp $(SHARED) ./deployment/
	cp lambda_function.py webhook_handler.py github_client_helper.py ./deployment/
	pip3 install --target ./deployment -r requirements.txt $(PIP_FLAGS)
	cd deployment && zip -r ../deployment.zip .

prepare: deployment.zip

deploy: deployment.zip
	aws lambda update-function-code --region $(AWS_REGION) --function-name $(FUNCTION_NAME) --zip-file fileb://deployment.zip

clean:
	rm -rf deployment deployment.zip

.PHONY: deployment.zip prepare deploy clean
