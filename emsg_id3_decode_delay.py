# emsg_id3_decode_delay.py (fixed)
import sys, struct, urllib.parse, time, io, csv
from datetime import datetime
import requests, m3u8
from mutagen.id3 import ID3

UA = "Lavf/62.3.100"

DEFAULT_HEADERS = {
    "User-Agent": UA,
    "Accept": "*/*",
    "Accept-Encoding": "identity",
    "Range": "bytes=0-",
}

SMOOTH_ALPHA = 0.10  # 0..1 (higher = faster tracking, more jitter)

def absolute_uri(base, uri):
    if uri.startswith(("http://","https://")):
        return uri
    return urllib.parse.urljoin(base, uri)

def fetch(session, url):
    r = session.get(url, timeout=10)
    r.raise_for_status()
    return r.content

def iter_boxes(buf):
    i, n = 0, len(buf)
    while i + 8 <= n:
        size = struct.unpack_from(">I", buf, i)[0]
        typ  = buf[i+4:i+8]
        if size == 0:
            size = n - i; hdr = 8
        elif size == 1:
            if i + 16 > n: break
            size = struct.unpack_from(">Q", buf, i+8)[0]; hdr = 16
        else:
            hdr = 8
        if size < hdr or i + size > n:
            break
        yield (typ, i, size, hdr)
        i += size

def read_cstring(b, off):
    end = b.find(b"\x00", off)
    if end == -1: return b[off:].decode("utf-8","ignore"), len(b)
    return b[off:end].decode("utf-8","ignore"), end + 1

def parse_emsg(payload):
    if len(payload) < 4: return None
    version = payload[0]; p = 4
    meta = {"version": version}
    try:
        if version == 1:
            if len(payload) < p + 4 + 8 + 4 + 4: return None
            timescale = struct.unpack_from(">I", payload, p)[0]; p += 4
            presentation_time = struct.unpack_from(">Q", payload, p)[0]; p += 8
            event_duration = struct.unpack_from(">I", payload, p)[0]; p += 4
            event_id = struct.unpack_from(">I", payload, p)[0]; p += 4
            scheme, p = read_cstring(payload, p)
            value,  p = read_cstring(payload, p)
            message_data = payload[p:]
            pts_seconds = (presentation_time / timescale) if timescale else None
            meta.update({
                "timescale": timescale,
                "pts_seconds": pts_seconds,
                "event_duration": event_duration,
                "event_id": event_id,
                "scheme": scheme, "value": value,
                "message_data": message_data
            })
            return meta
        elif version == 0:
            scheme, p = read_cstring(payload, p)
            value,  p = read_cstring(payload, p)
            if len(payload) < p + 16: return None
            timescale = struct.unpack_from(">I", payload, p)[0]; p += 4
            ptd = struct.unpack_from(">I", payload, p)[0]; p += 4
            event_duration = struct.unpack_from(">I", payload, p)[0]; p += 4
            event_id = struct.unpack_from(">I", payload, p)[0]; p += 4
            message_data = payload[p:]
            meta.update({
                "timescale": timescale, "pts_seconds": None,
                "ptd": ptd, "event_duration": event_duration,
                "event_id": event_id, "scheme": scheme, "value": value,
                "message_data": message_data
            })
            return meta
    except Exception:
        return None
    return None

def choose_variant(master_url, master_pl):
    if not (master_pl.is_variant and master_pl.playlists): return None
    for p in master_pl.playlists:
        uri = p.uri or ""
        codecs = (getattr(p.stream_info, "codecs", "") or "").lower()
        if "flac" in uri.lower() or "flac" in codecs:
            return urllib.parse.urljoin(master_pl.base_uri or (master_url.rsplit("/",1)[0]+"/"), uri)
    best = max(master_pl.playlists, key=lambda p: (getattr(p.stream_info,"bandwidth",0) or 0))
    return urllib.parse.urljoin(master_pl.base_uri or (master_url.rsplit("/",1)[0]+"/"), best.uri)

def decode_id3(id3_bytes):
    title = artist = album = None
    try:
        tag = ID3(io.BytesIO(id3_bytes))
        if tag.get("TIT2"): title  = tag["TIT2"].text[0]
        if tag.get("TPE1"): artist = tag["TPE1"].text[0]
        if tag.get("TALB"): album  = tag["TALB"].text[0]
    except Exception:
        pass
    return title, artist, album

