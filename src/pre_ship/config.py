from copy import deepcopy
import os
from pathlib import Path
import re
import tomllib

DEFAULTS = {
    'agent_markers': ['CLAUDECODE', 'AI_AGENT', 'CLAUDE_CODE_SESSION_ID', 'LODY_SESSION_ID', 'CODEX_SESSION_ID', 'CODEX_THREAD_ID'],
    'exemption_reply': 'EXEMPT',
    'gitleaks': {'mode': 'optional'},
    'push': {'protected_branches': ['main', 'master'], 'allow_protected': False},
    'review': {'command': []},
}


def config_home():
    configured = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config').expanduser()
    return configured if configured.is_absolute() else Path.home() / '.config'


def config_path():
    return config_home() / 'pre-ship/config.toml'


def load_config():
    path = config_path()
    try:
        with path.open('rb') as source:
            data = tomllib.load(source)
    except FileNotFoundError:
        return deepcopy(DEFAULTS)
    except (OSError, ValueError):
        raise ValueError(f'Cannot read pre-ship configuration at {path}. Repair the TOML file.') from None
    result = deepcopy(DEFAULTS)
    for key, value in data.items():
        if key not in result:
            raise ValueError('Unknown pre-ship configuration key. Use the configuration reference in README.md.')
        if isinstance(result[key], dict):
            if not isinstance(value, dict) or set(value) - set(result[key]):
                raise ValueError('Invalid pre-ship configuration table. Use the configuration reference in README.md.')
            result[key].update(value)
        else:
            result[key] = value
    markers = result['agent_markers']
    if not isinstance(markers, list) or not all(isinstance(item, str) and re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', item) for item in markers):
        raise ValueError('agent_markers must be an array of environment variable names.')
    reply = result['exemption_reply']
    if not isinstance(reply, str) or not reply.strip() or len(reply) > 100:
        raise ValueError('exemption_reply must contain 1 to 100 characters, including a non-space character.')
    if result['gitleaks']['mode'] not in ('off', 'optional', 'required'):
        raise ValueError('gitleaks.mode must be off, optional, or required.')
    push = result['push']
    if type(push['allow_protected']) is not bool or not isinstance(push['protected_branches'], list) or not all(isinstance(item, str) and item and not item.startswith('refs/') for item in push['protected_branches']):
        raise ValueError('push requires a Boolean allow_protected and an array of short protected_branches names.')
    command = result['review']['command']
    if not isinstance(command, list) or not all(isinstance(item, str) and item and '\0' not in item for item in command):
        raise ValueError('review.command must be an array of nonempty command arguments.')
    return result
