all: deployment.zip

clean:
	rm -rf deployment
	rm -rf venv
	rm -f deployment.zip

venv/bin/python:
	virtualenv venv
	venv/bin/pip install -r requirements.txt

# Build a Lambda deployment package compatible with the repo's release workflow.
# It produces ./deployment.zip
deployment.zip:
	mkdir -p deployment
	cp *.py whitelist.yaml deployment/.
	pip3.10 install -r requirements.txt -t ./deployment/. --platform manylinux2014_x86_64 --only-binary=:all: --implementation cp --python-version 3.10 --upgrade
	python3.10 -c "import os, zipfile; zip_path=os.path.join(os.getcwd(),'deployment.zip'); root_dir=os.path.join(os.getcwd(),'deployment'); zf=zipfile.ZipFile(zip_path,'w',compression=zipfile.ZIP_DEFLATED); [zf.write(os.path.join(folder,name), os.path.relpath(os.path.join(folder,name), root_dir)) for folder,_,files in os.walk(root_dir) for name in files]; zf.close(); print('wrote', zip_path)"

.PHONY: all clean
