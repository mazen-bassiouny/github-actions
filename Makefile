image_name ?= te_tracktor_azure
repo_name ?= bidmaindevk8sreg573m
subscription ?= 266c6944-7320-41c7-81d7-b3e6ad81f3c5


init:
	pip3 install -r requirements.txt

init-update:
	pip3 install --upgrade -r requirements.txt

run-local:
	uwsgi --ini tracktor/uwsgi_local.ini

test:
	python3 -m pytest

test-extended:
	pytest --cov-report term-missing --cov=tracktor -vv

docker-image:
	export DOCKER_BUILDKIT=1; eval $$(ssh-agent); \
	ssh-add; docker build --ssh default -f Dockerfile -t $(image_name) .;

deploy-dev:
	make docker-image; \
	az acr login --name $(repo_name) --subscription $(subscription); \
	docker tag $(image_name):latest $(repo_name).azurecr.io/$(image_name):latest; \
	docker tag $(image_name):latest $(repo_name).azurecr.io/$(image_name):$$(git rev-parse --short HEAD); \
	docker push $(repo_name).azurecr.io/$(image_name):latest; \
	docker push $(repo_name).azurecr.io/$(image_name):$$(git rev-parse --short HEAD);

set-minikub-docker:
	eval $$(minikube docker-env | grep SET | sed -e 's/SET/export/' -e 's|\\|/|g' -e 's/C:/\/c/')
