#!/bin/sh
# Rebuild three-bundle.js (three.js + OrbitControls + GLTF/STL/OBJ loaders as one classic
# script). Needs node; nothing here is needed at run time.
set -e
cd "$(dirname "$0")"
work=$(mktemp -d)
cp entry.js "$work/"
cd "$work"
npm init -y >/dev/null
npm install --silent three@0.186.1 esbuild@0.25
npx esbuild entry.js --bundle --minify --format=iife --target=chrome110 \
    --legal-comments=none --outfile=three-bundle.js
cp three-bundle.js "$OLDPWD/../three-bundle.js"
cp node_modules/three/LICENSE "$OLDPWD/../LICENSE.three"
echo "three-bundle.js rebuilt"
