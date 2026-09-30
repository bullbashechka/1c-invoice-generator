#!/usr/bin/env bash
set -e

mkdir -p /src/build_linux
cd /src/build_linux
cmake .. -DCMAKE_BUILD_TYPE=Release
cmake --build . -- -j$(nproc)

echo '--- Verify ---'
EXPORTS=$(nm -D /src/build_linux/SyntaxHelpReader.so | grep -cE 'GetClassNames|GetClassObject|DestroyObject|SetPlatformCapabilities')
echo "Exports found: $EXPORTS/4"
[ "$EXPORTS" -eq 4 ] || { echo '[ERROR] Not all 4 exports found'; exit 1; }
