import pytest
import yaml

from s300d import alarms as al

CONFIG = yaml.safe_load(open("config.yaml", encoding="utf-8"))
RULES = CONFIG["alarms"]
CHANNELS_NO_WB = [(0x0100, 0x83), (0x0160, 0x50), (0x0320, 0x9E)]
CHANNELS_WB = CHANNELS_NO_WB + [(0x0329, 0x9E)]


def base(**kw):
    d = {"rpm": 3000, "map_kpa": 40.0, "boost_psi": -8.0, "tps": 20.0, "ect_c": 88.0,
         "iat_c": 30.0, "vbat": 14.0, "knock_retard": 0.0, "knock_count": 0,
         "boost_cut": False, "vtec": False, "shift_stage": 0}
    d.update(kw)
    return d


def run(engine, samples, t0=0.0, dt=0.1):
    """Feed a list of gauge dicts; return the list of alarm snapshots per step."""
    out = []
    for i, d in enumerate(samples):
        out.append(engine.update(d, t0 + i * dt))
    return out


# --- derive ---------------------------------------------------------------

def test_derive_converts_temps_once_and_computes_boost():
    values = {"RPM": 3420, "MAP": 42.1, "BarometricPressure": 101.4, "TPS": 18.5,
              "ECT": 190.4, "IAT": 93.2, "BatteryVoltage": 14.1, "KnockRetard": 0.0,
              "VtecSpool": True}
    d = al.derive(values, CHANNELS_NO_WB)
    assert d["ect_c"] == pytest.approx(88.0)
    assert d["iat_c"] == pytest.approx(34.0)
    assert d["boost_psi"] == pytest.approx((42.1 - 101.4) * 0.145038)
    assert d["vtec"] is True
    assert d["shift_stage"] == 0
    assert "wideband_lambda" not in d


def test_derive_boost_none_without_baro_channel():
    assert al.derive({"MAP": 50.0}, [])["boost_psi"] is None


def test_shift_stages_from_config():
    stages = CONFIG["shift_light"]
    assert [al.shift_stage(r, stages) for r in (7000, 7400, 7900, 8100, 9000)] == [0, 1, 2, 3, 3]
    assert al.shift_stage(None, stages) == 0


# --- debounce / hysteresis / latching -----------------------------------------

def test_single_sample_spike_does_not_trip():
    eng = al.AlarmEngine(RULES)
    samples = [base()] * 3 + [base(ect_c=110.0)] + [base()] * 5
    assert all(a == [] for a in run(eng, samples))


def test_trips_after_n_consecutive_samples():
    eng = al.AlarmEngine(RULES)
    out = run(eng, [base(ect_c=107.0)] * 5)
    assert out[3] == []
    assert out[4][0]["id"] == "ect_high" and out[4][0]["level"] == "warn"
    assert out[4][0]["since"] == pytest.approx(0.4)
    assert out[4][0]["latched"] is False


def test_oscillating_across_trip_threshold_does_not_flicker():
    eng = al.AlarmEngine(RULES)
    samples = [base(ect_c=106.0)] * 5                       # trip warn
    samples += [base(ect_c=104.0), base(ect_c=106.0)] * 10   # hover around 105, above clear (100)
    out = run(eng, samples)
    assert all(len(a) == 1 and a[0]["level"] == "warn" for a in out[4:])
    sinces = {a[0]["since"] for a in out[4:]}
    assert len(sinces) == 1  # never re-tripped


def test_warn_clears_only_below_clear_threshold():
    eng = al.AlarmEngine(RULES)
    out = run(eng, [base(ect_c=106.0)] * 5 + [base(ect_c=102.0)] * 3 + [base(ect_c=99.0)])
    assert out[7] and out[7][0]["level"] == "warn"  # 102 is under warn but above clear
    assert out[8] == []


def test_critical_latches_until_ack():
    eng = al.AlarmEngine(RULES)
    out = run(eng, [base(ect_c=115.0)] * 5 + [base()] * 10)
    crit = out[4][0]
    assert crit["level"] == "critical" and crit["latched"] is True
    assert out[-1] == [crit | {"value": out[-1][0]["value"]}]  # still active at normal temp
    eng.ack()
    assert eng.active() == []
    assert run(eng, [base()])[0] == []


