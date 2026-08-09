# Linux/macOS entry point. All logic lives in tasks.py -- this only forwards, so
# there is nothing here that can drift away from it.
#
#   make up
#   make logs ARGS=ignition
#
# This file used to be a hand-written mirror of tasks.ps1 and had quietly lost
# the module version check, the COMMISSIONING detection and the Chariot login
# wait. That is why it forwards now instead of reimplementing.

PY   ?= python3
ARGS ?=

TASKS := help init verify-modules hash-modules seed up down restart ps logs nuke \
         scan export-config trial reset-trial chariot-trial health

.DEFAULT_GOAL := help
.PHONY: $(TASKS)

$(TASKS):
	@$(PY) tasks.py $@ $(ARGS)
