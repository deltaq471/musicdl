Music DL — macOS
================
YouTube + SoundCloud + Spotify downloader. No ffmpeg needed.

QUICK START (easy way)
  Double-click  Run.command   (first launch installs yt-dlp once into
  this folder and then opens the app), or run it from Terminal:
      sh run.sh

STANDALONE BUILD (optional, makes a single-file binary)
  sh build.sh
  Result: dist/MusicDL  (run with:  ./dist/MusicDL)

REQUIREMENTS
  Python 3.9+ (macOS includes it at /usr/bin/python3; Homebrew's
  Homebrew Python also works — it ships with Tk).

NOTE
  When you first open the app from a downloaded folder, macOS may say
  the app is from an unknown developer. Right-click the file and choose
  "Open", then "Open" again.

HOW TO USE
  Paste a YouTube, SoundCloud or Spotify (track/album/playlist) link,
  click "+ Add". Formats: SoundCloud -> native mp3; YouTube/Spotify ->
  native m4a (AAC) or WebM; convert to other formats only if ffmpeg is
  installed.
  "No metadata" button: red = tags on, green = raw audio.