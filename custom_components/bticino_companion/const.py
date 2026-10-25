"""Constants for the BTicino Companion integration."""

from __future__ import annotations

DOMAIN = "bticino_companion"
NAME = "BTicino Companion"
DATA_CAMERA_ENTITIES = f"{DOMAIN}_camera_entities"
DATA_FRONTEND_REGISTERED = f"{DOMAIN}_frontend_registered"
DATA_PENDING_REAUTH_URLS = f"{DOMAIN}_pending_reauth_urls"
FRONTEND_PATH = "/bticino_companion_static"
CARD_RESOURCE_URL = f"{FRONTEND_PATH}/bticino-go-intercom-card.js?automatically-added"

PLATFORMS: list[str] = [
    "camera",
    "image",
    "sensor",
    "binary_sensor",
    "button",
    "switch",
    "update",
]

CONF_ACCESS_TOKEN = "access_token"
CONF_CLAIM_CODE = "claim_code"
CONF_COMPANION_URL = "companion_url"
CONF_DEVICE_ID = "device_id"
CONF_INSTANCE_ID = "instance_id"
CONF_REPAIR_CODE = "repair_code"
CONF_SSH_USERNAME = "ssh_username"
CONF_SSH_HOST_KEY = "ssh_host_key"
CONF_OWNER_USERNAME = "owner_username"
CONF_OWNER_PASSWORD = "owner_password"
CONF_ACCEPT_NEW_HOST_KEY = "accept_new_host_key"
DEFAULT_SSH_USERNAME = "root"
# The Companion web UI's bootstrap credentials, which an owner may not keep.
DEFAULT_OWNER_CREDENTIAL = "companion"
MIN_OWNER_PASSWORD_LENGTH = 8
MAX_OWNER_PASSWORD_BYTES = 72
DEFAULT_PORT = 8080

API_PATH_PAIR_CHALLENGE = "/api/v3/pair/challenge"
API_PATH_PAIR_CLAIM = "/api/v3/pair/claim"
API_PATH_RECOVER_BEARER = "/api/v3/auth/recover"
API_PATH_AUTH_STATUS = "/api/v3/auth/status"
API_PATH_ENTRYPOINT_UNLOCK = "/api/v3/entrypoints/{entrypoint_id}/unlock"
API_PATH_AUDIO_MUTE = "/api/v3/audio/mute"
API_PATH_AUDIO_UNMUTE = "/api/v3/audio/unmute"
API_PATH_CALL_ANSWER = "/api/v3/call/answer"
API_PATH_CALL_HANGUP = "/api/v3/call/hangup"
API_PATH_VOICEMAIL_ENABLE = "/api/v3/voicemail/enable"
API_PATH_VOICEMAIL_DISABLE = "/api/v3/voicemail/disable"
API_PATH_UPDATE_INSTALL = "/api/v3/system/update/install"
API_PATH_SYSTEM_REBOOT = "/api/v3/system/reboot"
API_PATH_SYSTEM_SERVICE_RESTART = "/api/v3/system/services/{service}/restart"
API_PATH_WEBRTC_WS = "/api/v3/webrtc/ws"
API_PATH_SNAPSHOT_LATEST = "/api/v3/entrypoints/{entrypoint_id}/snapshot/latest.jpg"
API_PATH_SNAPSHOT_RING = "/api/v3/entrypoints/{entrypoint_id}/snapshot/ring.jpg"
WEBSOCKET_PATH = "/api/v3/ws"

WEBSOCKET_PING_INTERVAL_SECONDS = 20
WEBSOCKET_PONG_TIMEOUT_SECONDS = 90
WEBSOCKET_RECONNECT_MAX_SECONDS = 60
WEBSOCKET_CONNECT_TIMEOUT_SECONDS = 10
WEBSOCKET_CLOSE_TIMEOUT_SECONDS = 5
WEBSOCKET_EXPECTED_DISCONNECT_COOLDOWN_SECONDS = 5
WEBSOCKET_STABLE_CONNECTION_SECONDS = 5
