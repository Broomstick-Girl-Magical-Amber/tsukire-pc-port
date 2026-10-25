#!/bin/bash
# Install the Wonder APK and push the game data to a device or emulator (serial as $1, default emulator-5554).
# The data goes to Internal storage/Super Mario Bros. Wonder, with all-files access granted by appops.
set -e
S=${1:-emulator-5554}
cd "$(dirname "$0")/.."
PKG=io.github.belmantegu.wonderrecomp
D="/storage/emulated/0/Super Mario Bros. Wonder"
adb -s "$S" install -r private/wonder/apk/wonder.apk | tail -1
adb -s "$S" shell appops set --uid "$PKG" MANAGE_EXTERNAL_STORAGE allow
adb -s "$S" shell mkdir -p "'$D/analysis'" "'$D/extract/romfs'" "'$D/fonts'" "'$D/spirv'" "'$D/frames'"
adb -s "$S" push private/wonder/analysis/main.bin private/wonder/analysis/sdk.bin "$D/analysis/" | tail -1
adb -s "$S" push private/wonder/fonts/shared_5.ttf "$D/fonts/" | tail -1
adb -s "$S" push private/wonder/celular/config.txt "$D/" | tail -1
if [ "${2:-}" != "--no-data" ]; then
    adb -s "$S" push private/wonder/spirv/. "$D/spirv/" | tail -1
    adb -s "$S" push private/wonder/romfs/. "$D/extract/romfs/" | tail -1
fi
echo done
