"""Map UserVoice pitch/speed multipliers to Edge/Azure SSML prosody strings."""

from __future__ import annotations


def microsoft_rate_string(speed: float) -> str:
    rate_percent = round((speed - 1.0) * 100)
    sign = '' if rate_percent < 0 else '+'
    return f'{sign}{rate_percent}%'


def microsoft_pitch_string(pitch: float) -> str:
    pitch_hz = round((pitch - 1.0) * 100)
    sign = '' if pitch_hz < 0 else '+'
    return f'{sign}{pitch_hz}Hz'
