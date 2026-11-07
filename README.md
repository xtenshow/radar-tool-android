# GTA SA Radar Tool — Android

KivyMD port of Radar Tool v8.0 by Tensho Records.

## How to build APK

1. Go to the **Actions** tab
2. Select **Build Android APK**
3. Click **Run workflow**
4. Wait 40–90 minutes
5. Download the APK from **Artifacts**


## Android emulator compatibility and app updates

The APK is built for `arm64-v8a`, `armeabi-v7a`, `x86_64` and `x86` so it can target ARM64
Android devices and x86_64 Android emulator instances such as suitably
configured BlueStacks 64-bit instances. The CI job checks all four ABI library folders and installs/launches the APK
on an Android 13 x86_64 emulator before uploading it.

The Android application ID remains `org.tenshorecords.radartool`; the version
is now `1.1.0` with version code `2`. Keep the application ID unchanged and
increase the version code for every future update.

### Stable signing is required for seamless updates

GitHub Actions can sign release APKs with one persistent keystore. Add these
repository Actions secrets under **Settings → Secrets and variables → Actions**:

- `ANDROID_KEYSTORE_BASE64` — base64-encoded keystore file
- `ANDROID_KEYSTORE_PASSWORD` — keystore password
- `ANDROID_KEY_ALIAS` — key alias
- `ANDROID_KEY_PASSWORD` — key password

When all four are configured, the workflow creates a signed release APK.
Without them, CI intentionally produces a debug APK; debug signing on fresh
GitHub-hosted runners is not a reliable update signature across builds.

Older APKs produced before stable signing may have a different debug
certificate. Android can reject an update signed with a different certificate;
a one-time uninstall may therefore be necessary when migrating from those
older test APKs. After installing the first stable-signed release, subsequent
updates signed with the same keystore can install over the existing app and
retain app-private data such as operation history.

## Automated checks

Every workflow run compiles the Python sources, runs tests for history
persistence and rollback-safe file writes, verifies the APK package/version,
checks all four ARM/x86 native ABIs and runs an Android x86_64 install smoke test, and verifies the APK signature.
