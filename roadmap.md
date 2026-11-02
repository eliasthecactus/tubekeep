# Roadmap

## v0.3.0: Ampli integration

[Ampli](https://github.com/eliasthecactus/ampli-ios) (Jellyfin, Navidrome and Audiobookshelf player) gets Tubekeep as a server type next to the others. Through Tubekeep it can search YouTube Music, play songs, and add songs to or remove them from YouTube playlists. Ampli never talks to YouTube itself: Tubekeep does all of it with the YouTube account assigned to Ampli.

```
Ampli ──(Tubekeep user + device token)──▶ Tubekeep /api/ampli/* ──(cookies of the Ampli account, proxy pool)──▶ YouTube
```

### Settings: new "Ampli" section

- **Integration** switch, off by default. While off, every `/api/ampli/*` route answers 404.
- **Account for Ampli**: one of the cookie accounts. The integration can only be switched on with an account picked.
- **Connected devices**: name, Tubekeep user, last seen, **Disconnect**.

New settings: `ampli_enabled` (false), `ampli_account_id` (none).

### Sign-in

- Ampli signs in with a normal Tubekeep username and password: `POST /api/ampli/login {username, password, device_name}`.
- Same password check and rate limit as the web login (`auth.verify_password`, `check_rate_limit`).
- The answer is a long-lived device token plus a signing secret. Both are stored hashed or encrypted in a new table `ampli_devices` (user, name, created, last seen).
- All other Ampli routes use `Authorization: Bearer <token>`, independent of the web session cookie and the `X-Requested-With` guard.
- Deleting a user or disconnecting a device ends access right away.

### API

| Route | Purpose |
|---|---|
| `POST /api/ampli/login` | username + password → device token |
| `POST /api/ampli/logout` | ends this device |
| `GET /api/ampli/status` | integration on, account name, Tubekeep version |
| `GET /api/ampli/search?q=` | songs (and videos) from YouTube Music |
| `GET /api/ampli/playlists` | playlists of the Ampli account, with whether each can be edited |
| `GET /api/ampli/playlists/{id}` | tracks of one playlist |
| `POST /api/ampli/playlists/{id}/tracks` | add a song |
| `DELETE /api/ampli/playlists/{id}/tracks/{videoId}` | remove a song |
| `POST /api/ampli/match` | title, artist, duration → best YouTube song (for adding Jellyfin/Navidrome songs to a YouTube playlist) |
| `GET /api/ampli/stream/{videoId}?exp=&sig=` | the audio |

Mostly reuses `YouTubeMusic.search`, `list_playlists`, `get_tracks`, `add_tracks`, `remove_tracks` and `matching.py`.

### Streaming

- **Song already downloaded by Tubekeep:** the file is served directly, with seeking (HTTP Range).
- **Otherwise:** yt-dlp resolves the m4a/AAC audio (format 140; AVPlayer can't play YouTube's Opus/WebM) with the account's cookies and the proxy pool.
  - Tubekeep passes the stream through to Ampli, including Range requests. This is required because YouTube ties audio links to the IP that asked for them.
  - Resolved links are cached until they expire (about 6 hours).
- **Limit:** at most 2 yt-dlp lookups at the same time, separate from the download queue. Never unbounded yt-dlp work.
- **Signed stream links:** each link carries an expiry and an HMAC of video id + expiry, made with the device's signing secret. The token never appears in URLs or proxy logs.

### Side effect

A song added from Ampli to a playlist Tubekeep syncs is downloaded on the next sync. If that folder is part of a Navidrome or Jellyfin library, it shows up in Ampli's normal library too.

### Frontend

- New "Ampli" section in Settings: switch, account picker, device list with Disconnect.
- A short note on how to connect Ampli: server URL plus a Tubekeep user.

### Tests

pytest with the fake yt-dlp and `FakeYTMusic`, covering:
- login (right and wrong password, rate limit)
- integration off → 404
- disconnected device and deleted user
- wrong or expired signature
- Range requests on proxied and local streams
- local file preferred over YouTube
- search, playlists, add and remove, match
- yt-dlp lookup limit

### Release

v0.3.0 on main with tag. CI builds the images `0.3.0`, `0.3` and `latest`. README gets an "Ampli" section.

## Later

- GitHub release pages for 0.2.1, 0.2.2 and 0.2.3.
- Try the YouTube write path (Replace, Remove, Ampli playlist edits) with a real account and note the results.
