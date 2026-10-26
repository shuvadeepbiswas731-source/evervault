# Native Android background backup module (Capacitor 6)

This source module schedules periodic background work with Android WorkManager. The worker requires charging and either an unmetered network (Wi-Fi by default) or any connected network, scans MediaStore images/videos, and uploads unseen items using Evervault's existing authenticated chunk upload API.

## Important behavior and limits
- Android WorkManager periodic jobs are not instant; Android chooses execution time, and the minimum periodic interval is 15 minutes.
- User must explicitly enable backup and grant photo/video permissions.
- It uploads all media visible in Android's shared MediaStore on the first run, then skips items recorded in app preferences. New items are picked up on later runs.
- This initial version uses MediaStore IDs for deduplication. Clearing app data or reinstalling may cause re-uploading. If the Evervault tunnel URL changes, update the server address in the app and reconfigure backup.
- The token is stored in app-private SharedPreferences for the worker. For a hardened production release, migrate it to encrypted storage and implement token refresh/re-authentication.
- Android 13+ photo/video permissions are requested separately. The user must grant both for a complete backup.
- Validate compilation and test with a small library before relying on it for irreplaceable files. A second backup is still recommended.

## Integrate after generating the Capacitor Android project
1. In `android/`, run `npm install`, then `npx cap add android` if the native `android/` directory does not exist.
2. Copy `src/main/java/com/evervault/personalmedia/EvervaultBackupPlugin.java` and `EvervaultBackupWorker.java` into `android/app/src/main/java/com/evervault/personalmedia/`.
3. In `android/app/src/main/java/com/evervault/personalmedia/MainActivity.java`, register the plugin inside `onCreate` before `super.onCreate`:
   ```java
   import com.evervault.personalmedia.EvervaultBackupPlugin;
   // inside class MainActivity:
   @Override
   public void onCreate(android.os.Bundle savedInstanceState) {
       registerPlugin(EvervaultBackupPlugin.class);
       super.onCreate(savedInstanceState);
   }
   ```
   If your MainActivity already overrides `onCreate`, add only the `registerPlugin(...)` call before `super.onCreate(...)`.
4. Add this dependency to `android/app/build.gradle` inside `dependencies`:
   ```gradle
   implementation "androidx.work:work-runtime:2.9.1"
   ```
5. Add permissions to `android/app/src/main/AndroidManifest.xml` above `<application>`:
   ```xml
   <uses-permission android:name="android.permission.READ_MEDIA_IMAGES" />
   <uses-permission android:name="android.permission.READ_MEDIA_VIDEO" />
   <uses-permission android:name="android.permission.READ_EXTERNAL_STORAGE" android:maxSdkVersion="32" />
   ```
6. Sync and build:
   ```powershell
   npx cap sync android
   npx cap open android
   ```
   In Android Studio, build an APK. Test opt-in, permission prompts, backup while charging, pause, and a changed tunnel URL.

The Capacitor wrapper must have its native bridge enabled. If the app is configured to load an external URL, verify `window.Capacitor.Plugins.EvervaultBackup` exists in that WebView. For production, bundle the web assets locally and configure an explicit API base URL rather than depending on a remote page to host the app UI.
