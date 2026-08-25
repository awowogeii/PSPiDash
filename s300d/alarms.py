"""Derived gauge values, shift light, and the alarm engine.

Everything here is evaluated in the daemon so alarms survive a UI reload and
are unit-testable. ``derive()`` is the single point where degF becomes degC and
where boost is computed from MAP minus barometric pressure (channel 0x0170).
Downstream code must never convert temperatures again.
"""
import logging
from collections import deque

log = logging.getLogger("s300d.alarms")

WIDEBAND_LAMBDA_ID = 0x0329
VTEC_ID = 0x0200
KPA_TO_PSI = 0.145038

DEFAULT_SHIFT_LIGHT = {"amber": 7400, "red": 7900, "flash": 8100}

# Channel name -> public gauge key, in channel-id order. Every decodable
# channel is published (None when the ECU's channel list lacks it) EXCEPT
# narrowband lambda (0x0320 Lambda / 0x0321 CorrectedLambda): AFR display
# only exists via 0x0329 WidebandLambda, and that key is added separately
# and only when afr_available.
CHANNEL_KEYS = (
    ("RPM", "rpm"), ("Speed", "speed_kph"), ("Gear", "gear"),
    ("MAP", "map_kpa"), ("MAPVoltage", "map_v"), ("TPS", "tps"),
    ("InjectorDuration", "inj_ms"), ("InjectorDuty", "inj_duty"),
    ("IgnitionAdvance", "ign_adv"), ("IgnitionDwell", "ign_dwell"),
    ("IAT", "iat_c"), ("ECT", "ect_c"), ("BarometricPressure", "baro_kpa"),
    ("BatteryVoltage", "vbat"), ("VtecPressure", "vtec_oil"),
    ("TargetLambda", "target_lambda"), ("WidebandVoltage", "wideband_v"),
    ("KnockLevel", "knock_level"), ("KnockThreshold", "knock_threshold"),
    ("KnockRetard", "knock_retard"), ("KnockCount", "knock_count"),
    ("RevLimiter", "rev_limiter"), ("IgnitionCut", "ignition_cut"),
    ("BoostCut", "boost_cut"), ("LaunchCut", "launch_cut"),
    ("ShiftCut", "shift_cut"), ("BoostControlDuty", "boost_duty"),
    ("AnalogInput1", "analog1"), ("AnalogInput2", "analog2"),
)

# The exact key set of the WS "d" object; "wideband_lambda" is appended only
# when afr_available.
PUBLIC_KEYS = tuple(k for _, k in CHANNEL_KEYS) + ("boost_psi", "vtec", "shift_stage")


def f_to_c(deg_f):
    return None if deg_f is None else (deg_f - 32.0) * 5.0 / 9.0


def shift_stage(rpm, stages=None):
    """0 = off, 1 = amber, 2 = red, 3 = flash."""
    s = stages or DEFAULT_SHIFT_LIGHT
    if rpm is None:
        return 0
    if rpm >= s["flash"]:
        return 3
    if rpm >= s["red"]:
        return 2
    if rpm >= s["amber"]:
        return 1
    return 0


def afr_available(channel_list):
    return any(cid == WIDEBAND_LAMBDA_ID for cid, _ in (channel_list or ()))


def derive(values, channel_list=None, stages=None):
    """Decoded channel dict -> gauge dict (the ``d`` object in the WS message).

    Temperatures are converted degF -> degC here and nowhere else.
    """
    d = {key: values.get(name) for name, key in CHANNEL_KEYS}
    d["ect_c"] = f_to_c(d["ect_c"])
    d["iat_c"] = f_to_c(d["iat_c"])
    map_kpa, baro = d["map_kpa"], d["baro_kpa"]
    d["boost_psi"] = (map_kpa - baro) * KPA_TO_PSI \
        if map_kpa is not None and baro is not None else None
    d["vtec"] = bool(values.get("VtecSpool", False))
    d["shift_stage"] = shift_stage(d["rpm"], stages)
    if afr_available(channel_list):
        d["wideband_lambda"] = values.get("WidebandLambda")
    return d


# --- alarm engine -----------------------------------------------------------

