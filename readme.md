Here’s a README.md you can drop into the repo alongside emsg_id3_decode_delay.py 👇

⸻

emsg_id3_decode_delay.py

Apple HLS → decode in-band ID3 + compute live “next track” delay

This script listens to an Apple HLS stream (fMP4/MP4 segments), extracts timed ID3 metadata carried inside emsg boxes (and the Apple UUID variant), converts PTS → wall-clock, and logs when the next track should be shown in your UI.

It’s built for cases where a NowPlaying feed flips before the audio actually changes. Instead of guessing, this reads the timed metadata embedded in the stream and computes a per-event delay.

⸻

What it does
	1.	Playlist handling
	•	Opens the HLS URL you pass in.
	•	If the URL is a master playlist, it automatically picks the variant (e.g. your FLAC rendition).
	2.	Segment parsing
	•	Downloads .m4s segments.
	•	Parses MP4 boxes and finds timed-metadata carriers:
	•	emsg v1 with scheme_id_uri = https://developer.apple.com/streaming/emsg-id3
	•	(and the Apple UUID ID3 carrier if present)
	3.	ID3 decoding
	•	Pulls the ID3 payload and decodes frames with Mutagen (TIT2, TPE1, TALB, etc.).
	4.	PTS → wall-clock delay
	•	Uses the first ID3 as a baseline: (base_pts, base_wall_time).
	•	For each subsequent ID3 with PTS:

predicted_time  = base_wall_time + (pts - base_pts)
delay_seconds   = predicted_time - now()


	•	That delay_seconds is the time until the new track should be shown.

	5.	[NEXT] flag
	•	When (title, artist, album) changes from the last announced values, the line is annotated with [NEXT] so you can key off true track changes.
	6.	CSV log
	•	Appends a row to id3_events.csv with: WallClock,AbsPTS,RelPTS,DeltaSincePrev,Title,Artist,Album,Source.

⸻

Example output

[ID3][emsg] WallClock=22:16:20 | PTS=203094.613 | Delay=11.585s | Title=Ride With You | Artist=Blackfoot | Album=Vertical Smiles
[ID3][emsg] WallClock=22:16:40 | PTS=203116.715 | Delay=10.653s | Title=It Ain't Over (Til It's Over) | Artist=No Love Lost | Album=Last Call [NEXT]

	•	PTS: presentation timestamp on the stream’s global timeline (does not reset per song).
	•	Delay: how long from now until the audio for that metadata should begin.

⸻

Why you’d use this
	•	Your NowPlaying feed updates early and you need the true switch time.
	•	You want a simple console/CSV signal to integrate into tooling, dashboards, or another writer.
	•	Your encoder follows the Apple timed metadata approach (ID3 in emsg / UUID).

⸻

Requirements
	•	Python 3.10+
	•	pip install the deps:

requests
m3u8
mutagen

(They’re typically listed in requirements.txt in this repo.)

⸻

Quick start

python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Run against your HLS playlist (master or variant)
python emsg_id3_decode_delay.py https://your-host/path/stream.m3u8

	•	Lines print to stdout as events arrive.
	•	A running CSV log is written next to the script as id3_events.csv.

⸻

How delay is computed (details)
	•	Baseline: first ID3 seen → base_pts and base_wall_time = now().
	•	Subsequent event:
rel_pts = pts - base_pts
predicted_wall = base_wall_time + rel_pts
delay = predicted_wall - now()
	•	This avoids relying on wall-clock from the server and stays stable even if PTS is a large number (streams run for days).

⸻

Notes & gotchas
	•	PTS looks huge? Totally normal; it’s a monotonic clock across the entire broadcast.
	•	Duplicate metadata lines: some encoders emit repeated ID3 every N seconds for the same track. You’ll see multiple lines until the next track. Use [NEXT] to detect the real change.
	•	No ID3 showing up? Use a segment probe (or ffprobe) to ensure your encoder emits emsg with the Apple ID3 scheme/UUID. Some stacks only inject timed metadata for HLS-TS, not fMP4.
	•	Network time: The script uses the host wall clock; significant skew will bias computed delays.

⸻

Troubleshooting
	•	No output
	•	Verify the URL is reachable and returns HLS (not a 302, not DRM).
	•	If it’s a master playlist, ensure there’s a valid audio-only variant.
	•	Decoding errors
	•	You may see one-off lines like ID3v2.xx not supported if an encoder emits oddities; later events typically parse fine.
	•	Delay jitter
	•	Small fluctuations (<1–2 s) can come from segment fetch timings; let it run and prefer the latest line per track.

⸻

License

MIT. PRs welcome—especially probes/fixtures for different encoder flavors of emsg/UUID ID3.
