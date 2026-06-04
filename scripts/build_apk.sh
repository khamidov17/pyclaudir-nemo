#!/usr/bin/env bash
# Build Nemo APK, increment version, copy to data/apk/ for OTA serving.
# Usage: ./scripts/build_apk.sh

set -euo pipefail
cd "$(dirname "$0")/.."

VERSION_FILE="nemo-app/VERSION"
APK_SRC="nemo-app/build/app/outputs/flutter-apk/app-release.apk"
APK_DIR="data/apk"

# Read + increment version
VERSION=$(cat "$VERSION_FILE" | tr -d '[:space:]')
NEW_VERSION=$((VERSION + 1))
echo "$NEW_VERSION" > "$VERSION_FILE"
echo "Building Nemo v$NEW_VERSION..."

# Build
export JAVA_HOME=/opt/homebrew/opt/openjdk@17
export PATH="$JAVA_HOME/bin:$PATH"
export ANDROID_HOME=~/Library/Android/sdk
export ANDROID_SDK_ROOT=~/Library/Android/sdk

cd nemo-app
flutter pub get
flutter build apk --release --android-skip-build-dependency-validation \
  --build-number="$NEW_VERSION" \
  --build-name="1.0.$NEW_VERSION"
cd ..

# Deploy for OTA
mkdir -p "$APK_DIR"
cp "$APK_SRC" "$APK_DIR/nemo-latest.apk"
echo "$NEW_VERSION" > "$APK_DIR/version.txt"

# Also copy to Desktop
cp "$APK_SRC" ~/Desktop/Nemo.apk

echo ""
echo "✓ Nemo v$NEW_VERSION built"
echo "  APK: $APK_DIR/nemo-latest.apk"
echo "  Desktop: ~/Desktop/Nemo.apk"
echo "  OTA: the running server will serve /apk/version + /apk/download"
