"""Uji auto-upload Buffer (offline, requests di-mock): catbox host + createPost + endpoint."""
import sys
import time
from types import SimpleNamespace

try:
    sys.stdout.reconfigure(encoding="utf-8")   # konsol Windows aman cetak '→' dari status Buffer
except Exception:
    pass

from app.config import get_settings

s = get_settings()
s.buffer_api_key = "x"

import app.uploaders.buffer as bf  # noqa: E402
import requests as _rq  # noqa: E402

cap = {"gql": [], "catbox": 0}


class _Resp:
    def __init__(self, json_data=None, text=""):
        self._j, self.text = json_data, text

    def raise_for_status(self):
        pass

    def json(self):
        return self._j


def _fake_post(url, **kw):
    if "catbox" in url:
        cap["catbox"] += 1
        return _Resp(text="https://files.catbox.moe/abc123.mp4")
    payload = kw.get("json") or {}
    q, v = payload.get("query", ""), payload.get("variables", {})
    cap["gql"].append((q, v))
    if "organizations" in q:
        return _Resp({"data": {"account": {"organizations": [{"id": "org1", "name": "Me"}]}}})
    if "channels(" in q:
        return _Resp({"data": {"channels": [{"id": "ch1", "name": "TikTok Aku", "service": "tiktok"},
                                            {"id": "ch2", "name": "IG", "service": "instagram"}]}})
    if "createPost" in q:
        return _Resp({"data": {"createPost": {"post": {"id": "post_" + v["input"]["channelId"]}}}})
    return _Resp({"data": {}})


_rq.post = _fake_post
bf._verify_url = lambda url, **k: None        # lewati verifikasi HEAD (jangan sentuh jaringan)

# 1) catbox upload → URL langsung
assert bf._catbox_upload(__file__) == "https://files.catbox.moe/abc123.mp4"
print("1: catbox upload OK")

# 2) list_channels (organizations → channels)
chs = bf.list_channels(s)
assert [c["id"] for c in chs] == ["ch1", "ch2"], chs
print("2: list_channels OK")

# 3) create_post TERJADWAL → mutation benar (assets.video.url, channelId, customScheduled+dueAt)
cap["gql"].clear()
r = bf.create_post(s, "ch1", "halo", "https://v.mp4", due_at="2030-01-01T00:00:00Z")
inp = cap["gql"][-1][1]["input"]
assert inp["channelId"] == "ch1" and inp["assets"][0]["video"]["url"] == "https://v.mp4", inp
assert inp["mode"] == "customScheduled" and inp["dueAt"] == "2030-01-01T00:00:00Z", inp
assert r["id"] == "post_ch1", r
# 3b) tanpa jadwal → addToQueue
bf.create_post(s, "ch2", "halo", "https://v.mp4")
assert cap["gql"][-1][1]["input"]["mode"] == "addToQueue"
print("3: create_post (terjadwal & antrian) OK")

# 4) publish_clip → catbox 1x + post tiap channel
cap["catbox"] = 0
cap["gql"].clear()
res = bf.publish_clip(__file__, "teks", ["ch1", "ch2"], due_at="2030-01-01T00:00:00Z", settings=s)
assert cap["catbox"] == 1 and res["channels"] == 2, (cap, res)
print("4: publish_clip OK (host catbox 1x, post 2 channel)")

# 4b) publish_clip pakai GitHub bila dikonfigur (catbox tak dipanggil)
from app.uploaders import github_host as _gh  # noqa: E402
_gh.upload = lambda path, st=None: "https://raw.githubusercontent.com/o/r/main/clips/x.mp4"
s.github_token, s.github_repo = "tok", "o/r"
cap["catbox"] = 0
res2 = bf.publish_clip(__file__, "t", ["ch1"], settings=s)
assert cap["catbox"] == 0 and res2["video_url"].startswith("https://raw.githubusercontent.com/"), (cap, res2)
s.github_token, s.github_repo = "", ""        # reset utk tes endpoint berikut
print("4b: publish_clip via GitHub host OK")

# 5) endpoint /api/publish target buffer + /api/upload-auth + /api/buffer/channels
import app.main as M  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

clip_dir = s.output_dir / "bftest"
clip_dir.mkdir(parents=True, exist_ok=True)
(clip_dir / "clip_1.mp4").write_bytes(b"x")
M.manager.get = lambda jid: SimpleNamespace(clips=[SimpleNamespace(
    title="K", path="output/bftest/clip_1.mp4", caption="cap mantap", hashtags=["fyp"])])
bf.publish_clip = lambda *a, **k: {"video_url": "https://x", "posts": [{"id": "p1"}], "channels": 1}

with TestClient(M.app) as c:
    auth = c.get("/api/upload-auth").json()
    assert "buffer" in auth, auth
    assert c.get("/api/buffer/channels").json().get("channels"), "channels harus terisi (mock)"
    rr = c.post("/api/publish", json={"job_id": "x", "clips": [0], "targets": ["buffer"],
                                      "buffer_channels": ["ch1"]})
    assert rr.status_code == 200, rr.text
    tid = rr.json()["task_id"]
    t = {}
    for _ in range(100):
        t = c.get(f"/api/publish/{tid}").json()
        if t.get("done"):
            break
        time.sleep(0.05)
    by = {it["target"]: it for it in t["items"]}
    assert by["buffer"]["state"] == "done", t
    print("5: endpoint buffer OK ->", by["buffer"]["url"])

print("\nOK - auto-upload Buffer (catbox + createPost + endpoint) bekerja.")