class _RuleState:
    __slots__ = ("level", "since", "value", "latched", "warn_run", "crit_run",
                 "history", "crit_since")

    def __init__(self):
        self.level = None      # None | "warn" | "critical"
        self.since = None
        self.value = None
        self.latched = False
        self.warn_run = 0
        self.crit_run = 0
        self.history = deque()  # (t, value) for rate rules
        self.crit_since = None


def _beyond(value, threshold, direction, inclusive=False):
    if direction == "below":
        return value <= threshold if inclusive else value < threshold
    return value >= threshold if inclusive else value > threshold


def _within_clear(value, clear, direction):
    return value >= clear if direction == "below" else value <= clear


class AlarmEngine:
    """Config-driven alarm evaluation with deadband, debounce and latching."""

    def __init__(self, rules, channel_list=None):
        self.rules = {}
        self.states = {}
        have_wideband = afr_available(channel_list)
        for rule_id, rule in (rules or {}).items():
            rule = dict(rule)
            if not rule.get("enabled", True):
                continue
            if rule.get("channel") == "wideband_lambda" and not have_wideband:
                log.info("alarm %s not registered: no wideband (0x0329) in channel list", rule_id)
                continue
            rule.setdefault("samples", 5)
            rule.setdefault("direction", "above")
            rule.setdefault("type", "threshold")
            self.rules[rule_id] = rule
            self.states[rule_id] = _RuleState()

    def ack(self):
        """Clear latched criticals whose value is back inside the clear band."""
        for rule_id, st in self.states.items():
            if st.latched:
                st.latched = False
                if st.level == "critical" and st.value is not None and \
                        _within_clear(st.value, self.rules[rule_id]["clear"],
                                      self.rules[rule_id]["direction"]):
                    self._clear(st)

    @staticmethod
    def _clear(st):
        st.level = None
        st.since = None
        st.warn_run = st.crit_run = 0
        st.crit_since = None

    def _gated(self, rule, d):
        if "rpm_min" in rule and (d.get("rpm") is None or d["rpm"] <= rule["rpm_min"]):
            return True
        if "tps_min" in rule and (d.get("tps") is None or d["tps"] < rule["tps_min"]):
            return True
        return False

    def _metric(self, rule, st, d, t):
        """Value the thresholds apply to; for rate rules the increase over the window."""
        raw = d.get(rule["channel"])
        if raw is None:
            return None
        raw = float(raw)
        if rule["type"] != "rate":
            return raw
        window = float(rule.get("window_s", 5))
        st.history.append((t, raw))
        while st.history and t - st.history[0][0] > window:
            st.history.popleft()
        return raw - st.history[0][1]

    def update(self, d, t):
        """Evaluate one gauge dict at monotonic time ``t``; returns active alarms."""
        for rule_id, rule in self.rules.items():
            st = self.states[rule_id]
            value = self._metric(rule, st, d, t)
            if value is None or self._gated(rule, d):
                st.warn_run = st.crit_run = 0
                continue
            st.value = value
            direction = rule["direction"]
            needed = int(rule["samples"])

            incl = rule["type"] == "rate"  # counters: an increase OF warn trips, not beyond it
            crit_hit = _beyond(value, rule["critical"], direction, incl) if "critical" in rule else False
            warn_hit = _beyond(value, rule["warn"], direction, incl) if "warn" in rule else False
            st.crit_run = st.crit_run + 1 if crit_hit else 0
            st.warn_run = st.warn_run + 1 if warn_hit else 0

            if rule["type"] == "rate" and "sustained_s" in rule:
                # critical only when the rate stays above threshold for sustained_s
                if crit_hit:
                    st.crit_since = st.crit_since if st.crit_since is not None else t
                    crit_hit = (t - st.crit_since) >= float(rule["sustained_s"])
                else:
                    st.crit_since = None

            if st.crit_run >= needed and crit_hit and st.level != "critical":
                st.level, st.since, st.latched = "critical", t, True
            elif st.warn_run >= needed and st.level is None:
                st.level, st.since = "warn", t
            elif st.level == "warn" and _within_clear(value, rule["clear"], direction):
                self._clear(st)
            elif st.level == "critical" and not st.latched and \
                    _within_clear(value, rule["clear"], direction):
                self._clear(st)
        return self.active()

    def active(self):
        out = []
        for rule_id, st in self.states.items():
            if st.level is not None:
                out.append({"id": rule_id, "level": st.level, "since": st.since,
                            "value": st.value, "latched": st.latched})
        return out
