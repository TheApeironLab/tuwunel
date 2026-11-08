# Local build entrypoints. Native deployment/release interfaces remain supported.
.DEFAULT_GOAL := help
CHANNEL ?= development
ARCH ?= amd64
VERSION ?=
OUTPUT ?=
BASE_PACKAGE ?=
TEST_SCOPE ?= all
export CHANNEL ARCH VERSION OUTPUT BASE_PACKAGE TEST_SCOPE
export MATERIALS VASI_SOURCE FULL_PACKAGE PACKAGE
export PYTHONDONTWRITEBYTECODE := 1
.PHONY: help check test images chart package package-slim verify materials
help check test images chart package package-slim verify materials:
	@python3 -B release/build-entry.py $@
