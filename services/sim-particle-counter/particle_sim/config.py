"""Configuration: environment variables, then CLI flags on top.

The variable names and defaults come straight from
`docs/reference/particle_counter_sim.md` § Configuration. Two of them are
overridden in `docker-compose.yml` rather than here, and the override is the
point:

  * ``DURATION`` is 10 in compose, not the vendor's 60. Ten seconds is a
    cadence an audience can watch; sixty is a coffee break.
  * ``SEED_SAMPLES`` is 0 in compose, not the vendor's 50. Nothing exists until
    somebody presses Start on the touchscreen -- see the panel's idle banner.
    Fifty pre-generated records would put readings on the backbone that nobody
    in the room saw the instrument take.

Everything not in the vendor table (``PANEL_PORT``, ``BUFFER_MAX``,
``DIRTY_MULTIPLIER``, the JWT knobs) is ours and is named so in the table below.
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass, field


def _env(name: str, default: str) -> str:
    value = os.environ.get(name)
    return default if value is None or value == "" else value


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except ValueError:
        return default


def _channels(raw: str) -> list:
    out = []
    for piece in raw.split(","):
        piece = piece.strip()
        if not piece:
            continue
        try:
            out.append(float(piece))
        except ValueError:
            continue
    return out or [0.3, 0.5, 1.0, 3.0, 5.0, 10.0]


@dataclass
class Config:
    # ---- the vendor's own table
    port: int = 8443                      # PORT          HTTPS port
    seed_samples: int = 50                # SEED_SAMPLES  pre-generated records
    device_id: str = "SIM-001"            # DEVICE_ID     serial number in samples
    device_name: str = "Simulator"        # DEVICE_NAME   device name in samples
    duration: int = 60                    # DURATION      seconds per sample
    channels: list = field(                # CHANNELS      micron sizes
        default_factory=lambda: [0.3, 0.5, 1.0, 3.0, 5.0, 10.0])

    # ---- ours, and not part of the vendor surface
    panel_port: int = 8089                # the operator touchscreen
    config_dir: str = "/config"           # sample point + run state survive a restart
    cert_dir: str = "/tmp/particle-sim"   # self-signed cert, regenerated every start
    buffer_max: int = 500                 # rolling buffer cap; ~83 min at 10 s
    flow_rate_lpm: float = 28.3           # 1 CFM, the instrument's rated flow
    dirty_multiplier: float = 25.0        # what the "dirty room" button multiplies by
    username: str = "admin"
    password: str = "password"
    operator_name: str = "Admin User"
    operator_role: str = "ADMIN"
    jwt_secret: str = "particle-sim-secret"
    # Five minutes on purpose. The poll re-authenticates on 401 rather than
    # tracking expiry, and a token that never expires makes that path dead code
    # that fails in a year. See docs/plans/06-poll-particle-counter.md § Auth.
    token_ttl_s: int = 300
    log_level: str = "INFO"

    @property
    def sample_volume_l(self) -> float:
        """Litres drawn per analysis. 28.3 LPM for 10 s is 4.717 L.

        Every raw count in this simulator means something only against this
        number -- which is why the Ignition-side threshold records the duration
        it was chosen for.
        """
        return self.flow_rate_lpm * self.duration / 60.0


def from_env() -> Config:
    return Config(
        port=_env_int("PORT", 8443),
        seed_samples=_env_int("SEED_SAMPLES", 50),
        device_id=_env("DEVICE_ID", "SIM-001"),
        device_name=_env("DEVICE_NAME", "Simulator"),
        duration=_env_int("DURATION", 60),
        channels=_channels(_env("CHANNELS", "0.3,0.5,1,3,5,10")),
        panel_port=_env_int("PANEL_PORT", 8089),
        config_dir=_env("CONFIG_DIR", "/config"),
        cert_dir=_env("CERT_DIR", "/tmp/particle-sim"),
        buffer_max=_env_int("BUFFER_MAX", 500),
        flow_rate_lpm=_env_float("FLOW_RATE_LPM", 28.3),
        dirty_multiplier=_env_float("DIRTY_MULTIPLIER", 25.0),
        username=_env("API_USERNAME", "admin"),
        password=_env("API_PASSWORD", "password"),
        operator_name=_env("OPERATOR_NAME", "Admin User"),
        operator_role=_env("OPERATOR_ROLE", "ADMIN"),
        jwt_secret=_env("JWT_SECRET", "particle-sim-secret"),
        token_ttl_s=_env_int("TOKEN_TTL_S", 300),
        log_level=_env("LOG_LEVEL", "INFO").upper(),
    )


def from_args(argv=None) -> Config:
    """Environment first, CLI flags on top.

    `python -m particle_sim --port 8443` is the vendor README's quickstart and
    has to keep working: a README that lies about its own quickstart is a bug in
    the transcription.
    """
    cfg = from_env()
    parser = argparse.ArgumentParser(
        prog="particle_sim",
        description="particle counter API simulator (GraphQL over HTTPS)")
    parser.add_argument("--port", type=int, default=cfg.port)
    parser.add_argument("--panel-port", type=int, default=cfg.panel_port)
    parser.add_argument("--device-id", default=cfg.device_id)
    parser.add_argument("--device-name", default=cfg.device_name)
    parser.add_argument("--duration", type=int, default=cfg.duration)
    parser.add_argument("--channels", default=",".join(str(c) for c in cfg.channels))
    parser.add_argument("--seed-samples", type=int, default=cfg.seed_samples)
    parser.add_argument("--config-dir", default=cfg.config_dir)
    parser.add_argument("--log-level", default=cfg.log_level)
    args = parser.parse_args(argv)

    cfg.port = args.port
    cfg.panel_port = args.panel_port
    cfg.device_id = args.device_id
    cfg.device_name = args.device_name
    cfg.duration = args.duration
    cfg.channels = _channels(args.channels)
    cfg.seed_samples = args.seed_samples
    cfg.config_dir = args.config_dir
    cfg.log_level = args.log_level.upper()
    return cfg
