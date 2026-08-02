#!/bin/bash

oc new-app --template=postgresql-persistent \
	-p DATABASE_SERVICE_NAME=lls-postgresql \
	-p POSTGRESQL_DATABASE=lls \
	-p VOLUME_CAPACITY=10Gi \
	-p POSTGRESQL_VERSION=10-el8

