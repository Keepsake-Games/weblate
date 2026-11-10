GIT_USR_BIN := C:/PROGRA~1/Git/usr/bin
SHELL := $(GIT_USR_BIN)/sh.exe
export PATH := $(GIT_USR_BIN);$(PATH)

REGISTRY := europe-west4-docker.pkg.dev
IMAGE := $(REGISTRY)/jump-ship-3c7d/docker/weblate
CTX := .build
TMP_INDEX := .git/image-index

.PHONY: build push src clean

build: src
	podman build -f Dockerfile -t $(IMAGE) $(CTX)

src:
	rm -rf $(CTX) $(TMP_INDEX)
	mkdir -p $(CTX)
	GIT_INDEX_FILE=$(TMP_INDEX) git read-tree HEAD
	GIT_INDEX_FILE=$(TMP_INDEX) git add -A
	git archive --format=tar -o $(CTX)/src.tar $$(GIT_INDEX_FILE=$(TMP_INDEX) git write-tree)
	rm -f $(TMP_INDEX)

push:
	gcloud auth print-access-token | podman login -u oauth2accesstoken --password-stdin $(REGISTRY)
	podman push --digestfile $(CTX)/digest $(IMAGE)
	@echo "Pushed $(IMAGE)@$$(cat $(CTX)/digest)"

clean:
	rm -rf $(CTX) $(TMP_INDEX) src.tar