def test_ack_while_still_critical_keeps_alarm_active():
    eng = al.AlarmEngine(RULES)
    run(eng, [base(ect_c=115.0)] * 5)
    eng.ack()
    out = run(eng, [base(ect_c=115.0)] * 2)
    assert out[-1][0]["level"] == "critical"
    assert run(eng, [base()])[0] == []  # self-clears after ack once back in range


def test_warn_escalates_to_critical():
    eng = al.AlarmEngine(RULES)
    out = run(eng, [base(ect_c=107.0)] * 5 + [base(ect_c=113.0)] * 5)
    assert out[4][0]["level"] == "warn"
    assert out[-1][0]["level"] == "critical"


# --- gates ------------------------------------------------------------------------

def test_battery_low_gated_on_rpm():
    eng = al.AlarmEngine(RULES)
    assert run(eng, [base(vbat=12.5, rpm=800)] * 6)[-1] == []
    out = run(eng, [base(vbat=12.5, rpm=2000)] * 5)
    assert out[-1][0]["id"] == "battery_low" and out[-1][0]["level"] == "warn"


def test_battery_low_direction_below_and_critical():
    eng = al.AlarmEngine(RULES)
    out = run(eng, [base(vbat=11.5, rpm=2000)] * 5)
    assert out[-1][0]["level"] == "critical"


def test_disabled_rules_never_fire():
    eng = al.AlarmEngine(RULES)
    assert "overboost" not in eng.rules and "boost_cut" not in eng.rules
    assert run(eng, [base(boost_psi=15.0, boost_cut=True)] * 10)[-1] == []


def test_overboost_fires_when_enabled():
    rules = {"overboost": RULES["overboost"] | {"enabled": True}}
    eng = al.AlarmEngine(rules)
    assert run(eng, [base(boost_psi=9.0)] * 3)[-1][0]["level"] == "warn"


# --- AFR gating -------------------------------------------------------------------

def test_no_afr_alarm_without_wideband():
    eng = al.AlarmEngine(RULES, CHANNELS_NO_WB)
    assert "lean" not in eng.rules
    assert al.afr_available(CHANNELS_NO_WB) is False
    out = run(eng, [base(tps=100.0, wideband_lambda=1.3)] * 10)
    assert all(a == [] for a in out)


def test_lean_alarm_only_with_wideband_and_load():
    eng = al.AlarmEngine(RULES, CHANNELS_WB)
    assert "lean" in eng.rules and al.afr_available(CHANNELS_WB)
    assert run(eng, [base(tps=20.0, wideband_lambda=1.2)] * 6)[-1] == []      # gated on tps
    out = run(eng, [base(tps=100.0, wideband_lambda=1.2)] * 5)
    assert out[-1][0]["id"] == "lean" and out[-1][0]["level"] == "critical"


def test_stock_narrowband_never_drives_alarms():
    eng = al.AlarmEngine(RULES, CHANNELS_WB)
    assert all(r["channel"] != "Lambda" for r in eng.rules.values())


# --- rate rule ------------------------------------------------------------------

def test_knock_count_rate_rule():
    eng = al.AlarmEngine({"knock_count": RULES["knock_count"]})
    # 100 ms samples; count steady -> nothing
    assert run(eng, [base(knock_count=5)] * 20)[-1] == []
    # one knock within the window -> warn
    out = run(eng, [base(knock_count=6)] * 3, t0=2.0)
    assert out[-1][0]["level"] == "warn"
    # warn self-clears once the increase falls out of the 5 s window
    assert run(eng, [base(knock_count=6)], t0=8.0)[0] == []
    # +3 per window sustained for 10 s -> critical
    samples = [base(knock_count=10 + i) for i in range(1, 40)]
    out = run(eng, samples, t0=20.0, dt=1.0)
    assert out[5][0]["level"] == "warn"           # rate high but not yet sustained
    assert out[-1][0]["level"] == "critical"
    assert out[-1][0]["latched"] is True
