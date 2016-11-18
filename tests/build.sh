#!/bin/bash -e

if [[ -n "$CONFIG_BZR_REPO" ]] ; then
  make configrepo
fi
make installdeps