def main(url):
    sess = requests.Session()
    headers = dict(DEFAULT_HEADERS); headers["Referer"] = url
    sess.headers.update(headers)

    print(f"Opened playlist: {url}")
    print("Decoding in-band ID3 with live delay calc… (Ctrl+C to stop)")

    txt = fetch(sess, url).decode("utf-8","ignore")
    pl = m3u8.loads(txt)

    if pl.is_variant:
        variant_url = choose_variant(url, pl)
        if not variant_url:
            print("Master playlist detected but no variants found."); return
        print(f"Master detected → variant selected: {variant_url}")
        url = variant_url
        headers["Referer"] = url; sess.headers.update(headers)
        txt = fetch(sess, url).decode("utf-8","ignore")
        pl = m3u8.loads(txt)

    base = pl.base_uri or (url.rsplit("/",1)[0]+"/")
    seen = set()

    have_offset = False
    O = 0.0  # wallclock-to-PTS offset estimate: O ≈ now - pts

    last_key = None  # (Title, Artist)
    csv_path = "id3_events.csv"
    csv_file = open(csv_path, "a", newline="")
    writer = csv.writer(csv_file)
    if csv_file.tell() == 0:
        writer.writerow(["WallClock","PTS_seconds","DelaySeconds","Title","Artist","Album","Scheme","EventID","Note"])

    try:
        while True:
            try:
                txt = fetch(sess, url).decode("utf-8","ignore")
                pl = m3u8.loads(txt)
            except Exception:
                time.sleep(1.0); continue

            segs = pl.segments[-5:] if len(pl.segments) > 5 else pl.segments
            for seg in segs:
                seg_url = absolute_uri(base, seg.uri)
                if seg_url in seen: continue
                seen.add(seg_url)

                try:
                    data = fetch(sess, seg_url)
                except Exception:
                    continue

                for typ, start, size, hlen in iter_boxes(data):
                    payload = data[start+hlen:start+size]

                    if typ == b"emsg":
                        em = parse_emsg(payload)
                        if not em: continue
                        scheme = (em.get("scheme") or "")
                        if ("id3" in scheme.lower()) or ("apple" in scheme.lower()):
                            md = em.get("message_data") or b""
                            if not md.startswith(b"ID3"):
                                idx = md.find(b"ID3")
                                if idx != -1:
                                    md = md[idx:]
                            title = artist = album = None
                            if md.startswith(b"ID3") and len(md) >= 10:
                                title, artist, album = decode_id3(md)

                            now = time.time()
                            pts  = em.get("pts_seconds")  # absolute PTS (seconds) for v1

                            # Build strings safely
                            if pts is not None:
                                current_O = now - pts
                                if not have_offset:
                                    O = current_O; have_offset = True
                                else:
                                    O = (1.0 - SMOOTH_ALPHA) * O + SMOOTH_ALPHA * current_O
                                current_pts_wallclock = now - O
                                delay_val = pts - current_pts_wallclock
                                if delay_val < 0 and delay_val > -0.75:
                                    delay_val = 0.0
                                delay_str = f"{delay_val:.3f}"
                                pts_str = f"{pts:.3f}"
                            else:
                                delay_str = "n/a"
                                pts_str = "n/a"

                            wall = datetime.now().strftime("%H:%M:%S")
                            key = (title or "", artist or "")
                            note = ""
                            if last_key is not None and key and key != last_key:
                                note = "NEXT"
                            if key:
                                last_key = key

                            print(f"[ID3][emsg] WallClock={wall} | PTS={pts_str} | Delay={delay_str}s | "
                                  f"Title={title} | Artist={artist} | Album={album} {('['+note+']') if note else ''}")

                            writer.writerow([
                                wall,
                                pts_str if pts_str != "n/a" else "",
                                delay_str if delay_str != "n/a" else "",
                                title or "", artist or "", album or "",
                                scheme, em.get("event_id",""), note
                            ])
                            csv_file.flush()

                    elif typ == b"id3 ":
                        md = payload
                        if not md.startswith(b"ID3"):
                            idx = md.find(b"ID3")
                            if idx != -1: md = md[idx:]
                        title = artist = album = (None, None, None)
                        if md.startswith(b"ID3") and len(md) >= 10:
                            title, artist, album = decode_id3(md)
                        wall = datetime.now().strftime("%H:%M:%S")
                        print(f"[ID3][id3 ] WallClock={wall} | PTS=n/a | Delay=n/a | Title={title} | Artist={artist} | Album={album}")
                        writer.writerow([wall,"","","","","","id3 box","", ""])
                        csv_file.flush()

                    elif typ == b"uuid":
                        if len(payload) > 16:
                            maybe = payload[16:]
                            if not maybe.startswith(b"ID3") and b"ID3" in maybe[:64]:
                                maybe = maybe[maybe.find(b"ID3"):]
                            if maybe.startswith(b"ID3"):
                                title, artist, album = decode_id3(maybe)
                                wall = datetime.now().strftime("%H:%M:%S")
                                print(f"[ID3][uuid] WallClock={wall} | PTS=n/a | Delay=n/a | Title={title} | Artist={artist} | Album={album}")
                                writer.writerow([wall,"","","","","","uuid","", ""])
                                csv_file.flush()

            time.sleep(1.5)
    except KeyboardInterrupt:
        pass
    finally:
        csv_file.close()
        print(f"\nSaved events to {csv_path}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python emsg_id3_decode_delay.py <playlist.m3u8 or master.m3u8>")
        sys.exit(1)
    main(sys.argv[1])