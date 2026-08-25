import asyncio
import json

import pytest
import yaml
from aiohttp.test_utils import TestClient, TestServer

from s300d import settings as st
from tests.test_server import FakeSource
from s300d import server as srv

BASE = yaml.safe_load(open("config.yaml", encoding="utf-8"))


def test_apply_patch_toggles_and_edits_alarm():
    new = st.apply_patch(BASE, {"alarms": {"overboost": {"enabled": True, "warn": 7.0, "clear": 6.0}}})
    assert new["alarms"]["overboost"]["enabled"] is True
    assert new["alarms"]["overboost"]["warn"] == 7.0
    assert BASE["alarms"]["overboost"]["enabled"] is False  # original untouched


def test_apply_patch_rejects_bad_deadband_and_unknown_things():
    with pytest.raises(ValueError):
        st.apply_patch(BASE, {"alarms": {"ect_high": {"clear": 110}}})  # clear above warn
    with pytest.raises(ValueError):
        st.apply_patch(BASE, {"alarms": {"battery_low": {"clear": 12.0}}})  # below rule
    with pytest.raises(ValueError):
        st.apply_patch(BASE, {"alarms": {"nope": {}}})
    with pytest.raises(ValueError):
        st.apply_patch(BASE, {"server": {"host": "0.0.0.0"}})  # not editable
    with pytest.raises(ValueError):
        st.apply_patch(BASE, {"shift_light": {"amber": 8000, "red": 7900, "flash": 8100}})
    with pytest.raises(ValueError):
        st.apply_patch(BASE, {"mac": "not-a-mac"})


def test_apply_patch_scaling_blank_removes_override():
    new = st.apply_patch(BASE, {"scaling_overrides": {"CT_RPM": 0.5, "CT_TPS": ""}})
    assert new["scaling_overrides"] == {"CT_RPM": 0.5}
    with pytest.raises(ValueError):
        st.apply_patch(BASE, {"scaling_overrides": {"CT_NOPE": 1}})


def test_apply_patch_ui_display():
    new = st.apply_patch(BASE, {"ui": {"show_rpm": False, "units": "imperial",
                                       "tiles_big": ["rpm", "ect_c", "iat_c", "vbat"],
                                       "tiles_small": ["tps", "vtec", "map_kpa"]}})
    assert new["ui"]["show_rpm"] is False and new["ui"]["units"] == "imperial"
    assert new["ui"]["tiles_big"] == ["rpm", "ect_c", "iat_c", "vbat"]
    assert new["ui"]["ws"] == BASE["ui"]["ws"]  # non-editable ui keys preserved
    assert BASE["ui"].get("show_rpm", True) is True  # original untouched
    new = st.apply_patch(BASE, {"ui": {"theme": {"bg": "#102030", "accent": "#FFB000"}}})
    assert new["ui"]["theme"]["bg"] == "#102030"
    assert new["ui"]["theme"]["accent"] == "#ffb000"  # normalised to lowercase
    assert new["ui"]["theme"]["warn"] == BASE["ui"]["theme"]["warn"]  # merged, not replaced
    for bad in ({"theme": {"bg": "red"}},                               # not #rrggbb
                {"theme": {"sparkles": "#ffffff"}},                     # unknown colour
                {"theme": "dark"},                                      # not a mapping
                {"tiles_big": ["rpm"]},                                 # wrong slot count
                {"tiles_big": ["nope", "ect_c", "iat_c", "vbat"]},      # unknown sensor
                {"tiles_small": "tps"},                                 # not a list
                {"units": "kelvin"},
                {"ws": "ws://0.0.0.0:1/ws"}):                           # file-only key
        with pytest.raises(ValueError):
            st.apply_patch(BASE, {"ui": bad})


def test_save_atomic_roundtrip(tmp_path):
    p = tmp_path / "c.yaml"
    st.save_atomic(str(p), BASE)
    assert yaml.safe_load(p.read_text()) == BASE
    assert not [f for f in tmp_path.iterdir() if f.name.startswith(".config-")]


def test_api_put_saves_and_hot_reloads(tmp_path):
    p = tmp_path / "c.yaml"
    st.save_atomic(str(p), BASE)
    src = FakeSource()
    hub = srv.Hub(src, BASE["alarms"], BASE["shift_light"])

    async def body():
        client = TestClient(TestServer(st.make_app(hub, str(p))))
        await client.start_server()
        try:
            r = await client.get("/api/config")
            assert (await r.json())["alarms"]["ect_high"]["warn"] == 105
            r = await client.put("/api/config", json={"alarms": {"ect_high": {"warn": 95, "clear": 90}},
                                                     "shift_light": {"amber": 7000, "red": 7500, "flash": 8000},
                                                     "scaling_overrides": {"CT_RPM": 0.5}})
            assert (await r.json()) == {"ok": True}
            saved = yaml.safe_load(p.read_text())
            assert saved["alarms"]["ect_high"]["warn"] == 95
            assert hub.alarm_rules["ect_high"]["warn"] == 95
            assert hub.shift_stages["amber"] == 7000
            assert src.scaling_overrides == {"CT_RPM": 0.5} and src.released and src.resumed
            r = await client.put("/api/config", json={"alarms": {"ect_high": {"clear": 200}}})
            assert r.status == 400 and yaml.safe_load(p.read_text())["alarms"]["ect_high"]["clear"] == 90
            r = await client.post("/api/command", json={"cmd": "read_dtc"})
            assert r.status == 400
            r = await client.get("/")
            assert "Del Sol cluster" in await r.text()
            r = await client.get("/api/live")
            assert set(await r.json()) >= {"state", "d", "a", "raw"}
        finally:
            await client.close()
    asyncio.run(body())
