SHARED := config.py utils.py redis_helper.py allowlist.py gh_helper.py pr_handler.py
PIP_FLAGS := --platform manylinux2014_x86_64 --only-binary=:all: --implementation cp --python-version 3.10

deployment.zip: clean
	mkdir -p ./deployment
	cp $(SHARED) lambda_function.py ./deployment/
	pip3 install --target ./deployment -r requirements.txt $(PIP_FLAGS)
	cd deployment && zip -r ../deployment.zip .

deploy: deployment.zip
	aws lambda update-function-code --region $(AWS_REGION) --function-name $(FUNCTION_NAME) --zip-file fileb://deployment.zip

clean:
	rm -rf deployment deployment.zip

.PHONY: prepare deploy clean
