#!/bin/bash
# Wrapper script for AWS CLI with Copernicus S3 endpoint
# Usage: ./aws-copernicus.sh s3 ls s3://eodata/...

# Set the Copernicus endpoint URL
export AWS_S3_ENDPOINT_URL="https://eodata.dataspace.copernicus.eu"

# Run AWS CLI with the copernicus profile and custom endpoint
aws "$@" --profile copernicus --endpoint-url "${AWS_S3_ENDPOINT_URL}"
