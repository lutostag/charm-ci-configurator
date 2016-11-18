PWD := $(shell pwd)
SOURCEDEPS_DIR ?= $(shell dirname $(PWD))/.sourcecode
HOOKS_DIR := $(PWD)/hooks
CHARM_DIR := $(PWD)
FILES_DIR := $(PWD)/files
CONFIGS_DIR := $(PWD)/ci-config-repo
PYTHON := /usr/bin/env python
GIT := /usr/bin/git
TAR := /bin/tar
CP := /bin/cp
RM := /bin/rm
UNZIP := /usr/bin/unzip
CURL := /usr/bin/curl
CAT := /bin/cat
SED := /bin/sed
JBB_GIT := "https://github.com/openstack-infra/jenkins-job-builder.git"

build: configrepo installdeps proof

revision:
	@test -f revision || echo 0 > revision

proof: revision
	@echo Proofing charm...
	@(charm proof $(PWD) || [ $$? -eq 100 ]) && echo OK
	@test `cat revision` = 0 && rm revision


define update_with_branch
	@echo Updating source dependencies from branch...
	@mkdir -p $(SOURCEDEPS_DIR)
	@$(RM) -rf $(SOURCEDEPS_DIR)/ci-configurator/*
	@bzr branch $(CI_CONFIGURATOR_SOURCEDEPS_BRANCH) $(SOURCEDEPS_DIR)/ci-configurator

	@$(CP) -R $(SOURCEDEPS_DIR)/ci-configurator/jenkins-job-builder/jenkins-job-builder_reqs $(FILES_DIR)/jenkins-job-builder_reqs
	@$(CP) $(SOURCEDEPS_DIR)/ci-configurator/jenkins-job-builder/jenkins-job-builder.tar.gz $(FILES_DIR)/
	@$(CP) $(SOURCEDEPS_DIR)/ci-configurator/jenkins-job-builder/requirements.txt $(FILES_DIR)/requirements.txt
endef

define update_without_branch
	@mkdir -p $(SOURCEDEPS_DIR)/jenkins-job-builder_reqs
	@echo Updating source dependencies...
	@$(GIT) clone $(JBB_GIT) $(SOURCEDEPS_DIR)/jenkins-job-builder
	@cd $(SOURCEDEPS_DIR) && $(CP) $(SOURCEDEPS_DIR)/jenkins-job-builder/requirements.txt $(FILES_DIR)/
	@cd $(SOURCEDEPS_DIR) && $(TAR) cfz $(FILES_DIR)/jenkins-job-builder.tar.gz jenkins-job-builder/
	@pip install --download $(SOURCEDEPS_DIR)/jenkins-job-builder_reqs/ -r $(FILES_DIR)/requirements.txt && $(CP) -R $(SOURCEDEPS_DIR)/jenkins-job-builder_reqs $(FILES_DIR)/jenkins-job-builder_reqs
endef

installdeps: clean
	@mkdir -p $(SOURCEDEPS_DIR) $(FILES_DIR)
ifndef CI_CONFIGURATOR_SOURCEDEPS_BRANCH
	$(call update_without_branch)
else
ifeq ($(CI_CONFIGURATOR_SOURCEDEPS_BRANCH), '')
	$(call_update_without_branch)
else
	$(call update_with_branch)
endif
endif
	

configrepo:
	@$(RM) -rf $(CONFIGS_DIR)
	@bzr branch $(CONFIG_BZR_REPO) $(CONFIGS_DIR)

clean:
	@$(RM) -rf $(FILES_DIR)/*
	@$(RM) -rf $(SOURCEDEPS_DIR)/*

lint:
	@flake8 --exclude hooks/charmhelpers,hooks/lib/ hooks tests

sync:
	@charm-helper-sync -c charm-helpers.yaml

test:
	@(export PYTHONPATH=hooks; nosetests -v tests)

.PHONY: revision proof installdeps
