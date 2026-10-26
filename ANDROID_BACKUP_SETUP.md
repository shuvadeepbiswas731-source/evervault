# Evervault Android automatic backup — setup guide

## A. Update the Windows web UI
1. Stop the Evervault server.
2. Back up `G:\\evervault_media_cloud\\app\\web\\index.html`, `app\\web\\assets\\app.js`, and `style.css`.
3. Copy those three files from this project into the same paths in `G:\\evervault_media_cloud`.
4. Start Evervault again. Open the HTTPS tunnel URL and sign in. The sidebar should show **Automatic backup**. In a normal browser, the panel explains that native Android support is required; this is expected.

## B. Build the Android app with native background backup
Follow `android/README.md` and `android/native-background-backup/README.md`. The app must be built with the native plugin registered and WorkManager dependency added.

## C. Enable it on the phone
1. Open the Evervault APK and sign in.
2. Tap **Automatic backup**.
3. Paste the current HTTPS Quick Tunnel address if it is not already filled.
4. Choose **Wi-Fi / unmetered network + charging** (recommended) or **Any network + charging**.
5. Tap **Enable automatic backup** and grant photo/video permissions.

## D. Verify
- Connect the phone to Wi-Fi and charging.
- Keep the PC awake, Evervault server running, and Cloudflare tunnel open.
- Wait for Android's scheduled WorkManager run (not instant; Android controls exact timing).
- Confirm a new photo appears in Evervault and exists under `G:\\EvervaultData\\media`.

## Security and operational notes
- Do not share your public tunnel URL.
- The worker stores the current Evervault bearer token in app-private preferences; for a hardened release, use encrypted storage and a refresh/re-authentication flow.
- The Quick Tunnel URL can change. Update it in the app's backup settings when it changes.
- The included native source has not been compiled or device-tested in this environment. Build and test with non-critical photos before relying on it.
- A single HDD is not a backup; keep a second copy of important media.
