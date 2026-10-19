
# from threading import Lock

# _lock = Lock()

# _controls = {
#     "enabled": True,
#     "level": "balanced",
# }

# LEVEL_THRESHOLDS = {
#     "lenient": (0.40, 0.85),
#     "balanced": (0.50, 0.50),
#     "strict": (0.20, 0.40),
# }


# def get_controls():
#     with _lock:
#         return _controls.copy()


# def update_controls(enabled=None, level=None):
#     with _lock:
#         if enabled is not None:
#             _controls["enabled"] = enabled

#         if level is not None:
#             if level not in LEVEL_THRESHOLDS:
#                 raise ValueError("Invalid moderation level")
#             _controls["level"] = level

#         return _controls.copy()


from threading import Lock

from app.routing import Thresholds

_lock = Lock()

_controls = {
    "enabled": True,
    "level": "balanced",
}

# Higher strictness means harmful content is rejected at a lower risk score.
LEVEL_THRESHOLDS = {
    "lenient": Thresholds(allow_max=0.70, reject_min=0.90),
    "balanced": Thresholds(allow_max=0.50, reject_min=0.50),
    "strict": Thresholds(allow_max=0.20, reject_min=0.40),
}


def get_controls():
    with _lock:
        return {
            **_controls,
            "thresholds": LEVEL_THRESHOLDS[_controls["level"]].as_dict(),
        }


def get_active_thresholds():
    with _lock:
        return LEVEL_THRESHOLDS[_controls["level"]]


def update_controls(enabled=None, level=None):
    with _lock:
        if enabled is not None:
            if not isinstance(enabled, bool):
                raise ValueError("'enabled' must be true or false")
            _controls["enabled"] = enabled

        if level is not None:
            if level not in LEVEL_THRESHOLDS:
                raise ValueError("Invalid moderation level")
            _controls["level"] = level

        return {
            **_controls,
            "thresholds": LEVEL_THRESHOLDS[_controls["level"]].as_dict(),
        }
