"""Two-camera matching and heading maths, without a server (uploads are captured)."""
import math, random
import beemap_agent as agent

def make(mode, flip=False):
    cfg = agent.Config(server="http://127.0.0.1:9", station_id="t", token="x", heading_camera="simulated",
                       heading_mode=mode, heading_hfov=120.0, heading_flip=flip, queue_dir="/tmp/beemap-agent-test",
                       send_live_frame=False, heartbeat_s=3600)
    hook = agent.BeeMapHook(cfg=cfg)
    sent = []
    hook.up.send_visit = lambda meta, jpeg, clip=None: sent.append(meta)
    return hook, sent

def run(mode, ref, compasses, flip=False):
    hook, sent = make(mode, flip)
    rng = random.Random(1)
    t = 1000.0
    for c in compasses:
        t_end = agent.fake_visit(hook, rng.uniform(0, 360), t, rng, draw=False)
        agent.fake_heading(hook, c, ref, t_end, rng)
        hook.flush()
        t = t_end + 30
    import time; time.sleep(0.3)
    return sent

def test_sky():
    ref = 40
    for c in (0, 75, 160, 300):
        m = run("sky", ref, [c])[0]
        got = (ref + m["heading_rel_deg"]) % 360
        assert m["heading_source"] == "heading_camera" and abs((got - c + 180) % 360 - 180) < 4, (c, got)
    print("sky heading camera: compass directions recovered within 4°")

def test_horizon_and_out_of_view():
    ref = 240
    for c in (200, 240, 285):
        m = run("horizon", ref, [c])[0]
        got = (ref + m["heading_rel_deg"]) % 360
        assert abs((got - c + 180) % 360 - 180) < 3, (c, got)
    m = run("horizon", ref, [60])[0]          # behind the camera: presence only
    assert m["heading_source"] is None and "heading_rel_deg" not in m
    print("horizon heading camera: within 3° inside its view; outside it the visit is presence-only")

def test_flip():
    m = run("sky", 0, [90], flip=True)[0]
    assert abs(((m["heading_rel_deg"]) - 90 + 180) % 360 - 180) < 4
    print("flipped heading camera handled")

def test_confidence_and_clip():
    """Each visit reports its median confidence and keeps a clip of its last seconds."""
    cfg = agent.Config(server="http://127.0.0.1:9", station_id="t", token="x", queue_dir="/tmp/beemap-agent-test",
                       send_live_frame=False, heartbeat_s=3600, clip_s=4)
    hook = agent.BeeMapHook(cfg=cfg)
    sent = []
    hook.up.send_visit = lambda meta, jpeg, clip=None: sent.append((meta, clip))
    rng = random.Random(3)
    t = 1000.0
    for _ in range(6):
        t = agent.fake_visit(hook, rng.uniform(0, 360), t, rng) + 30
    import time; time.sleep(0.3)
    assert len(sent) == 6
    for meta, clip in sent:
        assert cfg.min_confidence <= meta["confidence_median"] <= meta["confidence"] <= 1, meta
        span = clip[-1][0] - clip[0][0]
        assert 2 < span <= cfg.clip_s + cfg.lost_after_s + 0.2 and clip[-1][0] >= meta["ts"], span
    video = agent.make_clip(sent[0][1])
    assert video and len(video[0]) > 1000 and video[1] in ("mp4", "avi")
    assert video[1] != "mp4" or video[0][4:8] == b"ftyp"
    print(f"visits carry their median confidence; {len(sent[0][1])}-frame clip encoded as {video[1]}")

if __name__ == "__main__":
    import logging; logging.disable(logging.WARNING)
    test_sky(); test_horizon_and_out_of_view(); test_flip(); test_confidence_and_clip(); print("OK")
