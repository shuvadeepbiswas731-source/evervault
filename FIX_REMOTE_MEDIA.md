# Fix for remote photos/videos

The gallery previously tried to load media using normal `<img>` / `<video>` URLs, which do not attach the Evervault Bearer token. Uploads worked because `fetch()` uploads included the token, but media GETs were unauthorized.

This update fetches media with the `Authorization: Bearer ...` header, creates local browser object URLs, and uses those URLs for thumbnails and viewer playback. It does not put the session token into a query string.

## Apply the fix
1. Stop the Evervault server and Cloudflare tunnel.
2. Back up your current `G:\evervault_media_cloud` project folder.
3. Extract this updated project into a separate folder, or copy only the updated `app\web\assets\app.js` into your current project (and `android\www\assets\app.js` for the Android wrapper).
4. Start Evervault using your updated launcher, then restart the tunnel.
5. On your phone, reload the public URL. If needed, close the tab and open it again to bypass cached JavaScript.
6. Open a photo and play a video to verify.

Your media and database on `G:\EvervaultData` are not changed by this fix.
