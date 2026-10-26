# Evervault Android app

This project contains the Capacitor wrapper and an opt-in native WorkManager background-backup module.

## Build
1. Install Node.js LTS and Android Studio.
2. Replace `https://YOUR-DOMAIN.example` in `capacitor.config.json` with your current Evervault HTTPS URL. A Quick Tunnel URL may change after restart.
3. From this directory run:
   ```powershell
   npm install
   npx cap add android
   ```
   Skip `npx cap add android` if the native `android/` directory already exists.
4. Follow `native-background-backup/README.md` to add the native WorkManager plugin, permissions, and dependency.
5. Copy the latest web files into `www/` (they are included in this project), then run:
   ```powershell
   npx cap sync android
   npx cap open android
   ```
6. In Android Studio build an APK. On your phone, sign in, open **Automatic backup**, set the HTTPS server URL, and enable backup. Grant photo and video permissions.

## Automatic backup behavior
- Off by default; explicitly opt in.
- Default mode: network is unmetered (normally Wi-Fi) AND phone is charging.
- Optional mode: any network AND phone is charging.
- Android runs the job periodically, not immediately on every new photo.
- The PC and Evervault server must be on, connected, and reachable. If the Quick Tunnel URL changes, update it in Automatic backup settings.
- This is an initial implementation and should be tested with non-critical media before relying on it. See the native module README for limits and production hardening notes.

The Android APK is not prebuilt in this ZIP. Build it on a machine with Android Studio and SDK installed.
