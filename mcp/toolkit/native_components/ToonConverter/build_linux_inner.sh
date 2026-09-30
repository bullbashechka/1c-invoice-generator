#!/usr/bin/env bash
set -e

rm -rf /src/build_linux
mkdir -p /src/build_linux
cd /src/build_linux
cmake .. -DCMAKE_BUILD_TYPE=Release -DTOON_CONVERTER_SMOKE_TEST=ON \
    -DCMAKE_CXX_COMPILER=g++-10 -DCMAKE_C_COMPILER=gcc-10
cmake --build . -- -j$(nproc)

echo '--- Smoke test ---'
./toon_smoke_test

echo '--- Verify exports ---'
EXPORTS=$(nm -D /src/build_linux/ToonConverter.so | grep -cE 'GetClassNames|GetClassObject|DestroyObject|SetPlatformCapabilities')
echo "Exports found: $EXPORTS/4"
[ "$EXPORTS" -eq 4 ] || { echo '[ERROR] Not all 4 exports found'; exit 1; }
